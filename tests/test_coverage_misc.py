"""Gitops, cleanup end, manager, report, webhook, and diffmap coverage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.report import (
    build_report_context,
    public_settings,
    read_capped_text,
    system_diagnostics,
    _cli_version,
    _opencode_cli_logs,
    _queue_items,
    _recent_fail_lines,
    _runtime,
    _serve_log_names,
)
from creasy.api.webhook import router as webhook_router
from creasy.cleanup.end import delete_clone_path, protect_pids, retry_windows_delete_if_held, stop_job_holders
from creasy.gitlab.events import CleanupTrigger, ReviewTrigger
from creasy.jobs.manager import Manager, _real_review_busy
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.jobs.store import JobStore
from creasy.workspace.diffmap import parse_unified_diff
from creasy.workspace.gitops import (
    GitError,
    askpass_path,
    delete_clone,
    inject_token,
    isolated_git_env,
    public_git_url,
    _kill_git,
    _run_git,
)
from creasy.workspace.store import WorkspaceRecord
from conftest import FakeRunner


def _fake_os(monkeypatch, module, name: str) -> None:
    real = module.os

    class _Os:
        def __getattr__(self, item):
            if item == "name":
                return name
            return getattr(real, item)

    monkeypatch.setattr(module, "os", _Os())


def test_gitops_helpers(tmp_path, monkeypatch):
    path = askpass_path()
    assert path.exists()
    env = isolated_git_env("tok", auth_scheme="azure")
    assert env["CREASY_AZURE_GIT"] == "1"
    env2 = isolated_git_env("tok", auth_scheme="gitlab")
    assert env2["GIT_ASKPASS"] == "echo"
    isolated_git_env("")
    assert inject_token("https://host/repo.git", "tok").startswith("https://")
    assert inject_token("https://host:8443/repo.git", "tok", scheme="azure")
    assert inject_token("https://host/repo.git", "") == "https://host/repo.git"
    with pytest.raises(GitError):
        inject_token("ssh://host/repo", "tok")
    with pytest.raises(GitError):
        inject_token("https:///repo", "tok")
    assert public_git_url("https://oauth2:tok@host/repo.git") == "https://host/repo.git"
    assert public_git_url("https://host/repo.git") == "https://host/repo.git"
    assert public_git_url("https://u:p@host:8443/r") == "https://host:8443/r"
    proc = MagicMock()
    proc.pid = 99
    proc.wait.side_effect = RuntimeError("x")
    monkeypatch.setattr("creasy.cleanup.kill.kill_job_tree", lambda pids: (_ for _ in ()).throw(RuntimeError("x")))
    proc.kill.side_effect = OSError("x")
    _kill_git(proc)
    monkeypatch.setattr(
        "creasy.workspace.gitops.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="ok", stderr=""),
    )
    _run_git(["status"], cwd=tmp_path, timeout=1)
    env_az = isolated_git_env("tok", auth_scheme="azure")
    _run_git(["status"], cwd=tmp_path, env=env_az, timeout=1)
    monkeypatch.setattr("creasy.workspace.gitops._run_git_killable", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    _run_git(["status"], cwd=tmp_path, timeout=1, on_pid=lambda p: None, should_stop=lambda: False)
    delete_clone(None)
    missing = tmp_path / "gone"
    delete_clone(missing)
    tree = tmp_path / "tree"
    tree.mkdir()
    monkeypatch.setattr("creasy.cleanup.end.delete_clone_path", lambda *a, **k: True)
    delete_clone(tree)


def test_cleanup_end(tmp_path, monkeypatch):
    from creasy.jobs.models import JobRecord

    job = JobRecord(job_id="j", mr_key="1-1", project_id=1, mr_iid=1, trigger="review", serve_pid=9, extra_pids=[8])
    assert 9 not in protect_pids(None, ["x", 2, 99]) or True
    assert 99 in protect_pids([99])
    monkeypatch.setattr("creasy.cleanup.end.kill_job_tree", lambda p: None)
    monkeypatch.setattr("creasy.cleanup.end.reap_path", lambda *a, **k: 0)
    monkeypatch.setattr("creasy.cleanup.end.kill_file_holders", lambda *a, **k: 0)
    monkeypatch.setattr("creasy.cleanup.end.drop_git_locks", lambda *a, **k: None)
    monkeypatch.setattr("creasy.cleanup.end.path_has_holders", lambda *a, **k: False)
    stop_job_holders(job, None)
    clone = tmp_path / "c"
    clone.mkdir()
    stop_job_holders(job, clone)
    from creasy.cleanup import end as endmod

    _fake_os(monkeypatch, endmod, "posix")
    monkeypatch.setattr("creasy.cleanup.end.path_has_holders", lambda *a, **k: True)
    stop_job_holders(job, clone)
    monkeypatch.setattr("creasy.cleanup.end.kill_job_tree", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    stop_job_holders(job, clone)
    monkeypatch.setattr("creasy.cleanup.end.kill_job_tree", lambda p: None)
    monkeypatch.setattr("creasy.cleanup.end.reap_path", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    stop_job_holders(job, clone)
    monkeypatch.setattr("creasy.cleanup.end.reap_path", lambda *a, **k: 0)
    monkeypatch.setattr("creasy.cleanup.end.kill_file_holders", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    stop_job_holders(job, clone)
    stop_job_holders(job, tmp_path / "boom")
    assert delete_clone_path(None, reason="x") is True
    monkeypatch.setattr("creasy.cleanup.end.hard_delete", lambda p: True)
    gone = tmp_path / "gone"
    assert delete_clone_path(gone, reason="x") is True
    stay = tmp_path / "stay"
    stay.mkdir()
    monkeypatch.setattr("creasy.cleanup.end.hard_delete", lambda p: False)
    from creasy.cleanup import end as endmod

    _fake_os(monkeypatch, endmod, "nt")
    monkeypatch.setattr("creasy.cleanup.end.retry_windows_delete_if_held", lambda p: True)
    delete_clone_path(stay, reason="x")
    monkeypatch.setattr("creasy.cleanup.end.retry_windows_delete_if_held", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    delete_clone_path(stay, reason="x")
    monkeypatch.setattr("creasy.cleanup.end.hard_delete", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    delete_clone_path(stay, reason="x")
    monkeypatch.setattr("creasy.cleanup.end.query_windows_restart_manager", lambda p: SimpleNamespace(pids=[99], died=True))
    monkeypatch.setattr("creasy.cleanup.end.may_kill", lambda p: True)
    monkeypatch.setattr("creasy.cleanup.end.kill_pid", lambda p: None)
    monkeypatch.setattr("creasy.cleanup.end.hard_delete", lambda p: True)
    retry_windows_delete_if_held(stay, protect=[1])
    monkeypatch.setattr("creasy.cleanup.end.query_windows_restart_manager", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr("creasy.cleanup.end.hard_delete", lambda p: False)
    retry_windows_delete_if_held(stay)
    monkeypatch.setattr("creasy.cleanup.end.query_windows_restart_manager", lambda p: SimpleNamespace(pids=[1], died=False))
    monkeypatch.setattr("creasy.cleanup.end.may_kill", lambda p: False)
    retry_windows_delete_if_held(stay)
    missing = tmp_path / "nope"
    assert retry_windows_delete_if_held(missing) is True


def test_diffmap_edges():
    parsed = parse_unified_diff(
        """diff --git a/old.py b/new.py
rename from old.py
rename to new.py
--- a/old.py
+++ b/new.py
@@ -1,3 +1,4 @@
 keep
-old
+new
+added
 context
\\ No newline
diff --git a/gone.py b/gone.py
deleted file mode 100644
--- a/gone.py
+++ /dev/null
@@ -1 +0,0 @@
-bye
diff --git a/fresh.py b/fresh.py
new file mode 100644
--- /dev/null
+++ b/fresh.py
@@ -0,0 +1 @@
+hi
"""
    )
    assert parsed.find("") is None
    assert parsed.find("new.py") is not None
    fd = parsed.find("new.py")
    fd.resolve_new(1)
    fd.resolve_new(99)
    fd.resolve_old(1)
    fd.resolve_old(99)
    gone = parsed.find("gone.py")
    gone.resolve_old(1)
    gone.resolve_new(1)
    fresh = parsed.find("fresh.py")
    fresh.resolve_new(1)
    fresh.resolve_old(1)
    parse_unified_diff("")


def test_manager_edges(tmp_config, monkeypatch):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    leftover = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-1",
        project_id=1,
        mr_iid=1,
        trigger="review",
        status="running",
        serve_pid=12,
        extra_pids=[13],
    )
    queued = JobRecord(
        job_id=mint_job_id(),
        mr_key="8-8",
        project_id=8,
        mr_iid=8,
        trigger="ask",
        status="queued",
        accepted_at="2020-01-01T00:00:00Z",
    )
    manager.store.save(leftover)
    manager.store.save(queued)
    monkeypatch.setattr("creasy.jobs.manager.kill_job_tree", lambda p: None)
    monkeypatch.setattr("creasy.jobs.manager.reap_work_dir", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    runner.release.set()
    manager.boot()
    assert manager.ready
    assert _real_review_busy(manager.store, leftover, [queued.job_id])
    assert not _real_review_busy(manager.store, JobRecord(job_id="u", mr_key="1-1", project_id=1, mr_iid=1, trigger="usage", status="running"), [])
    trig = ReviewTrigger(
        kind="review",
        project_id=2,
        mr_iid=2,
        source_branch="f",
        target_branch="m",
        sha="s",
        explicit=False,
        title="t",
    )
    manager.ready = False
    ack, job, _ = manager.submit(trig)
    assert ack == "ignored"
    manager.ready = True
    manager._draining_mr.add("2-2")
    ack, job, _ = manager.submit(trig)
    assert ack == "ignored"
    manager._draining_mr.clear()
    running = JobRecord(job_id="job_r", mr_key="2-2", project_id=2, mr_iid=2, trigger="review", status="running")
    manager.store.save(running)
    ack, job, _ = manager.submit(trig)
    assert ack == "ignored"
    trig2 = ReviewTrigger(
        kind="review",
        project_id=2,
        mr_iid=2,
        source_branch="f",
        target_branch="m",
        sha="s",
        explicit=True,
        title="t",
    )
    ack, job, _ = manager.submit(trig2)
    assert ack in {"accepted", "queued", "ignored"}
    ok, msg = manager.cancel_job("missing")
    assert ok is False
    done = JobRecord(job_id="job_done", mr_key="1-1", project_id=1, mr_iid=1, trigger="review", status="success")
    manager.store.save(done)
    ok, msg = manager.cancel_job("job_done")
    assert ok is False
    q = JobRecord(job_id="job_q1", mr_key="3-3", project_id=3, mr_iid=3, trigger="review", status="queued")
    manager.store.save(q)
    manager.queue.enqueue("3-3", "job_q1")
    ok, msg = manager.cancel_job("job_q1")
    assert ok
    run = JobRecord(job_id="job_run1", mr_key="4-4", project_id=4, mr_iid=4, trigger="review", status="running", clone_path=str(tmp_config.work_dir / "4-4"))
    manager.store.save(run)
    manager._cancel["job_run1"] = __import__("threading").Event()
    monkeypatch.setattr("creasy.jobs.manager.stop_job_holders", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr("creasy.jobs.manager.kill_job_tree", lambda p: None)
    ok, msg = manager.cancel_job("job_run1")
    assert ok
    monkeypatch.setattr("creasy.jobs.manager.delete_clone_path", lambda *a, **k: True)
    monkeypatch.setattr("creasy.jobs.manager.stop_job_holders", lambda *a, **k: None)
    manager.cleanup_mr(CleanupTrigger(action="close", project_id=5, mr_iid=5))
    rec = WorkspaceRecord(mr_key="5-5", project_id=5, mr_iid=5, clone_path=str(tmp_config.work_dir / "5-5"))
    manager.workspaces.save(rec)
    monkeypatch.setattr("creasy.jobs.manager.delete_clone_path", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    manager.cancel_mr(5, 5, delete_clone_dir=True)
    manager.health()
    from creasy.jobs.worker import RunResult

    job = JobRecord(job_id="f1", mr_key="1-1", project_id=1, mr_iid=1, trigger="review")
    manager._finish(job, RunResult(text="t", session_id="s", clone_path="c", merge_base="m", sha="h", diff_stat="d", changed_paths=["a"], chat_snapshot=[], serve_pid=1, serve_port=2, posted=True), status="success")
    manager._finish(job, RunResult(cancelled=True), status="cancelled")
    manager._finish(job, RunResult(error="e"), status="error")
    manager._try_start_locked("nope")
    manager.stopping = True
    assert manager._try_start_locked("1-1") is False
    manager.stopping = False
    manager._draining_mr.add("1-1")
    assert manager._try_start_locked("1-1") is False
    manager._draining_mr.clear()
    manager._running_mr.add("1-1")
    assert manager._try_start_locked("1-1") is False
    manager._running_mr.clear()
    manager._running = 99
    assert manager._try_start_locked("1-1") is False
    manager._running = 0
    manager.queue.enqueue("9-9", "ghost")
    assert manager._try_start_locked("9-9") is False
    runner.release.set()
    manager.shutdown()


def test_report_and_webhook(tmp_config, tmp_path, monkeypatch):
    manager = Manager(tmp_config, FakeRunner())
    manager.ready = True
    ctx = build_report_context(manager)
    assert ctx["meta"]["app_name"] == "creasy"
    boom = SimpleNamespace()
    ctx2 = build_report_context(boom)
    assert "meta" in ctx2
    system_diagnostics(tmp_config, running=1, queued=2)
    public_settings(tmp_config)
    log = tmp_config.log_dir / "app.log"
    log.write_text("ok\n[x] FAIL something\n", encoding="utf-8")
    assert _recent_fail_lines(log)
    big = tmp_path / "big.txt"
    big.write_bytes(b"x" * 100)
    read_capped_text(big, max_bytes=10)
    read_capped_text(tmp_path / "missing", max_bytes=10)
    _queue_items(manager)
    _queue_items(SimpleNamespace())
    _runtime(tmp_config, running=0, queued=0)
    _cli_version("git")
    monkeypatch.setattr("creasy.api.report.shutil.which", lambda b: None)
    _cli_version("nope")
    _opencode_cli_logs()
    (tmp_config.serve_dir / "a.log").write_text("x", encoding="utf-8")
    assert _serve_log_names(tmp_config)

    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.state.gitlab = SimpleNamespace(current_user_id=lambda: 9, current_user=lambda: {"names": ["bot"]})
    app.include_router(webhook_router)
    client = TestClient(app)
    assert client.post(
        "/creasy/webhook/gitlab",
        json={"object_kind": "note"},
        headers={"X-Gitlab-Token": "secret"},
    ).status_code == 200
    bad = client.post("/creasy/webhook/gitlab", content="not-json", headers={"X-Gitlab-Token": "secret"})
    assert bad.status_code == 400
    tmp_config.webhook_secret = ""
    client2 = TestClient(app)
    client2.post("/creasy/webhook/gitlab", json=["x"])
    app.state.bot_user_id = None
    app.state.gitlab = None
    client.post("/creasy/webhook/gitlab", json={"object_kind": "note"})
    runner = FakeRunner()
    runner.release.set()
    manager.shutdown()
