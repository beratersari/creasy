"""GitLab GET after rebase: wait until sha moves off the in-progress commit.

Reproduces beratersari0/test_project!72 poll[1]: rebase_in_progress=false
while sha / diff_refs were still pre-rebase.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

import creasy.gitlab.client as gitlab_client
from creasy.gitlab.client import GitLabClient
from creasy.jobs.manager import Manager
from creasy.jobs.worker import OpenCodeRunner
from creasy.workspace.store import WorkspaceStore

STUB = Path(__file__).resolve().parent / "support" / "opencode_serve_stub.py"

REVIEW = """### Summary
1 Critical.

```opencoderman-findings
{"findings": [{"path": "src/app.py", "start_line": 2, "end_line": 2, "side": "new", "severity": "critical", "title": "overflow", "body": "unbounded copy"}]}
```
"""


def _payload(sha: str, base: str, *, rebasing: bool = False) -> dict[str, Any]:
    return {
        "iid": 72,
        "title": "stale rebase",
        "description": "",
        "author": {"username": "dev"},
        "source_branch": "feat",
        "target_branch": "main",
        "sha": sha,
        "diff_refs": {"base_sha": base, "start_sha": base, "head_sha": sha},
        "source": {"http_url_to_repo": "http://example/repo.git"},
        "web_url": "http://gl/mr/72",
        "draft": False,
        "state": "opened",
        "labels": [],
        "target_project_id": 1,
        "rebase_in_progress": rebasing,
    }


def _client(handler) -> GitLabClient:
    client = GitLabClient("https://gitlab.example", "tok")
    client._http.close()
    client._http = httpx.Client(
        base_url="https://gitlab.example/api/v4",
        headers={"PRIVATE-TOKEN": "tok"},
        transport=httpx.MockTransport(handler),
    )
    return client


def test_get_mr_without_rebase_is_a_single_request() -> None:
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pipelines"):
            return httpx.Response(200, json=[])
        hits["n"] += 1
        assert request.url.params.get("include_rebase_in_progress") == "true"
        return httpx.Response(200, json=_payload("newsha", "newbase", rebasing=False))

    client = _client(handler)
    try:
        mr = client.get_merge_request(1, 72)
        assert mr.sha == "newsha"
        assert mr.base_sha == "newbase"
        assert hits["n"] == 1
    finally:
        client.close()


def test_get_mr_waits_through_false_done_then_uses_new_sha(monkeypatch) -> None:
    """Exact !72 sequence: in progress → done+old sha → done+new sha."""
    monkeypatch.setattr(gitlab_client, "REBASE_SETTLE_INTERVAL", 0.01)
    monkeypatch.setattr(gitlab_client, "REBASE_SETTLE_TIMEOUT", 2.0)
    queue = [
        _payload("oldsha", "oldbase", rebasing=True),
        _payload("oldsha", "oldbase", rebasing=False),
        _payload("newsha", "newbase", rebasing=False),
    ]
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pipelines"):
            return httpx.Response(200, json=[])
        hits["n"] += 1
        body = queue.pop(0) if queue else _payload("newsha", "newbase")
        return httpx.Response(200, json=body)

    client = _client(handler)
    try:
        mr = client.get_merge_request(1, 72)
        assert mr.sha == "newsha"
        assert mr.base_sha == "newbase"
        assert mr.start_sha == "newbase"
        assert hits["n"] == 3
        assert queue == []
    finally:
        client.close()


def test_get_mr_times_out_if_rebase_never_updates_sha(monkeypatch) -> None:
    monkeypatch.setattr(gitlab_client, "REBASE_SETTLE_INTERVAL", 0.01)
    monkeypatch.setattr(gitlab_client, "REBASE_SETTLE_TIMEOUT", 0.05)
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pipelines"):
            return httpx.Response(200, json=[])
        hits["n"] += 1
        return httpx.Response(200, json=_payload("oldsha", "oldbase", rebasing=hits["n"] == 1))

    client = _client(handler)
    try:
        mr = client.get_merge_request(1, 72)
        assert mr.sha == "oldsha"
        assert hits["n"] >= 2
    finally:
        client.close()


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return (result.stdout or "").strip()


def _repo_with_rebased_feat(root: Path) -> tuple[Path, str, str, str]:
    repo = root / "origin"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "checkout", "-B", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "serve").write_text(STUB.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "assistants.json").write_text(json.dumps([REVIEW]), encoding="utf-8")
    _git(repo, "add", "src/app.py", "serve", "assistants.json")
    _git(repo, "commit", "-m", "init")
    _git(repo, "checkout", "-b", "feat")
    (repo / "src" / "app.py").write_text("dest = [0] * 8\nstrcpy(dest, src)\n", encoding="utf-8")
    _git(repo, "add", "src/app.py")
    _git(repo, "commit", "-m", "overflow")
    old = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    (repo / "later.txt").write_text("target only\n", encoding="utf-8")
    _git(repo, "add", "later.txt")
    _git(repo, "commit", "-m", "target moved")
    live = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "feat")
    _git(repo, "rebase", "main")
    new = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    return repo, old, new, live


class _State:
    def __init__(self, mr: dict[str, Any], sequence: list[dict[str, Any]]) -> None:
        self.lock = threading.Lock()
        self.sequence = list(sequence)
        self.mr = mr
        self.notes: list[dict[str, Any]] = []
        self.discussions: list[dict[str, Any]] = []
        self.note_id = 1
        self.disc_id = 1
        self.reviewer_state = "unreviewed"
        self.gets = 0


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
            parsed = urlparse(self.path)
            path = parsed.path
            st = holder["s"]
            if path == "/api/v4/user":
                self._json({"id": 99, "username": "creasy-bot"})
                return
            if path.endswith("/merge_requests/72") and "/notes" not in path and "/discussions" not in path:
                qs = parse_qs(parsed.query)
                assert qs.get("include_rebase_in_progress") == ["true"]
                with st.lock:
                    st.gets += 1
                    if st.sequence:
                        st.mr = st.sequence.pop(0)
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
                self._json([{"user": {"id": 99}, "state": st.reviewer_state}])
                return
            self._json({"error": "not found"}, status=404)

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
                if path.endswith("/discussions"):
                    st.disc_id += 1
                    disc = {
                        "id": f"d{st.disc_id}",
                        "notes": [{"id": 1, "body": body.get("body") or "", "position": body.get("position") or {}}],
                    }
                    st.discussions.append(disc)
                    self._json(disc)
                    return
            self._json({}, status=404)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_worker_reviews_rebased_sha_not_the_stale_get(tmp_config, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(gitlab_client, "REBASE_SETTLE_INTERVAL", 0.01)
    monkeypatch.setattr(gitlab_client, "REBASE_SETTLE_TIMEOUT", 2.0)
    origin, old, new, live = _repo_with_rebased_feat(tmp_path / "src")
    assert old != new
    stale_base = _git(origin, "rev-parse", f"{old}^")
    first = {
        "iid": 72,
        "title": "Add overflow",
        "description": "",
        "author": {"username": "dev"},
        "source_branch": "feat",
        "target_branch": "main",
        "sha": old,
        "diff_refs": {"base_sha": stale_base, "start_sha": stale_base, "head_sha": old},
        "source": {"http_url_to_repo": origin.as_uri()},
        "web_url": "http://gl/mr/72",
        "draft": False,
        "state": "opened",
        "labels": [],
        "target_project_id": 42,
        "rebase_in_progress": True,
    }
    done_old = dict(first)
    done_old["rebase_in_progress"] = False
    done_old["diff_refs"] = dict(first["diff_refs"])
    done_new = dict(first)
    done_new["rebase_in_progress"] = False
    done_new["sha"] = new
    done_new["diff_refs"] = {"base_sha": live, "start_sha": live, "head_sha": new}
    state = _State(first, [first, done_old, done_new])
    httpd = _server(state)
    host, port = httpd.server_address[:2]
    tmp_config.gitlab_url = f"http://{host}:{port}"
    tmp_config.gitlab_token = ""
    tmp_config.opencode_bin = sys.executable
    tmp_config.opencode_timeout = 20
    tmp_config.hang_timeout = 15
    tmp_config.serve_health_timeout = 15
    tmp_config.opencode_retry_count = 1
    gitlab = GitLabClient(tmp_config.gitlab_url, "test-token")
    workspaces = WorkspaceStore(tmp_config.data_dir / "workspace_meta")
    runner = OpenCodeRunner(tmp_config, workspaces, gitlab)
    manager = Manager(tmp_config, runner, workspaces=workspaces)
    manager.boot()
    try:
        from creasy.gitlab.events import ReviewTrigger

        ack, job, _ = manager.submit(
            ReviewTrigger(
                kind="review",
                project_id=42,
                mr_iid=72,
                source_branch="feat",
                target_branch="main",
                sha=old,
                explicit=True,
                title="Add overflow",
            )
        )
        assert ack in {"accepted", "queued"}
        deadline = time.time() + 40
        last = None
        while time.time() < deadline:
            last = manager.store.get(job.job_id)
            if last and last.status not in {"queued", "running"}:
                break
            time.sleep(0.05)
        assert last is not None
        assert last.status == "success", last.error_message
        assert last.sha == new
        assert last.merge_base == live
        assert last.sha != old
        assert "later.txt" not in (last.changed_paths or [])
        assert state.gets >= 3
    finally:
        manager.shutdown()
        httpd.shutdown()
        httpd.server_close()
        gitlab.close()
