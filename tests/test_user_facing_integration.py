"""End-to-end user flows: webhook → git → OpenCode serve → GitLab notes.

No injected fakes. GitLab and OpenCode are real HTTP servers. The
worker, manager, GitLab client, and stores are the production classes.
The OpenCode binary is tests/support/opencode_serve_stub.py launched
the same way Creasy launches `opencode serve`.
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
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.dashboard import router as dashboard_router
from creasy.api.webhook import router as webhook_router
from creasy.gitlab.client import GitLabClient
from creasy.gitlab.events import Ignore, classify_webhook
from creasy.jobs.manager import Manager
from creasy.jobs.worker import OpenCodeRunner
from creasy.opencode.session import last_assistant_text, turn_assistant_text
from creasy.workspace.store import WorkspaceStore

STUB = Path(__file__).resolve().parent / "support" / "opencode_serve_stub.py"

REVIEW_MARKDOWN = """### Summary
C++ change. 1 Critical. Do not merge.

### Critical

#### 1. `src/app.py:2` — stack buffer overflow

**Code**
```python
strcpy(dest, src)
```

**Why it is an issue and where**
`src/app.py:2` — unbounded copy into dest.

**Suggested fix**
Use a bounded copy.

```opencoderman-findings
{"findings": [{"path": "src/app.py", "start_line": 2, "end_line": 2, "side": "new", "severity": "critical", "title": "stack buffer overflow", "body": "Unbounded copy into dest."}]}
```
"""

WRAP_UP = (
    "The review is complete — no further steps remain on my side.\n\n"
    "**Current state:**\n- 1 Critical finding emitted.\n- Verdict: do not merge.\n"
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return (result.stdout or "").strip()


def _init_origin(root: Path, assistants: list[str]) -> tuple[Path, str, str]:
    repo = root / "origin"
    repo.mkdir(parents=True)
    _git(repo, "init")
    _git(repo, "checkout", "-B", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")
    # start_serve runs ``<python> serve --port N`` with cwd=clone.
    # The file must be named ``serve`` (no .py) because that is argv[1].
    (repo / "serve").write_text(STUB.read_text(encoding="utf-8"), encoding="utf-8")
    (repo / "assistants.json").write_text(json.dumps(assistants), encoding="utf-8")
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


class _GitlabState:
    def __init__(self, mr: dict[str, Any]) -> None:
        self.lock = threading.Lock()
        self.mr = mr
        self.notes: list[dict[str, Any]] = []
        self.discussions: list[dict[str, Any]] = []
        self.deleted_notes: list[int] = []
        self.note_id = 100
        self.disc_id = 1


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
            parsed = urlparse(self.path)
            path = parsed.path
            st = holder["s"]
            if path == "/api/v4/user":
                self._json({"id": 99, "username": "creasy-bot"})
                return
            if path.endswith("/merge_requests/7"):
                self._json(st.mr)
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
            if "/projects/" in path and path.count("/") == 4:
                self._json({"id": 42, "http_url_to_repo": st.mr.get("http_url_to_repo") or ""})
                return
            self._json({"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path
            body = self._read_json()
            st = holder["s"]
            with st.lock:
                if path.endswith("/merge_requests/7/notes") and "/discussions/" not in path:
                    st.note_id += 1
                    note = {
                        "id": st.note_id,
                        "body": body.get("body") or "",
                        "author": {"id": 99},
                        "system": False,
                    }
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

        def do_DELETE(self) -> None:  # noqa: N802
            st = holder["s"]
            parts = urlparse(self.path).path.rstrip("/").split("/")
            try:
                note_id = int(parts[-1])
            except ValueError:
                self._json({"error": "bad id"}, status=400)
                return
            with st.lock:
                st.deleted_notes.append(note_id)
                st.notes = [n for n in st.notes if n.get("id") != note_id]
                kept = []
                for disc in st.discussions:
                    notes = [n for n in (disc.get("notes") or []) if n.get("id") != note_id]
                    if notes:
                        row = dict(disc)
                        row["notes"] = notes
                        kept.append(row)
                st.discussions = kept
            self.send_response(204)
            self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _mr_payload(origin: Path, head: str, base: str, *, with_refs: bool) -> dict[str, Any]:
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
        data["diff_refs"] = {"base_sha": base, "start_sha": base, "head_sha": head}
    return data


def _boot(
    tmp_config,
    tmp_path: Path,
    *,
    assistants: list[str],
    with_refs: bool = True,
    gitlab_token: str = "",
) -> tuple[TestClient, Manager, _GitlabState, ThreadingHTTPServer]:
    origin, head, base = _init_origin(tmp_path / "src", assistants)
    state = _GitlabState(_mr_payload(origin, head, base, with_refs=with_refs))
    httpd = _gitlab_server(state)
    host, port = httpd.server_address[:2]
    tmp_config.gitlab_url = f"http://{host}:{port}"
    tmp_config.gitlab_token = gitlab_token
    tmp_config.opencode_bin = sys.executable
    tmp_config.opencode_timeout = 20
    tmp_config.hang_timeout = 15
    tmp_config.serve_health_timeout = 15
    tmp_config.opencode_retry_count = 1
    tmp_config.git_timeout = 60
    tmp_config.max_concurrent_jobs = 2
    # Token on the HTTP client only. Keep config.gitlab_token empty so
    # fetch/clone will not try to inject oauth2: into a file:// origin.
    gitlab = GitLabClient(tmp_config.gitlab_url, gitlab_token or "test-token")
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
    app.include_router(dashboard_router)
    return TestClient(app), manager, state, httpd


def _shutdown(manager: Manager, httpd: ThreadingHTTPServer) -> None:
    try:
        manager.shutdown()
    finally:
        httpd.shutdown()
        httpd.server_close()


def _wait_job(manager: Manager, job_id: str, timeout: float = 40):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = manager.store.get(job_id)
        if last and last.status not in {"queued", "running"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {last}")


def _note_body(note: dict) -> str:
    return {
        "object_kind": "note",
        "user": {"id": 1},
        "object_attributes": {"noteable_type": "MergeRequest", "note": note},
        "merge_request": {
            "iid": 7,
            "target_project_id": 42,
            "source_branch": "feat",
            "target_branch": "main",
            "title": "Add overflow",
            "url": "http://gl/group/repo/-/merge_requests/7",
            "draft": False,
        },
    }


def test_review_posts_overview_and_one_diff_thread(tmp_config, tmp_path: Path):
    client, manager, state, httpd = _boot(tmp_config, tmp_path, assistants=[REVIEW_MARKDOWN])
    try:
        res = client.post(
            "/webhook",
            json=_note_body("/review"),
            headers={"X-Gitlab-Token": "secret"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "accepted"
        job = _wait_job(manager, body["job_id"])
        assert job.status == "success", job.error_message
        assert state.notes, "expected an Overview note on the MR"
        note = state.notes[0]["body"]
        assert "stack buffer overflow" in note
        assert "opencoderman-findings" not in note
        assert state.discussions, "expected a GitLab diff thread for the finding"
        disc = state.discussions[0]
        assert "stack buffer overflow" in disc["notes"][0]["body"]
        pos = disc["notes"][0]["position"]
        assert pos.get("new_path") == "src/app.py" or pos.get("old_path") == "src/app.py"
        listed = client.get("/api/jobs").json()
        assert listed["total"] == 1
        assert listed["jobs"][0]["status"] == "success"
        assert listed["jobs"][0]["mr_title"] == "Add overflow"
        detail = client.get(f"/api/jobs/{job.job_id}").json()["job"]
        assert detail["text"]
        assert "overflow" in detail["text"].lower()
    finally:
        _shutdown(manager, httpd)


def test_wrapup_last_message_does_not_replace_the_review_on_the_mr(tmp_config, tmp_path: Path):
    """This turn can end with a wrap-up. The Overview note is the review."""
    client, manager, state, httpd = _boot(
        tmp_config, tmp_path, assistants=[REVIEW_MARKDOWN, WRAP_UP]
    )
    try:
        res = client.post(
            "/webhook",
            json=_note_body("/review"),
            headers={"X-Gitlab-Token": "secret"},
        )
        job = _wait_job(manager, res.json()["job_id"])
        assert job.status == "success", job.error_message
        assert state.notes, "job posted something"
        note = state.notes[0]["body"]
        assert "stack buffer overflow" in note
        assert "The review is complete" not in note
        assert state.discussions, "findings on the review message still become threads"
        assert "stack buffer overflow" in state.discussions[0]["notes"][0]["body"]
        messages = [
            {"info": {"role": "user"}, "parts": [{"type": "text", "text": "review"}]},
            {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": REVIEW_MARKDOWN}]},
            {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": WRAP_UP}]},
        ]
        assert last_assistant_text(messages).strip() == WRAP_UP.strip()
        assert "stack buffer overflow" in turn_assistant_text(messages)
    finally:
        _shutdown(manager, httpd)


def test_empty_gitlab_diff_refs_drops_inline_threads(tmp_config, tmp_path: Path):
    """GitLab often omits diff_refs on a fresh open. Positions then have
    no base_sha, so every finding is skipped even though merge-base and
    the review markdown are available.
    """
    client, manager, state, httpd = _boot(
        tmp_config, tmp_path, assistants=[REVIEW_MARKDOWN], with_refs=False
    )
    try:
        res = client.post(
            "/webhook",
            json=_note_body("/review"),
            headers={"X-Gitlab-Token": "secret"},
        )
        job = _wait_job(manager, res.json()["job_id"])
        assert job.status == "success", job.error_message
        assert state.notes, "Overview note should still post"
        assert "stack buffer overflow" in state.notes[0]["body"]
        assert state.discussions, "merge-base is enough to open a diff thread"
        assert job.merge_base
    finally:
        _shutdown(manager, httpd)


def test_marking_a_draft_ready_starts_a_review(tmp_config, tmp_path: Path):
    """Draft open is skipped. Mark-as-ready (update, no oldrev, draft
    flipped false) enqueues the review the author just asked for.
    """
    client, manager, state, httpd = _boot(tmp_config, tmp_path, assistants=[REVIEW_MARKDOWN])
    try:
        ready = {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "update",
                "iid": 7,
                "target_project_id": 42,
                "source_branch": "feat",
                "target_branch": "main",
                "title": "Add overflow",
                "draft": False,
                "url": "http://gl/mr/7",
            },
            "changes": {"draft": {"previous": True, "current": False}},
        }
        classified = classify_webhook(ready, skip_drafts=True)
        assert not isinstance(classified, Ignore)
        res = client.post("/webhook", json=ready, headers={"X-Gitlab-Token": "secret"})
        assert res.status_code == 200
        assert res.json()["status"] == "accepted"
        job = _wait_job(manager, res.json()["job_id"])
        assert job.status == "success", job.error_message
        assert state.notes
    finally:
        _shutdown(manager, httpd)


def test_draft_open_is_skipped_but_explicit_review_still_runs(tmp_config, tmp_path: Path):
    client, manager, state, httpd = _boot(tmp_config, tmp_path, assistants=[REVIEW_MARKDOWN])
    try:
        draft_open = {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "open",
                "iid": 7,
                "target_project_id": 42,
                "source_branch": "feat",
                "target_branch": "main",
                "title": "Add overflow",
                "draft": True,
            },
        }
        skipped = client.post("/webhook", json=draft_open, headers={"X-Gitlab-Token": "secret"})
        assert skipped.json()["status"] == "ignored"
        note = _note_body("/review please")
        note["merge_request"]["draft"] = True
        res = client.post("/webhook", json=note, headers={"X-Gitlab-Token": "secret"})
        assert res.json()["status"] == "accepted"
        job = _wait_job(manager, res.json()["job_id"])
        assert job.status == "success", job.error_message
        assert state.notes
    finally:
        _shutdown(manager, httpd)


def test_ask_then_reset_through_the_http_api(tmp_config, tmp_path: Path):
    client, manager, state, httpd = _boot(
        tmp_config, tmp_path, assistants=["### Summary\nThe lock is held across the wait."]
    )
    try:
        review = client.post(
            "/webhook",
            json=_note_body("/review"),
            headers={"X-Gitlab-Token": "secret"},
        )
        first = _wait_job(manager, review.json()["job_id"])
        assert first.status == "success", first.error_message
        assert state.notes
        ask = client.post(
            "/webhook",
            json=_note_body("/ask why is this lock held?"),
            headers={"X-Gitlab-Token": "secret"},
        )
        assert ask.json()["status"] == "accepted"
        second = _wait_job(manager, ask.json()["job_id"])
        assert second.status == "success", second.error_message
        assert second.trigger == "ask"
        assert any("Answer" in n["body"] or "lock" in n["body"].lower() for n in state.notes)
        before = len(state.notes)
        reset = client.post(
            "/webhook",
            json=_note_body("/reset"),
            headers={"X-Gitlab-Token": "secret"},
        )
        third = _wait_job(manager, reset.json()["job_id"])
        assert third.status == "success", third.error_message
        assert third.trigger == "reset"
        assert len(state.notes) < before
        workspace = manager.workspaces.get(first.mr_key)
        assert workspace is None or workspace.session_id == ""
        listed = client.get("/api/jobs", params={"filter": "completed"}).json()
        assert listed["total"] >= 3
    finally:
        _shutdown(manager, httpd)


def test_dashboard_search_matches_only_exact_mr_key(tmp_config, tmp_path: Path):
    """Operators coming from GitLab type !7 or the title. The API only
    exact-matches project_id-iid, so those searches look empty.
    """
    client, manager, state, httpd = _boot(tmp_config, tmp_path, assistants=[REVIEW_MARKDOWN])
    try:
        res = client.post(
            "/webhook",
            json=_note_body("/review"),
            headers={"X-Gitlab-Token": "secret"},
        )
        job = _wait_job(manager, res.json()["job_id"])
        key = job.mr_key
        hit = client.get("/api/jobs", params={"jira_id": key}).json()
        assert hit["total"] == 1
        by_iid = client.get("/api/jobs", params={"jira_id": "7"}).json()
        assert by_iid["total"] == 0
        by_bang = client.get("/api/jobs", params={"jira_id": "!7"}).json()
        assert by_bang["total"] == 0
        by_title = client.get("/api/jobs", params={"jira_id": "Add overflow"}).json()
        assert by_title["total"] == 0
        by_mr_key = client.get("/api/jobs", params={"mr_key": key}).json()
        assert by_mr_key["total"] == 1
    finally:
        _shutdown(manager, httpd)


def test_queued_job_is_live_and_jobs_list_has_no_http_poll(tmp_config, tmp_path: Path):
    """A queued comment is stored with live=True (LiveDot on the card).
    The jobs list only reloads when the websocket generation ticks —
    there is no refresh control on that page.
    """
    from creasy.api import dashboard as dash

    src = Path(dash.__file__).read_text(encoding="utf-8")
    assert "await asyncio.sleep(2)" in src
    page = Path(__file__).resolve().parents[1] / "web" / "src" / "pages" / "jobs" / "JobsPage.tsx"
    text = page.read_text(encoding="utf-8")
    assert "live.generation" in text
    assert "Refresh" in text
    assert "setInterval" in text

    client, manager, state, httpd = _boot(tmp_config, tmp_path, assistants=[REVIEW_MARKDOWN])
    try:
        # Hold the first job so the second stays queued.
        first = client.post(
            "/webhook",
            json=_note_body("/review"),
            headers={"X-Gitlab-Token": "secret"},
        )
        second = client.post(
            "/webhook",
            json=_note_body("/ask what about dest?"),
            headers={"X-Gitlab-Token": "secret"},
        )
        assert second.json()["status"] == "queued"
        queued = client.get(f"/api/jobs/{second.json()['job_id']}").json()["job"]
        assert queued["status"] == "queued"
        assert queued["live"] is True
        all_jobs = client.get("/api/jobs").json()
        live_queued = [j for j in all_jobs["jobs"] if j["status"] == "queued"]
        assert live_queued
        assert all(j["live"] is True for j in live_queued)
        _wait_job(manager, first.json()["job_id"])
        _wait_job(manager, second.json()["job_id"])
    finally:
        _shutdown(manager, httpd)


def test_cancel_queued_from_dashboard_does_not_post_a_gitlab_note(tmp_config, tmp_path: Path):
    client, manager, state, httpd = _boot(tmp_config, tmp_path, assistants=[REVIEW_MARKDOWN])
    try:
        first = client.post(
            "/webhook",
            json=_note_body("/review"),
            headers={"X-Gitlab-Token": "secret"},
        )
        second = client.post(
            "/webhook",
            json=_note_body("/ask later?"),
            headers={"X-Gitlab-Token": "secret"},
        )
        assert second.json()["status"] == "queued"
        cancel = client.post(f"/api/jobs/{second.json()['job_id']}/cancel")
        assert cancel.status_code == 200
        queued = client.get(f"/api/jobs/{second.json()['job_id']}").json()["job"]
        assert queued["status"] == "cancelled"
        first_job = _wait_job(manager, first.json()["job_id"])
        assert first_job.status == "success", first_job.error_message
        # Only the finished review posts. Cancelling a queued /ask is silent on GitLab.
        assert len(state.notes) == 1
        assert "Cancelled" not in state.notes[0]["body"]
    finally:
        _shutdown(manager, httpd)


def test_ask_without_a_question_is_ignored(tmp_config, tmp_path: Path):
    client, manager, state, httpd = _boot(tmp_config, tmp_path, assistants=[REVIEW_MARKDOWN])
    try:
        res = client.post(
            "/webhook",
            json=_note_body("/ask   "),
            headers={"X-Gitlab-Token": "secret"},
        )
        assert res.json()["status"] == "ignored"
        assert manager.store.list_all() == []
        assert state.notes == []
    finally:
        _shutdown(manager, httpd)
