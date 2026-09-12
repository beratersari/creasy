"""Creasy thread matching: HTML mark, Turkish headers, escaped mark."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.webhook import router as webhook_router
from creasy.gitlab.client import GitLabClient
from creasy.jobs.manager import Manager
from creasy.jobs.worker import OpenCodeRunner
from creasy.opencode.session import looks_like_review
from creasy.review.position import CREASY_FINDING_MARK, format_discussion
from creasy.review.findings import Finding
from creasy.review.threads import is_creasy_finding_body, parse_creasy_thread
from creasy.workspace.store import WorkspaceStore

STUB = Path(__file__).resolve().parent / "support" / "opencode_serve_stub.py"

REVIEW = """### Özet
1 Kritik.

```opencoderman-findings
{"findings": [{"path": "src/app.py", "start_line": 2, "end_line": 2, "side": "new", "severity": "critical", "title": "stack buffer overflow", "body": "Unbounded copy into dest."}]}
```
"""


def test_kritik_header_is_a_creasy_thread_without_the_html_mark() -> None:
    posted = format_discussion(
        Finding(
            path="src/app.py",
            start_line=2,
            end_line=2,
            side="new",
            severity="critical",
            title="stack buffer overflow",
            body="Unbounded copy.",
        )
    )
    assert CREASY_FINDING_MARK in posted
    assert "**Kritik**" in posted
    assert is_creasy_finding_body(posted) is True
    stripped = posted.replace(CREASY_FINDING_MARK, "").strip()
    assert stripped.startswith("**Kritik**")
    assert is_creasy_finding_body(stripped) is True
    assert is_creasy_finding_body("**Önemli** · other") is True
    assert is_creasy_finding_body("**Critical** overflow") is True
    escaped = posted.replace("<", "&lt;").replace(">", "&gt;")
    assert is_creasy_finding_body(escaped) is True
    raw = {
        "id": "disc_1",
        "notes": [
            {
                "id": 1,
                "body": stripped,
                "author": {"id": 99},
                "system": False,
                "position": {"new_path": "src/app.py", "old_path": "src/app.py", "new_line": 2},
                "resolved": False,
            }
        ],
    }
    assert parse_creasy_thread(raw) is not None


def test_looks_like_review_misses_turkish_ozet_without_fence() -> None:
    turkish = "### Özet\n1 Kritik.\n\n#### 1. src/app.py:2 — overflow\n"
    assert looks_like_review(turkish) is False
    assert looks_like_review("### Summary\nLooks fine.") is True
    assert looks_like_review("#### 1. `src/app.py:2` — overflow") is True
    assert looks_like_review(REVIEW) is True


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return (result.stdout or "").strip()


def _origin(root: Path) -> tuple[Path, str, str]:
    repo = root / "origin"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "checkout", "-B", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "serve").write_text(STUB.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "assistants.json").write_text(json.dumps([REVIEW, REVIEW]), encoding="utf-8")
    _git(repo, "add", "src/app.py", "serve", "assistants.json")
    _git(repo, "commit", "-m", "init")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "feat")
    (repo / "src" / "app.py").write_text("dest = [0] * 8\nstrcpy(dest, src)\n", encoding="utf-8")
    _git(repo, "add", "src/app.py")
    _git(repo, "commit", "-m", "overflow")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    return repo, head, base


class _State:
    def __init__(self, mr: dict[str, Any], *, strip_mark: bool) -> None:
        self.lock = threading.Lock()
        self.mr = mr
        self.notes: list[dict[str, Any]] = []
        self.discussions: list[dict[str, Any]] = []
        self.note_id = 10
        self.disc_id = 1
        self.reviewer_state = "unreviewed"
        self.strip_mark = strip_mark


def _server(state: _State) -> ThreadingHTTPServer:
    holder = {"s": state}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

        def _json(self, payload, status: int = 200) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            st = holder["s"]
            if path == "/api/v4/user":
                self._json({"id": 99, "username": "creasy-bot"})
                return
            if path.endswith("/merge_requests/7") and "/notes" not in path and "/discussions" not in path:
                self._json(dict(st.mr))
                return
            if path.endswith("/discussions"):
                with st.lock:
                    self._json(list(st.discussions))
                return
            if path.endswith("/notes"):
                with st.lock:
                    self._json(list(st.notes))
                return
            if path.endswith("/pipelines"):
                self._json([])
                return
            if path.endswith("/reviewers"):
                self._json([{"user": {"id": 99, "username": "creasy"}, "state": st.reviewer_state}])
                return
            self._json({}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            body = self._read_json()
            st = holder["s"]
            with st.lock:
                if path.endswith("/bulk_publish"):
                    st.reviewer_state = "reviewed"
                    self.send_response(204)
                    self.end_headers()
                    return
                if path.endswith("/notes") and "/discussions/" not in path:
                    st.note_id += 1
                    note = {"id": st.note_id, "body": body.get("body") or ""}
                    st.notes.append(note)
                    self._json(note)
                    return
                if "/discussions/" in path and path.endswith("/notes"):
                    st.note_id += 1
                    self._json({"id": st.note_id, "body": body.get("body") or ""})
                    return
                if path.endswith("/discussions"):
                    st.disc_id += 1
                    posted = str(body.get("body") or "")
                    if st.strip_mark:
                        posted = posted.replace(CREASY_FINDING_MARK, "").strip()
                    disc = {
                        "id": f"disc_{st.disc_id}",
                        "individual_note": False,
                        "notes": [
                            {
                                "id": st.note_id,
                                "body": posted,
                                "author": {"id": 99},
                                "system": False,
                                "position": body.get("position") or {},
                                "resolved": False,
                            }
                        ],
                    }
                    st.discussions.append(disc)
                    self._json(disc)
                    return
            self._json({}, status=404)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def _assign():
    return {
        "object_kind": "merge_request",
        "user": {"id": 1, "username": "dev"},
        "object_attributes": {
            "action": "update",
            "iid": 7,
            "target_project_id": 42,
            "source_branch": "feat",
            "target_branch": "main",
            "title": "overflow",
            "url": "http://gl/mr/7",
            "draft": False,
        },
        "changes": {"reviewers": {"previous": [], "current": [{"id": 99, "username": "creasy"}]}},
    }


def _note_review():
    return {
        "object_kind": "note",
        "user": {"id": 1, "username": "dev"},
        "object_attributes": {
            "noteable_type": "MergeRequest",
            "note": "@creasy /review",
            "id": 501,
        },
        "merge_request": {
            "iid": 7,
            "target_project_id": 42,
            "source_branch": "feat",
            "target_branch": "main",
            "title": "overflow",
            "url": "http://gl/mr/7",
            "draft": False,
        },
    }


def _boot(tmp_config, tmp_path: Path, *, strip_mark: bool):
    origin, head, base = _origin(tmp_path / "src")
    mr = {
        "id": 7,
        "iid": 7,
        "title": "overflow",
        "description": "",
        "author": {"username": "dev"},
        "source_branch": "feat",
        "target_branch": "main",
        "sha": head,
        "web_url": "http://gl/mr/7",
        "draft": False,
        "state": "opened",
        "labels": [],
        "source": {"http_url_to_repo": origin.as_uri()},
        "target_project_id": 42,
        "diff_refs": {"base_sha": base, "start_sha": base, "head_sha": head},
    }
    state = _State(mr, strip_mark=strip_mark)
    httpd = _server(state)
    host, port = httpd.server_address[:2]
    tmp_config.gitlab_url = f"http://{host}:{port}"
    tmp_config.gitlab_token = ""
    tmp_config.opencode_bin = sys.executable
    tmp_config.opencode_timeout = 20
    tmp_config.hang_timeout = 15
    tmp_config.serve_health_timeout = 15
    tmp_config.opencode_retry_count = 1
    tmp_config.review_mention = "creasy"
    gitlab = GitLabClient(tmp_config.gitlab_url, "tok")
    workspaces = WorkspaceStore(tmp_config.data_dir / "workspace_meta")
    runner = OpenCodeRunner(tmp_config, workspaces, gitlab)
    manager = Manager(tmp_config, runner, workspaces=workspaces)
    manager.boot()
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.state.gitlab = gitlab
    app.state.bot_user_id = 99
    app.include_router(webhook_router)
    return TestClient(app), manager, state, httpd


def _wait(manager: Manager, job_id: str):
    deadline = time.time() + 40
    last = None
    while time.time() < deadline:
        last = manager.store.get(job_id)
        if last and last.status not in {"queued", "running"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job stuck: {last}")


def _shutdown(manager, httpd):
    try:
        manager.shutdown()
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_second_review_reuses_thread_when_kritik_mark_is_stripped(tmp_config, tmp_path: Path):
    client, manager, state, httpd = _boot(tmp_config, tmp_path, strip_mark=True)
    try:
        first = client.post("/creasy/webhook/gitlab", json=_assign(), headers={"X-Gitlab-Token": "secret"})
        job1 = _wait(manager, first.json()["job_id"])
        assert job1.status == "success", job1.error_message
        assert len(state.discussions) == 1
        body = state.discussions[0]["notes"][0]["body"]
        assert "**Kritik**" in body
        assert CREASY_FINDING_MARK not in body

        second = client.post(
            "/creasy/webhook/gitlab", json=_note_review(), headers={"X-Gitlab-Token": "secret"}
        )
        job2 = _wait(manager, second.json()["job_id"])
        assert job2.status == "success", job2.error_message
        assert len(state.discussions) == 1
    finally:
        _shutdown(manager, httpd)


def test_second_review_reuses_thread_when_html_mark_is_kept(tmp_config, tmp_path: Path):
    client, manager, state, httpd = _boot(tmp_config, tmp_path, strip_mark=False)
    try:
        first = client.post("/creasy/webhook/gitlab", json=_assign(), headers={"X-Gitlab-Token": "secret"})
        job1 = _wait(manager, first.json()["job_id"])
        assert job1.status == "success", job1.error_message
        assert len(state.discussions) == 1
        assert CREASY_FINDING_MARK in state.discussions[0]["notes"][0]["body"]

        second = client.post(
            "/creasy/webhook/gitlab", json=_note_review(), headers={"X-Gitlab-Token": "secret"}
        )
        job2 = _wait(manager, second.json()["job_id"])
        assert job2.status == "success", job2.error_message
        assert len(state.discussions) == 1
    finally:
        _shutdown(manager, httpd)
