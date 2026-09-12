"""New integration tests for frequent critical findings.

Each test drives the production Manager + OpenCodeRunner + GitLab HTTP
client + real git. GitLab and OpenCode are local HTTP servers. Nothing
here imports another test module.

These tests record the behavior the current source actually produces.
The docstring on each test states the product rule that is broken.
"""

from __future__ import annotations

import json
import os
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
from creasy.gitlab.events import ReviewTrigger
from creasy.jobs.manager import Manager
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.queue import JobQueue
from creasy.jobs.worker import OpenCodeRunner, RunResult
from creasy.review.ask import ask_wants_new_review
from creasy.review.findings import split_findings
from creasy.review.mention import comment_intent
from creasy.workspace.store import WorkspaceStore

STUB = Path(__file__).resolve().parent / "support" / "opencode_persist_delay_serve.py"

FIRST_REVIEW = """### Summary
FIRST_REVIEW_MARKER. 1 Critical.

### Critical

#### 1. `src/app.py:2` — stack buffer overflow

**Why it is an issue and where**
Unbounded copy into dest.

```opencoderman-findings
{"findings": [{"path": "src/app.py", "start_line": 2, "end_line": 2, "side": "new", "severity": "critical", "title": "stack buffer overflow", "body": "Unbounded copy into dest."}]}
```
"""

SECOND_REVIEW = """### Summary
SECOND_REVIEW_MARKER. Same file still overflows.

### Critical

#### 1. `src/app.py:2` — still an overflow

**Why it is an issue and where**
Still unbounded.

```opencoderman-findings
{"findings": [{"path": "src/app.py", "start_line": 2, "end_line": 2, "side": "new", "severity": "critical", "title": "still an overflow", "body": "Still unbounded."}]}
```
"""

MARKDOWN_NO_BACKTICKS = """### Summary
1 Critical. Do not merge.

### Critical

#### 1. src/app.py:2 — stack buffer overflow

**Why it is an issue and where**
`src/app.py:2` — unbounded copy into dest.

**Suggested fix**
Use a bounded copy.
"""

ASK_ANSWER = "ASK_ANSWER_MARKER: dest is eight bytes and src is unbounded."


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return (result.stdout or "").strip()


def _init_origin(root: Path, assistants: list[str]) -> tuple[Path, str, str, str]:
    """main A → feat B → main C (target-only) → rebase feat onto C.

    Returns (repo, head, stale_base_A, live_merge_base_C).
    """
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
    stale_base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-b", "feat")
    (repo / "src" / "app.py").write_text("dest = [0] * 8\nstrcpy(dest, src)\n", encoding="utf-8")
    _git(repo, "add", "src/app.py")
    _git(repo, "commit", "-m", "overflow")
    _git(repo, "checkout", "main")
    (repo / "later.txt").write_text("target only\n", encoding="utf-8")
    _git(repo, "add", "later.txt")
    _git(repo, "commit", "-m", "target moved")
    live_base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "feat")
    _git(repo, "rebase", "main")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    return repo, head, stale_base, live_base


class _GitlabState:
    def __init__(self, mr: dict[str, Any]) -> None:
        self.lock = threading.Lock()
        self.mr = mr
        self.notes: list[dict[str, Any]] = []
        self.discussions: list[dict[str, Any]] = []
        self.replies: list[dict[str, Any]] = []
        self.note_id = 100
        self.disc_id = 1
        self.reviewer_state = "unreviewed"
        self.reply_status = 200


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
                self._json({"id": 99, "username": "creasy-bot", "name": "Creasy"})
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
            parsed = urlparse(self.path)
            path = parsed.path
            body = self._read_json()
            st = holder["s"]
            with st.lock:
                if path.endswith("/draft_notes/bulk_publish"):
                    if str(body.get("reviewer_state") or "") == "reviewed":
                        st.reviewer_state = "reviewed"
                    self.send_response(204)
                    self.end_headers()
                    return
                if "/discussions/" in path and path.endswith("/notes"):
                    if st.reply_status >= 400:
                        self._json({"error": "discussion gone"}, status=st.reply_status)
                        return
                    st.note_id += 1
                    note = {
                        "id": st.note_id,
                        "body": body.get("body") or "",
                        "author": {"id": 99},
                        "system": False,
                    }
                    disc_id = path.rstrip("/").split("/")[-2]
                    st.replies.append({"discussion_id": disc_id, "body": note["body"]})
                    for disc in st.discussions:
                        if str(disc.get("id")) == disc_id:
                            disc.setdefault("notes", []).append(note)
                            break
                    self._json(note)
                    return
                if path.endswith("/merge_requests/7/notes"):
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

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _mr_payload(origin: Path, head: str, base: str) -> dict[str, Any]:
    return {
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
        "diff_refs": {"base_sha": base, "start_sha": base, "head_sha": head},
    }


def _boot(
    tmp_config,
    tmp_path: Path,
    *,
    assistants: list[str],
    report_stale_base: bool = False,
    delay_sec: float = 0.0,
) -> tuple[TestClient, Manager, _GitlabState, ThreadingHTTPServer, dict[str, str]]:
    origin, head, stale, live = _init_origin(tmp_path / "src", assistants)
    reported_base = stale if report_stale_base else live
    state = _GitlabState(_mr_payload(origin, head, reported_base))
    httpd = _gitlab_server(state)
    host, port = httpd.server_address[:2]
    tmp_config.gitlab_url = f"http://{host}:{port}"
    tmp_config.gitlab_token = ""
    tmp_config.opencode_bin = sys.executable
    tmp_config.opencode_timeout = 25
    tmp_config.hang_timeout = 20
    tmp_config.serve_health_timeout = 15
    tmp_config.opencode_retry_count = 1
    tmp_config.git_timeout = 60
    tmp_config.max_concurrent_jobs = 2
    tmp_config.review_mention = "creasy"
    if delay_sec:
        os.environ["CREASY_STUB_DELAY_SEC"] = str(delay_sec)
    else:
        os.environ.pop("CREASY_STUB_DELAY_SEC", None)
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
    app.state.bot_mention_names = ["creasy-bot", "creasy"]
    app.include_router(webhook_router)
    shas = {"head": head, "stale": stale, "live": live}
    return TestClient(app), manager, state, httpd, shas


def _shutdown(manager: Manager, httpd: ThreadingHTTPServer) -> None:
    os.environ.pop("CREASY_STUB_DELAY_SEC", None)
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


def _note_body(note: str, *, discussion_id: str = "") -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "noteable_type": "MergeRequest",
        "note": note,
        "id": 501,
    }
    if discussion_id:
        attrs["discussion_id"] = discussion_id
    return {
        "object_kind": "note",
        "user": {"id": 1, "username": "dev"},
        "object_attributes": attrs,
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


def test_finding_discussion_position_uses_stale_gitlab_base_sha_after_rebase(
    tmp_config, tmp_path: Path
):
    """After a rebase onto a moved target, git merge-base is the new target
    tip. Discussion positions must use that SHA, not GitLab's cached
    diff_refs.base_sha.
    """
    client, manager, state, httpd, shas = _boot(
        tmp_config,
        tmp_path,
        assistants=[FIRST_REVIEW],
        report_stale_base=True,
    )
    try:
        assert shas["stale"] != shas["live"]
        res = client.post(
            "/creasy/webhook/gitlab",
            json=_assign_body(),
            headers={"X-Gitlab-Token": "secret"},
        )
        assert res.status_code == 200
        job = _wait_job(manager, res.json()["job_id"])
        assert job.status == "success", job.error_message
        assert job.merge_base == shas["live"]
        assert state.discussions, "expected a diff thread"
        pos = state.discussions[0]["notes"][0]["position"]
        assert pos["base_sha"] == shas["live"]
        assert pos["start_sha"] == shas["live"]
        assert pos["base_sha"] == job.merge_base
        assert pos["base_sha"] != shas["stale"]
        assert "later.txt" not in (job.changed_paths or [])
    finally:
        _shutdown(manager, httpd)


def test_finding_ask_reply_failure_posts_a_new_overview(tmp_config, tmp_path: Path):
    """If the thread reply 404s, the same /ask body is posted as a
    new Overview note. That is intended (AGENTS.md).
    """
    client, manager, state, httpd, _shas = _boot(
        tmp_config, tmp_path, assistants=[ASK_ANSWER]
    )
    state.reply_status = 404
    try:
        res = client.post(
            "/creasy/webhook/gitlab",
            json=_note_body("@creasy-bot /ask why is dest 8 bytes?", discussion_id="disc_user"),
            headers={"X-Gitlab-Token": "secret"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] in {"accepted", "queued"}
        job = _wait_job(manager, body["job_id"])
        assert job.trigger == "ask"
        assert job.status == "success", job.error_message
        assert state.replies == []
        assert state.notes, "failed thread reply fell back to a new overview note"
        assert "ASK_ANSWER_MARKER" in state.notes[0]["body"]
        assert "Creasy" in state.notes[0]["body"]
    finally:
        _shutdown(manager, httpd)


def test_ask_do_a_review_of_this_lock_stays_an_ask(tmp_config, tmp_path: Path):
    """'@bot /ask do a review of this lock?' is a question, not a request
    for a new full review. trigger stays ask; no new diff threads.
    """
    question = "do a review of this lock?"
    assert ask_wants_new_review(question) is False
    intent = comment_intent(f"@creasy-bot /ask {question}", ["creasy-bot", "creasy"])
    assert intent is not None
    assert intent[1] == "ask"

    client, manager, state, httpd, _shas = _boot(
        tmp_config, tmp_path, assistants=[ASK_ANSWER]
    )
    try:
        res = client.post(
            "/creasy/webhook/gitlab",
            json=_note_body(
                f"@creasy-bot /ask {question}",
                discussion_id="disc_user",
            ),
            headers={"X-Gitlab-Token": "secret"},
        )
        assert res.status_code == 200
        job = _wait_job(manager, res.json()["job_id"])
        assert job.trigger == "ask"
        assert job.status == "success", job.error_message
        assert state.discussions == []
    finally:
        _shutdown(manager, httpd)


def test_finding_markdown_title_without_backticks_opens_no_thread(
    tmp_config, tmp_path: Path
):
    """Intentional: headings without backticks are Overview-only
    (AGENTS.md). Threads need a fence or `#### N. \`path:lines\``.
    """
    markdown, findings = split_findings(MARKDOWN_NO_BACKTICKS)
    assert findings == []
    assert "stack buffer overflow" in markdown

    client, manager, state, httpd, _shas = _boot(
        tmp_config, tmp_path, assistants=[MARKDOWN_NO_BACKTICKS]
    )
    try:
        res = client.post(
            "/creasy/webhook/gitlab",
            json=_assign_body(),
            headers={"X-Gitlab-Token": "secret"},
        )
        job = _wait_job(manager, res.json()["job_id"])
        assert job.status == "success", job.error_message
        assert state.notes
        assert "stack buffer overflow" in state.notes[0]["body"]
        assert state.discussions == []
    finally:
        _shutdown(manager, httpd)


def test_finding_resumed_session_posts_previous_review_when_prompt_async_is_slow(
    tmp_config, tmp_path: Path
):
    """The clone keeps ses_* across jobs. prompt_async returns before the
    new user message is visible. wait_idle sees the previous assistant
    text, is not busy, and after idle_settle (8s) returns that old review.
    The worker then does `turn_assistant_text(...) or text` and posts it.
    """
    client, manager, state, httpd, _shas = _boot(
        tmp_config,
        tmp_path,
        assistants=[FIRST_REVIEW, SECOND_REVIEW],
        delay_sec=9.0,
    )
    try:
        first = client.post(
            "/creasy/webhook/gitlab",
            json=_assign_body(),
            headers={"X-Gitlab-Token": "secret"},
        )
        job1 = _wait_job(manager, first.json()["job_id"])
        assert job1.status == "success", job1.error_message
        assert state.notes
        assert "FIRST_REVIEW_MARKER" in state.notes[0]["body"]

        second = client.post(
            "/creasy/webhook/gitlab",
            json=_note_body("@creasy-bot /review", discussion_id="disc_user"),
            headers={"X-Gitlab-Token": "secret"},
        )
        job2 = _wait_job(manager, second.json()["job_id"], timeout=25)
        assert job2.status == "success", job2.error_message
        posted = [item["body"] for item in state.notes] + [item["body"] for item in state.replies]
        second_bodies = [body for body in posted if job2.job_id in body]
        assert second_bodies, posted
        # Current source republishes the first turn.
        assert any("FIRST_REVIEW_MARKER" in body for body in second_bodies)
        assert all("SECOND_REVIEW_MARKER" not in body for body in second_bodies)
    finally:
        _shutdown(manager, httpd)


def test_finding_azure_current_user_never_retries_after_first_failure() -> None:
    """Intentional: Azure current_user is once per process (AGENTS.md).
    A failed first lookup is not retried.
    """
    from creasy.azure.client import AzureClient

    hits = {"n": 0, "fail": True}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            hits["n"] += 1
            if hits["fail"]:
                body = b'{"error":"no"}'
                self.send_response(400)
            else:
                body = json.dumps(
                    {
                        "authenticatedUser": {
                            "id": "bot-guid",
                            "providerDisplayName": "Creasy",
                            "uniqueName": "DOMAIN\\\\creasy",
                        }
                    }
                ).encode("utf-8")
                self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address[:2]
    client = AzureClient(f"http://{host}:{port}", "pat-test")
    try:
        first = client.current_user()
        assert first is None
        after_fail = hits["n"]
        assert after_fail > 0
        hits["fail"] = False
        second = client.current_user()
        assert second is None
        assert hits["n"] == after_fail
        assert client.current_user_id() is None
    finally:
        client.close()
        httpd.shutdown()
        httpd.server_close()


def test_finding_torn_queue_json_drops_the_fifo(tmp_path: Path) -> None:
    """JobQueue._persist writes queue.json in place. A crash or AV lock
    mid-write leaves invalid JSON. The next process loads {} and the
    in-memory FIFO is empty until boot re-enqueues leftover queued jobs
    from the store. A process that stays up after a failed persist has
    no such recovery.
    """
    path = tmp_path / "queue.json"
    queue = JobQueue(path)
    queue.enqueue("42-7", "job_aaaa")
    queue.enqueue("42-7", "job_bbbb")
    assert queue.queued_ids("42-7") == ["job_aaaa", "job_bbbb"]
    path.write_text("{", encoding="utf-8")
    reloaded = JobQueue(path)
    assert reloaded.queued_ids("42-7") == []
    assert reloaded.public_items() == []


def test_finding_missing_job_file_at_run_start_wedges_the_mr(tmp_config) -> None:
    """_try_start_locked increments _running and adds the MR to
    _running_mr, then starts the worker thread. If store.get(job_id) is
    None at the top of _run_job (corrupt or deleted JSON), _after_job(None)
    decrements the global counter but never discards the MR key. Later
    submit for that MR is ignored.
    """

    class _Noop:
        def run(self, job, should_stop):
            return RunResult(text="ok", posted=True)

    manager = Manager(tmp_config, _Noop())
    manager.ready = True
    key = "42-7"
    job_id = mint_job_id()
    job = JobRecord(
        job_id=job_id,
        mr_key=key,
        project_id=42,
        mr_iid=7,
        trigger="review",
        status="running",
        accepted_at="2026-01-01T00:00:00.000Z",
    )
    manager.store.save(job)
    manager._running = 1
    manager._running_mr.add(key)
    manager.store._path(job_id).write_text("{", encoding="utf-8")
    manager._run_job(job_id, threading.Event())
    assert key in manager._running_mr

    ack, next_job, message = manager.submit(
        ReviewTrigger(
            kind="ask",
            project_id=42,
            mr_iid=7,
            comment_text="why dest?",
            explicit=True,
        )
    )
    # Explicit /ask is queued (FIFO). The worker never starts because
    # _running_mr still holds the MR after the missing-file _after_job(None).
    assert ack == "queued"
    assert next_job is not None
    assert manager._try_start_locked(key) is False
    assert manager.store.get(next_job.job_id) is not None
    assert manager.store.get(next_job.job_id).status == "queued"
    manager.ready = False
