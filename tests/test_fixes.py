"""Regression tests for the six remaining verified issues."""

from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.webhook import router as webhook_router
from creasy.gitlab.client import MergeRequest
from creasy.jobs.manager import Manager
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.store import JobStore
from creasy.jobs.worker import OpenCodeRunner, RunResult
from creasy.opencode.serve import ServeHandle
from creasy.opencode.session import OpenCodeClient, OpenCodeError
from creasy.review.prompt import hang_resume_prompt
from creasy.workspace.gitops import DiffIndex
from creasy.workspace.store import WorkspaceRecord, WorkspaceStore


def _mr(**kwargs) -> MergeRequest:
    data = dict(
        project_id=1,
        iid=1,
        title="Add login",
        description="",
        author="a",
        source_branch="feat",
        target_branch="main",
        sha="newsha",
        base_sha="oldsha",
        start_sha="oldsha",
        web_url="http://gl/mr/1",
        http_url="http://example/repo.git",
        draft=False,
        state="opened",
    )
    data.update(kwargs)
    return MergeRequest(**data)


def _job(**kwargs) -> JobRecord:
    data = dict(
        job_id=mint_job_id(),
        mr_key="1-1",
        project_id=1,
        mr_iid=1,
        trigger="review",
        log_file="job.log",
    )
    data.update(kwargs)
    return JobRecord(**data)


class SpyGitlab:
    def __init__(self, mr: MergeRequest | None = None) -> None:
        self.mr = mr or _mr()
        self.notes: list[str] = []

    def get_merge_request(self, project_id: int, mr_iid: int) -> MergeRequest:
        return self.mr

    def resolve_http_url(self, project_id: int, fallback: str = "") -> str:
        return fallback or self.mr.http_url

    def post_note(self, project_id: int, mr_iid: int, body: str) -> dict:
        self.notes.append(body)
        return {"ok": True}


def test_record_spawn_persists_pid_and_job_log(tmp_config):
    store = JobStore(tmp_config.job_dir)
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, SpyGitlab(), store=store)
    job = _job()
    store.save(job)
    handle = ServeHandle(pid=4242, port=9, base_url="http://127.0.0.1:9", proc=None, log_path=tmp_config.serve_dir / "x.log")  # type: ignore[arg-type]
    runner._record_spawn(job, handle)
    saved = store.get(job.job_id)
    assert saved is not None
    assert saved.serve_pid == 4242
    assert saved.serve_port == 9
    log_path = tmp_config.log_dir / job.log_file
    assert log_path.is_file()
    assert "4242" in log_path.read_text(encoding="utf-8")


def test_ask_prompt_uses_previous_sha_not_overwritten_last_sha(tmp_config):
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), SpyGitlab())
    workspace = WorkspaceRecord(mr_key="1-1", project_id=1, mr_iid=1, last_sha="newsha", session_id="ses_old")
    job = _job(trigger="ask", comment_text="why this lock?")
    index = DiffIndex(merge_base="b", stat="app.py | 1 +", paths=["app.py"], statuses={"app.py": "M"})
    text = runner._prompt(job, _mr(sha="newsha"), index, workspace, created_new=False, previous_sha="oldsha")
    assert "oldsha" in text
    assert "newsha" in text
    assert "why this lock?" in text
    unchanged = runner._prompt(job, _mr(sha="newsha"), index, workspace, created_new=False, previous_sha="newsha")
    assert unchanged == "why this lock?"


class _IdleHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _json(self, payload) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/global/health":
            self._json({"ok": True})
            return
        if path == "/session/status":
            self._json({"ses_verify": {"type": "idle"}})
            return
        if path.endswith("/message"):
            self._json(
                [
                    {
                        "info": {"id": "msg_1", "role": "assistant"},
                        "parts": [{"type": "text", "text": "review looks good"}],
                    }
                ]
            )
            return
        self._json({"id": "ses_verify"})

    def do_POST(self) -> None:  # noqa: N802
        self._json({"ok": True})


class _BusyThenIdleHandler(BaseHTTPRequestHandler):
    """Same assistant text the whole time; busy then idle (StructuredOutput)."""

    started: float = 0.0

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _json(self, payload) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/global/health":
            self._json({"ok": True})
            return
        if path == "/session/status":
            busy = (time.time() - self.started) < 0.8
            self._json({"ses_tool": {"type": "busy" if busy else "idle"}})
            return
        if path.endswith("/message"):
            self._json(
                [
                    {
                        "info": {"id": "msg_1", "role": "assistant"},
                        "parts": [{"type": "text", "text": "review already written"}],
                    }
                ]
            )
            return
        self._json({"id": "ses_tool"})

    def do_POST(self) -> None:  # noqa: N802
        self._json({"ok": True})


def test_wait_idle_does_not_hang_when_busy_without_new_text():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    _BusyThenIdleHandler.started = time.time()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _BusyThenIdleHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    client = OpenCodeClient(f"http://127.0.0.1:{port}", "C:/tmp/clone")
    try:
        text = client.wait_idle("ses_tool", timeout=4, hang_timeout=0.4, idle_settle=0.1)
        assert text == "review already written"
    finally:
        client.close()
        httpd.shutdown()


def test_wait_idle_accepts_already_finished_text():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _IdleHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    client = OpenCodeClient(f"http://127.0.0.1:{port}", "C:/tmp/clone")
    try:
        t0 = time.time()
        text = client.wait_idle("ses_verify", timeout=4, hang_timeout=3, idle_settle=0.2)
        elapsed = time.time() - t0
        assert text == "review looks good"
        assert elapsed < 2.0
    finally:
        client.close()
        httpd.shutdown()


def test_hang_retry_posts_resume_not_original(tmp_config, monkeypatch):
    spy = SpyGitlab()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), spy)
    posts: list[str] = []
    waits = {"n": 0}

    class FakeClient:
        def __init__(self, *a, **k) -> None:
            pass

        def resume_or_create(self, inbound, title):
            return "ses_abc", False

        def post_message(self, session_id, text, *, model, agent):
            posts.append(text)

        def wait_idle(self, session_id, *, timeout, hang_timeout, should_stop=None, idle_settle=8.0):
            waits["n"] += 1
            if waits["n"] == 1:
                raise OpenCodeError("hang")
            return "second attempt ok"

        def get_session(self, session_id):
            return SimpleNamespace(status_code=200)

        def create_session(self, title):
            return "ses_abc"

        def list_messages(self, session_id):
            return []

        def abort(self, session_id):
            return None

        def close(self):
            return None

    import creasy.jobs.worker as w

    monkeypatch.setattr(
        w,
        "start_serve",
        lambda **k: ServeHandle(pid=1, port=9, base_url="http://127.0.0.1:9", proc=None, log_path=tmp_config.serve_dir / "s.log"),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(w, "stop_serve", lambda h: None)
    monkeypatch.setattr(w, "OpenCodeClient", FakeClient)
    monkeypatch.setattr(w, "stop_job_holders", lambda *a, **k: None)
    monkeypatch.setattr(
        runner,
        "_ensure_workspace",
        lambda job, mr, stop: WorkspaceRecord(
            mr_key=job.mr_key,
            project_id=job.project_id,
            mr_iid=job.mr_iid,
            clone_path=str(tmp_config.work_dir / "1-1"),
            last_sha="newsha",
            session_id="ses_abc",
        ),
    )
    (tmp_config.work_dir / "1-1").mkdir(parents=True)
    monkeypatch.setattr(w, "resolve_merge_base", lambda *a, **k: "base")
    monkeypatch.setattr(
        w,
        "diff_stat",
        lambda *a, **k: DiffIndex(merge_base="base", stat="stat", paths=["a.py"], statuses={"a.py": "M"}),
    )
    tmp_config.opencode_retry_count = 2
    result = runner.run(_job(comment_text="please review"), lambda: False)
    assert len(posts) == 2
    assert posts[0] != posts[1]
    assert posts[1] == hang_resume_prompt()
    assert "please review" in posts[0]
    assert result.text == "second attempt ok"


def test_cancelled_running_job_posts_note(tmp_config):
    spy = SpyGitlab()
    runner = OpenCodeRunner(tmp_config, WorkspaceStore(tmp_config.data_dir / "ws"), spy)
    result = runner.run(_job(), lambda: True)
    assert result.cancelled
    assert result.posted
    assert spy.notes
    assert "cancelled" in spy.notes[0].lower()


def test_webhook_close_returns_without_waiting(tmp_config):
    class StickyRunner:
        def __init__(self) -> None:
            self.started = threading.Event()

        def run(self, job, should_stop):
            self.started.set()
            time.sleep(2.0)
            return RunResult(cancelled=should_stop(), text="late")

    runner = StickyRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.state.bot_user_id = 99
    app.include_router(webhook_router)
    client = TestClient(app)
    opened = client.post(
        "/webhook",
        json={
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "open",
                "iid": 11,
                "target_project_id": 6,
                "source_branch": "f",
                "target_branch": "main",
                "draft": False,
            },
        },
        headers={"X-Gitlab-Token": "secret"},
    )
    assert opened.json()["status"] == "accepted"
    assert runner.started.wait(2)
    t0 = time.time()
    closed = client.post(
        "/webhook",
        json={
            "object_kind": "merge_request",
            "object_attributes": {"action": "close", "iid": 11, "target_project_id": 6},
        },
        headers={"X-Gitlab-Token": "secret"},
    )
    elapsed = time.time() - t0
    assert closed.status_code == 200
    assert closed.json()["status"] == "accepted"
    assert elapsed < 0.8
    manager.shutdown()
