"""Live merge-base is the review left side. Positions, prompt, and threads
must match that diff — not a stale GitLab ``diff_refs.base_sha``.

Real git + production Manager/OpenCodeRunner + local GitLab HTTP.
OpenCode is the same serve stub Creasy launches as ``opencode serve``.
"""

from __future__ import annotations

import json
import re
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
from creasy.jobs.worker import OpenCodeRunner, RunResult, discussion_sha_attempts
from creasy.workspace.store import WorkspaceStore

STUB = Path(__file__).resolve().parent / "support" / "opencode_serve_stub.py"

# Two findings: one on the MR file, one on a target-only file. After a
# rebase the host must keep the first and drop the second (not in the
# live merge-base...HEAD map). That is how we check OpenCode output is
# applied only to the correct points.
REVIEW_WITH_TARGET_ONLY = """### Summary
1 Critical on the feature change. Do not merge.

### Critical

#### 1. `src/app.py:2` — stack buffer overflow

**Why it is an issue and where**
`src/app.py:2` — unbounded copy into dest.

#### 2. `later.txt:1` — target-only noise

**Why it is an issue and where**
This file is only on main after the branch point.

```opencoderman-findings
{"findings": [
  {"path": "src/app.py", "start_line": 2, "end_line": 2, "side": "new", "severity": "critical", "title": "stack buffer overflow", "body": "Unbounded copy into dest."},
  {"path": "later.txt", "start_line": 1, "end_line": 1, "side": "new", "severity": "major", "title": "target-only noise", "body": "This file is only on main after the branch point."}
]}
```
"""

REVIEW_FEATURE_ONLY = """### Summary
1 Critical.

### Critical

#### 1. `src/app.py:2` — stack buffer overflow

**Why it is an issue and where**
`src/app.py:2` — unbounded copy into dest.

```opencoderman-findings
{"findings": [
  {"path": "src/app.py", "start_line": 2, "end_line": 2, "side": "new", "severity": "critical", "title": "stack buffer overflow", "body": "Unbounded copy into dest."}
]}
```
"""


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return (result.stdout or "").strip()


def _init_rebased_origin(root: Path, assistants: list[str]) -> tuple[Path, dict[str, str]]:
    """main A → feat (overflow) → main + later.txt → rebase feat onto main."""
    repo = root / "origin"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "checkout", "-B", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "serve").write_text(STUB.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "assistants.json").write_text(json.dumps(assistants), encoding="utf-8")
    _git(repo, "add", "src/app.py", "serve", "assistants.json")
    _git(repo, "commit", "-m", "init")
    stale = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "feat")
    (repo / "src" / "app.py").write_text("dest = [0] * 8\nstrcpy(dest, src)\n", encoding="utf-8")
    _git(repo, "add", "src/app.py")
    _git(repo, "commit", "-m", "overflow")
    _git(repo, "checkout", "main")
    (repo / "later.txt").write_text("target only\n", encoding="utf-8")
    _git(repo, "add", "later.txt")
    _git(repo, "commit", "-m", "target moved")
    live = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "feat")
    _git(repo, "rebase", "main")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    return repo, {"head": head, "stale": stale, "live": live}


def _init_unrebased_origin(root: Path, assistants: list[str]) -> tuple[Path, dict[str, str]]:
    """Same history as rebase, but feat is left on the old fork."""
    repo = root / "origin"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "checkout", "-B", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "serve").write_text(STUB.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "assistants.json").write_text(json.dumps(assistants), encoding="utf-8")
    _git(repo, "add", "src/app.py", "serve", "assistants.json")
    _git(repo, "commit", "-m", "init")
    fork = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "feat")
    (repo / "src" / "app.py").write_text("dest = [0] * 8\nstrcpy(dest, src)\n", encoding="utf-8")
    _git(repo, "add", "src/app.py")
    _git(repo, "commit", "-m", "overflow")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    (repo / "later.txt").write_text("target only\n", encoding="utf-8")
    _git(repo, "add", "later.txt")
    _git(repo, "commit", "-m", "target moved")
    target_tip = _git(repo, "rev-parse", "HEAD")
    return repo, {"head": head, "fork": fork, "target_tip": target_tip}


class _GitlabState:
    def __init__(self, mr: dict[str, Any]) -> None:
        self.lock = threading.Lock()
        self.mr = mr
        self.notes: list[dict[str, Any]] = []
        self.discussions: list[dict[str, Any]] = []
        self.note_id = 100
        self.disc_id = 1
        self.reviewer_state = "unreviewed"


def _gitlab_server(state: _GitlabState) -> ThreadingHTTPServer:
    holder: dict[str, _GitlabState] = {"s": state}

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
            if path.endswith("/merge_requests/7"):
                self._json(dict(st.mr))
                return
            if path.endswith("/merge_requests/7/discussions"):
                with st.lock:
                    self._json(list(st.discussions))
                return
            if path.endswith("/merge_requests/7/notes"):
                with st.lock:
                    self._json(list(st.notes))
                return
            if path.endswith("/merge_requests/7/pipelines"):
                self._json([])
                return
            if path.endswith("/merge_requests/7/reviewers"):
                self._json(
                    [{"user": {"id": 99, "username": "creasy-bot"}, "state": st.reviewer_state}]
                )
                return
            self._json({"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            body = self._read_json()
            st = holder["s"]
            with st.lock:
                if path.endswith("/draft_notes/bulk_publish"):
                    if str(body.get("reviewer_state") or "") == "reviewed":
                        st.reviewer_state = "reviewed"
                    self.send_response(204)
                    self.end_headers()
                    return
                if path.endswith("/merge_requests/7/notes") and "/discussions/" not in path:
                    st.note_id += 1
                    note = {"id": st.note_id, "body": body.get("body") or "", "author": {"id": 99}}
                    st.notes.append(note)
                    self._json(note)
                    return
                if path.endswith("/merge_requests/7/discussions"):
                    st.disc_id += 1
                    st.note_id += 1
                    disc = {
                        "id": f"disc_{st.disc_id}",
                        "individual_note": False,
                        "notes": [
                            {
                                "id": st.note_id,
                                "body": body.get("body") or "",
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
            self._json({"error": "not found"}, status=404)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _mr_payload(
    origin: Path,
    head: str,
    *,
    base_sha: str = "",
    start_sha: str = "",
    with_refs: bool = True,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": 7,
        "iid": 7,
        "title": "Add overflow",
        "description": "planted overflow",
        "author": {"username": "dev"},
        "source_branch": "feat",
        "target_branch": "main",
        "sha": head,
        "web_url": "http://gl/group/repo/-/merge_requests/7",
        "draft": False,
        "work_in_progress": False,
        "state": "opened",
        "labels": [],
        "source": {"http_url_to_repo": origin.as_uri()},
        "http_url_to_repo": origin.as_uri(),
        "target_project_id": 42,
    }
    if with_refs:
        data["diff_refs"] = {
            "base_sha": base_sha or "",
            "start_sha": start_sha or base_sha or "",
            "head_sha": head,
        }
    return data


def _boot(
    tmp_config,
    origin: Path,
    shas: dict[str, str],
    *,
    base_sha: str = "",
    start_sha: str = "",
    with_refs: bool = True,
) -> tuple[TestClient, Manager, _GitlabState, ThreadingHTTPServer]:
    head = shas["head"]
    state = _GitlabState(
        _mr_payload(origin, head, base_sha=base_sha, start_sha=start_sha, with_refs=with_refs)
    )
    httpd = _gitlab_server(state)
    host, port = httpd.server_address[:2]
    tmp_config.gitlab_url = f"http://{host}:{port}"
    tmp_config.gitlab_token = ""
    tmp_config.opencode_bin = sys.executable
    tmp_config.opencode_timeout = 20
    tmp_config.hang_timeout = 15
    tmp_config.serve_health_timeout = 15
    tmp_config.opencode_retry_count = 1
    tmp_config.git_timeout = 60
    tmp_config.review_mention = "creasy"
    gitlab = GitLabClient(tmp_config.gitlab_url, "test-token")
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


def _shutdown(manager: Manager, httpd: ThreadingHTTPServer) -> None:
    try:
        manager.shutdown()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _wait_job(manager: Manager, job_id: str, timeout: float = 45):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = manager.store.get(job_id)
        if last and last.status not in {"queued", "running"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {last}")


def _assign_body():
    return {
        "object_kind": "merge_request",
        "user": {"id": 1, "username": "dev"},
        "object_attributes": {
            "action": "update",
            "iid": 7,
            "target_project_id": 42,
            "source_branch": "feat",
            "target_branch": "main",
            "title": "Add overflow",
            "url": "http://gl/group/repo/-/merge_requests/7",
            "draft": False,
        },
        "changes": {
            "reviewers": {
                "previous": [],
                "current": [{"id": 99, "username": "creasy"}],
            }
        },
    }


def _assert_prompt_reviews_live_diff(job, *, merge_base: str, must_have: list[str], must_not: list[str]) -> None:
    prompt = job.prompt or ""
    assert prompt, "worker must persist the OpenCode prompt"
    assert f"Separation point (merge-base): `{merge_base}`" in prompt
    assert f"git diff --stat {merge_base}...HEAD" in prompt
    assert f"git diff {merge_base}...HEAD" in prompt
    assert "Do not assume this prompt contains hunks" in prompt
    assert not re.search(r"^@@ ", prompt, re.MULTILINE), prompt
    assert "diff --git " not in prompt
    for path in must_have:
        assert f"`{path}`" in prompt, prompt
    for path in must_not:
        assert f"`{path}`" not in prompt, prompt
    assert "later.txt" not in (job.changed_paths or [])


def _posted_paths(state: _GitlabState) -> list[str]:
    paths = []
    for disc in state.discussions:
        pos = (disc.get("notes") or [{}])[0].get("position") or {}
        paths.append(str(pos.get("new_path") or pos.get("old_path") or ""))
    return paths


def test_discussion_sha_attempts_prefers_live_merge_base() -> None:
    assert discussion_sha_attempts(RunResult()) == []
    assert discussion_sha_attempts(RunResult(merge_base="live")) == [("live", "live")]
    assert discussion_sha_attempts(RunResult(merge_base="live", base_sha="live", start_sha="live")) == [
        ("live", "live")
    ]
    assert discussion_sha_attempts(RunResult(merge_base="live", base_sha="stale", start_sha="stale")) == [
        ("live", "live"),
        ("stale", "stale"),
    ]
    assert discussion_sha_attempts(RunResult(merge_base="fork", base_sha="fork", start_sha="target")) == [
        ("fork", "fork"),
        ("fork", "target"),
    ]
    assert discussion_sha_attempts(RunResult(merge_base="", base_sha="gl", start_sha="gl")) == [("gl", "gl")]
    assert discussion_sha_attempts(RunResult(base_sha="gl")) == [("gl", "gl")]


def test_rebase_stale_gitlab_sha_prompt_and_thread_use_live_merge_base(tmp_config, tmp_path: Path):
    origin, shas = _init_rebased_origin(tmp_path / "src", [REVIEW_WITH_TARGET_ONLY])
    assert shas["stale"] != shas["live"]
    client, manager, state, httpd = _boot(
        tmp_config, origin, shas, base_sha=shas["stale"], start_sha=shas["stale"]
    )
    try:
        res = client.post(
            "/creasy/webhook/gitlab",
            json=_assign_body(),
            headers={"X-Gitlab-Token": "secret"},
        )
        job = _wait_job(manager, res.json()["job_id"])
        assert job.status == "success", job.error_message
        assert job.merge_base == shas["live"]
        _assert_prompt_reviews_live_diff(
            job,
            merge_base=shas["live"],
            must_have=["src/app.py"],
            must_not=["later.txt"],
        )
        assert "stack buffer overflow" in state.notes[0]["body"]
        assert "opencoderman-findings" not in state.notes[0]["body"]
        paths = _posted_paths(state)
        assert paths == ["src/app.py"]
        pos = state.discussions[0]["notes"][0]["position"]
        assert pos["base_sha"] == shas["live"]
        assert pos["start_sha"] == shas["live"]
        assert pos["head_sha"] == shas["head"]
        assert pos["new_line"] == 2
        assert "target-only" not in state.discussions[0]["notes"][0]["body"]
    finally:
        _shutdown(manager, httpd)


def test_matching_gitlab_sha_still_posts_on_the_feature_line(tmp_config, tmp_path: Path):
    origin, shas = _init_rebased_origin(tmp_path / "src", [REVIEW_FEATURE_ONLY])
    client, manager, state, httpd = _boot(
        tmp_config, origin, shas, base_sha=shas["live"], start_sha=shas["live"]
    )
    try:
        res = client.post(
            "/creasy/webhook/gitlab",
            json=_assign_body(),
            headers={"X-Gitlab-Token": "secret"},
        )
        job = _wait_job(manager, res.json()["job_id"])
        assert job.status == "success", job.error_message
        assert job.merge_base == shas["live"]
        _assert_prompt_reviews_live_diff(
            job,
            merge_base=shas["live"],
            must_have=["src/app.py"],
            must_not=["later.txt"],
        )
        pos = state.discussions[0]["notes"][0]["position"]
        assert pos["base_sha"] == shas["live"]
        assert pos["new_path"] == "src/app.py"
        assert pos["new_line"] == 2
    finally:
        _shutdown(manager, httpd)


def test_empty_diff_refs_still_threads_from_live_merge_base(tmp_config, tmp_path: Path):
    origin, shas = _init_rebased_origin(tmp_path / "src", [REVIEW_FEATURE_ONLY])
    client, manager, state, httpd = _boot(tmp_config, origin, shas, with_refs=False)
    try:
        res = client.post(
            "/creasy/webhook/gitlab",
            json=_assign_body(),
            headers={"X-Gitlab-Token": "secret"},
        )
        job = _wait_job(manager, res.json()["job_id"])
        assert job.status == "success", job.error_message
        assert job.merge_base == shas["live"]
        _assert_prompt_reviews_live_diff(
            job,
            merge_base=shas["live"],
            must_have=["src/app.py"],
            must_not=["later.txt"],
        )
        assert state.discussions, "merge-base is enough to open a diff thread"
        pos = state.discussions[0]["notes"][0]["position"]
        assert pos["base_sha"] == shas["live"]
        assert pos["start_sha"] == shas["live"]
        assert pos["new_line"] == 2
    finally:
        _shutdown(manager, httpd)


def test_target_moved_without_rebase_uses_old_fork_not_new_target_tip(tmp_config, tmp_path: Path):
    """Source was not rebased. Live merge-base is still the old fork.
    GitLab may report start_sha as the new target tip. The prompt and
    thread must stay on the fork...HEAD feature diff, not later.txt.
    """
    origin, shas = _init_unrebased_origin(tmp_path / "src", [REVIEW_WITH_TARGET_ONLY])
    client, manager, state, httpd = _boot(
        tmp_config,
        origin,
        shas,
        base_sha=shas["fork"],
        start_sha=shas["target_tip"],
    )
    try:
        res = client.post(
            "/creasy/webhook/gitlab",
            json=_assign_body(),
            headers={"X-Gitlab-Token": "secret"},
        )
        job = _wait_job(manager, res.json()["job_id"])
        assert job.status == "success", job.error_message
        assert job.merge_base == shas["fork"]
        _assert_prompt_reviews_live_diff(
            job,
            merge_base=shas["fork"],
            must_have=["src/app.py"],
            must_not=["later.txt"],
        )
        assert _posted_paths(state) == ["src/app.py"]
        pos = state.discussions[0]["notes"][0]["position"]
        assert pos["base_sha"] == shas["fork"]
        assert pos["start_sha"] == shas["fork"]
        assert pos["new_line"] == 2
    finally:
        _shutdown(manager, httpd)
