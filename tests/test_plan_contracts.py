"""Pins AGENTS.md / plan.md contracts that the other suites do not cover.

Not included: OSM stay-up extras, draft→ready auto-review (update without
oldrev is ignored on purpose).
"""

from __future__ import annotations

import json
import logging
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.dashboard import router as dashboard_router
from creasy.gitlab.client import GitLabError, MergeRequest
from creasy.gitlab.events import ReviewTrigger, first_command
from creasy.jobs.manager import Manager
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.worker import OpenCodeRunner
from creasy.opencode.serve import ServeHandle
from creasy.opencode.session import OpenCodeClient, OpenCodeError
from creasy.review.format import format_cancelled, format_failure, format_success
from creasy.logging import redact_userinfo
from creasy.workspace.gitops import (
    DiffIndex,
    GitError,
    _run_git,
    _scrub_origin,
    inject_token,
    isolated_git_env,
    public_git_url,
)
from creasy.workspace.store import WorkspaceRecord, WorkspaceStore
from conftest import FakeRunner


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
    def __init__(self) -> None:
        self.mr = _mr()
        self.notes: list[str] = []
        self.discussions: list[dict] = []
        self.replies: list[dict] = []
        self.existing: list[dict] = []
        self.discussion_error: Exception | None = None
        self.list_error: Exception | None = None
        self.reply_error: Exception | None = None
        self.mr_error: Exception | None = None
        self.note_error: Exception | None = None

    def get_merge_request(self, project_id: int, mr_iid: int) -> MergeRequest:
        if self.mr_error:
            raise self.mr_error
        return self.mr

    def resolve_http_url(self, project_id: int, fallback: str = "") -> str:
        return fallback or self.mr.http_url

    def post_note(self, project_id: int, mr_iid: int, body: str) -> dict:
        if self.note_error:
            raise self.note_error
        self.notes.append(body)
        return {"ok": True}

    def list_discussions(self, project_id: int, mr_iid: int) -> list:
        if self.list_error:
            raise self.list_error
        return list(self.existing)

    def reply_to_discussion(self, project_id: int, mr_iid: int, discussion_id: str, body: str) -> dict:
        if self.reply_error:
            raise self.reply_error
        self.replies.append({"id": discussion_id, "body": body})
        return {"id": discussion_id}

    def post_discussion(self, project_id: int, mr_iid: int, body: str, position: dict) -> dict:
        if self.discussion_error:
            raise self.discussion_error
        self.discussions.append({"body": body, "position": position})
        return {"id": "disc1"}


class _FakeServeClient:
    """OpenCode HTTP stand-in for worker tests. No real serve."""

    def __init__(self, *a, wait=None, error=None, inbound_check=None, **k) -> None:
        self._wait = wait
        self._error = error
        self._inbound_check = inbound_check
        self.created_new = False

    def resume_or_create(self, inbound, title):
        if self._inbound_check is not None:
            self._inbound_check(inbound)
        if inbound and inbound.startswith("ses_"):
            return inbound, False
        self.created_new = True
        return "ses_new", True

    def post_message(self, session_id, text, *, model, agent):
        return None

    def wait_idle(self, session_id, *, timeout, hang_timeout, should_stop=None, idle_settle=8.0):
        if self._error:
            raise OpenCodeError(self._error)
        return self._wait or "review body"

    def get_session(self, session_id):
        return SimpleNamespace(status_code=200)

    def create_session(self, title):
        return "ses_new"

    def list_messages(self, session_id):
        return []

    def abort(self, session_id):
        return None

    def close(self):
        return None


def _patch_worker(monkeypatch, tmp_config, client_factory, dest) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    import creasy.jobs.worker as w

    monkeypatch.setattr(
        w,
        "start_serve",
        lambda **k: ServeHandle(
            pid=1,
            port=9,
            base_url="http://127.0.0.1:9",
            proc=None,  # type: ignore[arg-type]
            log_path=tmp_config.serve_dir / "s.log",
        ),
    )
    monkeypatch.setattr(w, "stop_serve", lambda h: None)
    monkeypatch.setattr(w, "OpenCodeClient", client_factory)
    monkeypatch.setattr(w, "stop_job_holders", lambda *a, **k: None)
    monkeypatch.setattr(w, "resolve_merge_base", lambda *a, **k: "base")
    monkeypatch.setattr(
        w,
        "diff_stat",
        lambda *a, **k: DiffIndex(merge_base="base", stat="stat", paths=["a.py"], statuses={"a.py": "M"}),
    )


def test_worker_success_posts_note_and_keeps_clone(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)

    def _ensure(job, mr, stop):
        return workspaces.save(
            WorkspaceRecord(
                mr_key=job.mr_key,
                project_id=job.project_id,
                mr_iid=job.mr_iid,
                clone_path=str(dest),
                last_sha="newsha",
            )
        )

    _patch_worker(monkeypatch, tmp_config, lambda *a, **k: _FakeServeClient(wait="looks good"), dest)
    monkeypatch.setattr(runner, "_ensure_workspace", _ensure)
    result = runner.run(_job(), lambda: False)
    assert result.error == ""
    assert result.text == "looks good"
    assert result.posted
    assert spy.notes and "looks good" in spy.notes[0]
    assert dest.exists()


def test_worker_posts_note_and_diff_threads(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)
    reply = """### Summary
1 Critical.

```creasy-findings
{"findings":[{"path":"src/buf.cpp","start_line":2,"end_line":2,"severity":"critical","title":"overflow","body":"strcpy overflows"}]}
```
"""
    diff = """diff --git a/src/buf.cpp b/src/buf.cpp
new file mode 100644
--- /dev/null
+++ b/src/buf.cpp
@@ -0,0 +1,3 @@
+char dest[8];
+strcpy(dest, src);
+return dest;
"""

    def _ensure(job, mr, stop):
        return workspaces.save(
            WorkspaceRecord(
                mr_key=job.mr_key,
                project_id=job.project_id,
                mr_iid=job.mr_iid,
                clone_path=str(dest),
                last_sha="newsha",
            )
        )

    _patch_worker(monkeypatch, tmp_config, lambda *a, **k: _FakeServeClient(wait=reply), dest)
    monkeypatch.setattr(runner, "_ensure_workspace", _ensure)
    import creasy.jobs.worker as w

    monkeypatch.setattr(w, "unified_diff", lambda *a, **k: diff)
    result = runner.run(_job(), lambda: False)
    assert result.posted
    assert result.findings_posted == 1
    assert spy.notes and "### Summary" in spy.notes[0]
    assert "creasy-findings" not in spy.notes[0]
    assert '"path"' not in spy.notes[0]
    assert len(spy.discussions) == 1
    disc = spy.discussions[0]
    assert "overflow" in disc["body"]
    assert disc["position"]["new_path"] == "src/buf.cpp"
    assert disc["position"]["new_line"] == 2
    assert disc["position"]["head_sha"] == "newsha"
    assert disc["position"]["base_sha"] == "oldsha"


def test_worker_replies_to_overlapping_unresolved_thread(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    spy.existing = [
        {
            "id": "disc_old",
            "notes": [
                {
                    "body": "<!-- creasy-finding -->\n**Critical** · overflow",
                    "resolved": False,
                    "position": {
                        "new_path": "src/buf.cpp",
                        "old_path": "src/buf.cpp",
                        "new_line": 2,
                    },
                }
            ],
        }
    ]
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)
    reply = """### Summary
1 Critical.

```creasy-findings
{"findings":[{"path":"src/buf.cpp","start_line":2,"end_line":2,"severity":"critical","title":"overflow","body":"still overflows"}]}
```
"""
    diff = """diff --git a/src/buf.cpp b/src/buf.cpp
new file mode 100644
--- /dev/null
+++ b/src/buf.cpp
@@ -0,0 +1,3 @@
+char dest[8];
+strcpy(dest, src);
+return dest;
"""

    def _ensure(job, mr, stop):
        return workspaces.save(
            WorkspaceRecord(
                mr_key=job.mr_key,
                project_id=job.project_id,
                mr_iid=job.mr_iid,
                clone_path=str(dest),
                last_sha="newsha",
            )
        )

    _patch_worker(monkeypatch, tmp_config, lambda *a, **k: _FakeServeClient(wait=reply), dest)
    monkeypatch.setattr(runner, "_ensure_workspace", _ensure)
    import creasy.jobs.worker as w

    monkeypatch.setattr(w, "unified_diff", lambda *a, **k: diff)
    result = runner.run(_job(), lambda: False)
    assert result.posted
    assert result.findings_posted == 1
    assert spy.discussions == []
    assert len(spy.replies) == 1
    assert spy.replies[0]["id"] == "disc_old"
    assert "still overflows" in spy.replies[0]["body"]


def test_worker_posts_new_thread_when_reply_fails(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    spy.reply_error = GitLabError("gone", status_code=404)
    spy.existing = [
        {
            "id": "disc_old",
            "notes": [
                {
                    "body": "<!-- creasy-finding -->\n**Critical** · overflow",
                    "resolved": False,
                    "position": {"new_path": "src/buf.cpp", "new_line": 2},
                }
            ],
        }
    ]
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)
    reply = """```creasy-findings
{"findings":[{"path":"src/buf.cpp","start_line":2,"end_line":2,"severity":"critical","title":"overflow","body":"strcpy overflows"}]}
```
"""
    diff = """diff --git a/src/buf.cpp b/src/buf.cpp
new file mode 100644
--- /dev/null
+++ b/src/buf.cpp
@@ -0,0 +1,3 @@
+char dest[8];
+strcpy(dest, src);
+return dest;
"""

    def _ensure(job, mr, stop):
        return workspaces.save(
            WorkspaceRecord(
                mr_key=job.mr_key,
                project_id=job.project_id,
                mr_iid=job.mr_iid,
                clone_path=str(dest),
                last_sha="newsha",
            )
        )

    _patch_worker(monkeypatch, tmp_config, lambda *a, **k: _FakeServeClient(wait=reply), dest)
    monkeypatch.setattr(runner, "_ensure_workspace", _ensure)
    import creasy.jobs.worker as w

    monkeypatch.setattr(w, "unified_diff", lambda *a, **k: diff)
    result = runner.run(_job(), lambda: False)
    assert result.findings_posted == 1
    assert spy.replies == []
    assert len(spy.discussions) == 1


def test_worker_discussion_failure_does_not_fail_job(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    spy.discussion_error = GitLabError("line_code can't be blank", status_code=400)
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)
    reply = """### Summary
ok

```creasy-findings
{"findings":[{"path":"src/buf.cpp","start_line":1,"title":"x","body":"y"}]}
```
"""

    def _ensure(job, mr, stop):
        return workspaces.save(
            WorkspaceRecord(
                mr_key=job.mr_key,
                project_id=job.project_id,
                mr_iid=job.mr_iid,
                clone_path=str(dest),
                last_sha="newsha",
            )
        )

    _patch_worker(monkeypatch, tmp_config, lambda *a, **k: _FakeServeClient(wait=reply), dest)
    monkeypatch.setattr(runner, "_ensure_workspace", _ensure)
    import creasy.jobs.worker as w

    monkeypatch.setattr(
        w,
        "unified_diff",
        lambda *a, **k: (
            "diff --git a/src/buf.cpp b/src/buf.cpp\n"
            "--- /dev/null\n+++ b/src/buf.cpp\n"
            "@@ -0,0 +1,1 @@\n+x\n"
        ),
    )
    result = runner.run(_job(), lambda: False)
    assert result.posted
    assert result.error == ""
    assert result.findings_posted == 0
    assert spy.notes
    assert spy.discussions == []


def test_worker_failure_posts_error_note_and_keeps_clone(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)

    def _ensure(job, mr, stop):
        return workspaces.save(
            WorkspaceRecord(
                mr_key=job.mr_key,
                project_id=job.project_id,
                mr_iid=job.mr_iid,
                clone_path=str(dest),
                last_sha="newsha",
            )
        )

    tmp_config.opencode_retry_count = 1
    _patch_worker(
        monkeypatch,
        tmp_config,
        lambda *a, **k: _FakeServeClient(error="serve-dead"),
        dest,
    )
    monkeypatch.setattr(runner, "_ensure_workspace", _ensure)
    result = runner.run(_job(), lambda: False)
    assert result.error
    assert result.posted
    assert spy.notes and "failed" in spy.notes[0].lower()
    assert dest.exists()


def test_worker_clone_auth_failure_posts_error_note(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    spy.mr = _mr(http_url="https://gitlab.example/group/repo.git")
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)
    started: list[int] = []
    import creasy.jobs.worker as w

    monkeypatch.setattr(
        w,
        "clone_repo",
        lambda *a, **k: (_ for _ in ()).throw(
            GitError("git failed (128): HTTP Basic: Access denied")
        ),
    )
    monkeypatch.setattr(w, "start_serve", lambda **k: started.append(1))
    monkeypatch.setattr(w, "stop_serve", lambda h: None)
    monkeypatch.setattr(w, "stop_job_holders", lambda *a, **k: None)

    result = runner.run(_job(), lambda: False)
    assert result.error.startswith("git failed:")
    assert "Access denied" in result.error
    assert result.posted
    assert result.clone_path == ""
    assert spy.notes and "Review failed" in spy.notes[0]
    assert "Access denied" in spy.notes[0]
    assert not dest.exists()
    assert started == []


def test_worker_fetch_auth_failure_keeps_clone_and_posts_error(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()
    spy = SpyGitlab()
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)
    started: list[int] = []
    import creasy.jobs.worker as w

    monkeypatch.setattr(
        w,
        "fetch_and_checkout",
        lambda *a, **k: (_ for _ in ()).throw(
            GitError("git failed (128): The project you were looking for could not be found")
        ),
    )
    monkeypatch.setattr(w, "start_serve", lambda **k: started.append(1))
    monkeypatch.setattr(w, "stop_serve", lambda h: None)
    monkeypatch.setattr(w, "stop_job_holders", lambda *a, **k: None)

    result = runner.run(_job(), lambda: False)
    assert result.error.startswith("git failed:")
    assert "could not be found" in result.error
    assert result.posted
    assert spy.notes and "Review failed" in spy.notes[0]
    assert dest.exists()
    assert started == []


def test_worker_mr_fetch_401_posts_error_note(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    spy.mr_error = GitLabError(
        "fetch MR failed: Client error '401 Unauthorized' for url 'https://gitlab.example/api/v4/projects/1/merge_requests/1'",
        status_code=401,
    )
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)
    started: list[int] = []
    import creasy.jobs.worker as w

    monkeypatch.setattr(w, "start_serve", lambda **k: started.append(1))
    monkeypatch.setattr(w, "stop_serve", lambda h: None)
    monkeypatch.setattr(w, "stop_job_holders", lambda *a, **k: None)

    result = runner.run(_job(), lambda: False)
    assert result.error.startswith("pipeline failed:")
    assert "401" in result.error
    assert result.posted
    assert spy.notes and "Review failed" in spy.notes[0]
    assert not dest.exists()
    assert started == []


def test_worker_missing_repo_url_posts_error_note(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    spy.mr = _mr(http_url="")
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)
    started: list[int] = []
    import creasy.jobs.worker as w

    monkeypatch.setattr(w, "start_serve", lambda **k: started.append(1))
    monkeypatch.setattr(w, "stop_serve", lambda h: None)
    monkeypatch.setattr(w, "stop_job_holders", lambda *a, **k: None)

    result = runner.run(_job(), lambda: False)
    assert result.error.startswith("git failed:")
    assert "no http repo url" in result.error
    assert result.posted
    assert not dest.exists()
    assert started == []


def test_worker_note_403_leaves_job_unposted(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    spy.note_error = GitLabError("post note failed: 403 Forbidden", status_code=403)
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)

    def _ensure(job, mr, stop):
        return workspaces.save(
            WorkspaceRecord(
                mr_key=job.mr_key,
                project_id=job.project_id,
                mr_iid=job.mr_iid,
                clone_path=str(dest),
                last_sha="newsha",
            )
        )

    _patch_worker(monkeypatch, tmp_config, lambda *a, **k: _FakeServeClient(wait="looks good"), dest)
    monkeypatch.setattr(runner, "_ensure_workspace", _ensure)
    result = runner.run(_job(), lambda: False)
    assert result.error == ""
    assert result.text == "looks good"
    assert result.posted is False
    assert spy.notes == []
    assert dest.exists()


def test_worker_empty_token_is_passed_to_clone(tmp_config, monkeypatch):
    tmp_config.gitlab_token = ""
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    spy.mr = _mr(http_url="https://gitlab.example/group/repo.git")
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    runner = OpenCodeRunner(tmp_config, workspaces, spy)
    seen: dict[str, object] = {}
    import creasy.jobs.worker as w

    def _clone(url, dest_path, token, *, timeout):
        seen["url"] = url
        seen["token"] = token
        raise GitError("git failed (128): Authentication failed")

    monkeypatch.setattr(w, "clone_repo", _clone)
    monkeypatch.setattr(w, "start_serve", lambda **k: None)
    monkeypatch.setattr(w, "stop_serve", lambda h: None)
    monkeypatch.setattr(w, "stop_job_holders", lambda *a, **k: None)

    result = runner.run(_job(), lambda: False)
    assert seen["token"] == ""
    assert seen["url"] == "https://gitlab.example/group/repo.git"
    assert result.error.startswith("git failed:")
    assert result.posted
    assert not dest.exists()


def test_unreadable_session_creates_new(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    spy = SpyGitlab()
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    workspaces.save(
        WorkspaceRecord(
            mr_key="1-1",
            project_id=1,
            mr_iid=1,
            clone_path=str(dest),
            session_id="ses_poisoned",
            last_sha="newsha",
        )
    )
    runner = OpenCodeRunner(tmp_config, workspaces, spy)
    created: list[str] = []

    class Poisoned(_FakeServeClient):
        def resume_or_create(self, inbound, title):
            return inbound, False

        def list_messages(self, session_id):
            if session_id == "ses_poisoned":
                raise OpenCodeError("messages unreadable", status_code=400)
            return []

        def create_session(self, title):
            created.append(title)
            return "ses_fresh"

    def _ensure(job, mr, stop):
        record = workspaces.get(job.mr_key)
        assert record is not None
        return record

    _patch_worker(monkeypatch, tmp_config, Poisoned, dest)
    monkeypatch.setattr(runner, "_ensure_workspace", _ensure)
    result = runner.run(_job(), lambda: False)
    assert created
    assert result.session_id == "ses_fresh"
    assert result.posted


def test_rejected_session_creates_new_and_continues(tmp_config, monkeypatch):
    dest = tmp_config.work_dir / "1-1"
    seen: list[object] = []
    spy = SpyGitlab()
    workspaces = WorkspaceStore(tmp_config.data_dir / "ws")
    workspaces.save(
        WorkspaceRecord(
            mr_key="1-1",
            project_id=1,
            mr_iid=1,
            clone_path=str(dest),
            session_id="ses_dead",
            last_sha="newsha",
        )
    )
    runner = OpenCodeRunner(tmp_config, workspaces, spy)

    class RejectThenCreate(_FakeServeClient):
        def resume_or_create(self, inbound, title):
            seen.append(inbound)
            return "ses_replacement", True

    def _ensure(job, mr, stop):
        record = workspaces.get(job.mr_key)
        assert record is not None
        return record

    _patch_worker(monkeypatch, tmp_config, RejectThenCreate, dest)
    monkeypatch.setattr(runner, "_ensure_workspace", _ensure)
    result = runner.run(_job(trigger="ask", comment_text="why?"), lambda: False)
    assert seen == ["ses_dead"]
    assert result.session_id == "ses_replacement"
    assert result.error == ""
    assert result.posted
    saved = workspaces.get("1-1")
    assert saved is not None
    assert saved.session_id == "ses_replacement"


def test_boot_leftover_running_is_error_not_resumed(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    leftover = JobRecord(
        job_id=mint_job_id(),
        mr_key="3-1",
        project_id=3,
        mr_iid=1,
        trigger="review",
        status="running",
        live=True,
        serve_pid=None,
    )
    manager.store.save(leftover)
    manager.boot()
    saved = manager.store.get(leftover.job_id)
    assert saved is not None
    assert saved.status == "error"
    assert saved.live is False
    assert "not resumed" in (saved.error_message or "")
    assert runner.runs == []
    manager.shutdown()


def test_boot_reenqueues_leftover_queued_then_dispatches(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    leftover = JobRecord(
        job_id=mint_job_id(),
        mr_key="3-2",
        project_id=3,
        mr_iid=2,
        trigger="ask",
        status="queued",
        live=True,
        comment_text="queued leftover",
        explicit=True,
    )
    manager.store.save(leftover)
    assert leftover.job_id not in manager.queue.queued_ids("3-2")
    manager.boot()
    assert runner.started.wait(2)
    assert any(r.startswith("ask") for r in runner.runs)
    runner.release.set()
    manager.shutdown()


def test_shutdown_cancels_queued_and_rejects_new_submit(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    manager.submit(
        ReviewTrigger(kind="review", project_id=4, mr_iid=1, explicit=True, comment_text="first")
    )
    assert runner.started.wait(2)
    ack2, queued, _ = manager.submit(
        ReviewTrigger(kind="ask", project_id=4, mr_iid=1, explicit=True, comment_text="later")
    )
    assert ack2 == "queued"
    assert queued is not None
    manager.shutdown()
    saved = manager.store.get(queued.job_id)
    assert saved is not None
    assert saved.status == "cancelled"
    ack3, job3, _ = manager.submit(
        ReviewTrigger(kind="review", project_id=4, mr_iid=1, explicit=True)
    )
    assert ack3 == "ignored"
    assert job3 is None


def test_dashboard_token_required_when_set(tmp_config):
    tmp_config.dashboard_token = "dash-secret"
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.include_router(dashboard_router)
    client = TestClient(app)
    assert client.get("/api/jobs").status_code == 401
    assert client.post("/api/jobs/job_missing/cancel").status_code == 401
    ok = client.get("/api/jobs", headers={"X-Creasy-Token": "dash-secret"})
    assert ok.status_code == 200
    bearer = client.get("/api/jobs", headers={"Authorization": "Bearer dash-secret"})
    assert bearer.status_code == 200
    manager.shutdown()


def test_dashboard_open_when_token_unset(tmp_config):
    assert tmp_config.dashboard_token == ""
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.include_router(dashboard_router)
    client = TestClient(app)
    assert client.get("/api/jobs").status_code == 200
    manager.shutdown()


def test_inject_token_then_public_url_has_no_userinfo():
    raw = "https://gitlab.example/group/repo.git"
    authed = inject_token(raw, "super-secret-token")
    assert "oauth2:super-secret-token@" in authed
    clean = public_git_url(authed)
    assert "super-secret-token" not in clean
    assert clean == raw


def test_redact_userinfo_strips_oauth_token():
    token = "super-secret-gitlab-token-TESTONLY"
    raw = f"fatal: Authentication failed for 'https://oauth2:{token}@gitlab.example/repo.git/'"
    got = redact_userinfo(raw)
    assert token not in got
    assert "oauth2:" not in got
    assert "https://gitlab.example/repo.git/" in got


def test_run_git_does_not_log_or_raise_oauth_token():
    import io

    token = "super-secret-gitlab-token-TESTONLY"
    url = f"https://oauth2:{token}@127.0.0.1:1/group/repo.git"
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    log = logging.getLogger("creasy.gitops")
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        try:
            _run_git(["clone", "--no-single-branch", url, "dest"], timeout=5)
        except GitError as exc:
            assert token not in str(exc)
            assert "oauth2:" not in str(exc)
    finally:
        log.removeHandler(handler)
    text = buf.getvalue()
    assert token not in text
    assert "oauth2:" not in text
    assert "https://127.0.0.1:1/group/repo.git" in text


def test_scrub_origin_removes_userinfo(tmp_path):
    dest = tmp_path / "repo"
    dest.mkdir()
    subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
    secret = "https://oauth2:leaked-token@gitlab.example/group/repo.git"
    subprocess.run(
        ["git", "remote", "add", "origin", secret],
        cwd=dest,
        check=True,
        capture_output=True,
    )
    _scrub_origin(dest, isolated_git_env(), timeout=30)
    got = subprocess.run(
        ["git", "config", "--local", "--get", "remote.origin.url"],
        cwd=dest,
        check=True,
        capture_output=True,
        text=True,
    )
    url = (got.stdout or "").strip()
    assert "leaked-token" not in url
    assert "oauth2" not in url
    assert url.endswith("gitlab.example/group/repo.git")


def test_posted_templates_are_not_command_tokens():
    job = _job(text="looks fine", error_message="boom", model="opencode/x")
    for body in (format_success(job), format_failure(job), format_cancelled(job)):
        assert first_command(body) is None


class _RejectSessionHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _json(self, payload, status=200) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/session/ses_old":
            self._json({"error": "missing"}, status=404)
            return
        if path == "/session/ses_known":
            self._json({"id": "ses_known"})
            return
        self._json({"error": "missing"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/session":
            self._json({"id": "ses_fresh"})
            return
        self._json({"ok": True})


def test_resume_or_create_rejected_id_opens_new_session():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _RejectSessionHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    client = OpenCodeClient(f"http://127.0.0.1:{port}", "C:/tmp/clone")
    try:
        sid, created = client.resume_or_create("ses_old", title="creasy 1-1")
        assert created is True
        assert sid == "ses_fresh"
        same, created_again = client.resume_or_create("ses_known", title="x")
        assert created_again is False
        assert same == "ses_known"
    finally:
        client.close()
        httpd.shutdown()
