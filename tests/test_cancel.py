from __future__ import annotations

import time

from creasy.gitlab.events import CleanupTrigger, ReviewTrigger
from creasy.jobs.manager import Manager
from creasy.workspace.store import WorkspaceRecord
from conftest import FakeRunner


def _trig(kind="review", comment="", iid=3) -> ReviewTrigger:
    return ReviewTrigger(
        kind=kind,
        project_id=9,
        mr_iid=iid,
        explicit=True,
        comment_text=comment,
        source_branch="feat",
        target_branch="main",
    )


def test_submit_ignored_while_mr_is_closing(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    manager._draining_mr.add("9-3")
    ack, job, message = manager.submit(_trig("review", "after close"))
    assert ack == "ignored"
    assert job is None
    assert "closing" in message
    manager.shutdown()


def test_close_does_not_start_comment_arriving_during_cleanup(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    dest = tmp_config.work_dir / "9-12"
    dest.mkdir(parents=True)
    manager.workspaces.save(
        WorkspaceRecord(mr_key="9-12", project_id=9, mr_iid=12, clone_path=str(dest))
    )
    manager.submit(_trig("review", "first", iid=12))
    assert runner.started.wait(2)
    manager._draining_mr.add("9-12")
    ack, job, _ = manager.submit(_trig("ask", "during close", iid=12))
    assert ack == "ignored"
    assert job is None
    manager.cleanup_mr(CleanupTrigger(project_id=9, mr_iid=12, action="close"))
    assert "9-12" not in manager._draining_mr
    runner.release.set()
    manager.shutdown()


def test_cancel_queued_leaves_runner(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    manager.submit(_trig("review", "first"))
    assert runner.started.wait(2)
    _, queued, _ = manager.submit(_trig("ask", "second"))
    assert queued is not None
    ok, msg = manager.cancel_job(queued.job_id)
    assert ok
    job = manager.store.get(queued.job_id)
    assert job is not None
    assert job.status == "cancelled"
    still = manager.store.running_for_mr("9-3")
    assert still is not None
    assert still.status == "running"
    runner.release.set()
    manager.shutdown()


def test_cancel_running_starts_next(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    _, first, _ = manager.submit(_trig("review", "r"))
    assert runner.started.wait(2)
    manager.submit(_trig("ask", "q"))
    runner.started.clear()
    manager.cancel_job(first.job_id)
    deadline = time.time() + 3
    while time.time() < deadline and "ask:" not in "".join(runner.runs):
        time.sleep(0.05)
        runner.release.set()
    assert any(r.startswith("ask") for r in runner.runs)
    manager.shutdown()


def test_cancel_all_keeps_clone_dir(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    dest = tmp_config.work_dir / "9-4"
    dest.mkdir(parents=True)
    (dest / "keep.txt").write_text("x", encoding="utf-8")
    manager.submit(_trig("review", iid=4))
    assert runner.started.wait(2)
    manager.submit(_trig("ask", "later", iid=4))
    count, key = manager.cancel_mr(9, 4, delete_clone_dir=False)
    assert key == "9-4"
    assert count >= 1
    assert dest.exists()
    runner.release.set()
    manager.shutdown()


def test_merge_runs_osm_delete_cascade(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    dest = tmp_config.work_dir / "9-5"
    dest.mkdir(parents=True)
    (dest / "tree.txt").write_text("clone", encoding="utf-8")
    manager.workspaces.save(
        WorkspaceRecord(mr_key="9-5", project_id=9, mr_iid=5, clone_path=str(dest))
    )
    manager.cleanup_mr(CleanupTrigger(project_id=9, mr_iid=5, action="merge"))
    assert not dest.exists()
    assert manager.workspaces.get("9-5") is None
    manager.shutdown()


def test_close_also_deletes_clone(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    dest = tmp_config.work_dir / "9-6"
    dest.mkdir(parents=True)
    (dest / "tree.txt").write_text("clone", encoding="utf-8")
    manager.workspaces.save(
        WorkspaceRecord(mr_key="9-6", project_id=9, mr_iid=6, clone_path=str(dest))
    )
    manager.cleanup_mr(CleanupTrigger(project_id=9, mr_iid=6, action="close"))
    assert not dest.exists()
    manager.shutdown()


def test_close_does_not_start_queued_job(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    dest = tmp_config.work_dir / "9-8"
    dest.mkdir(parents=True)
    (dest / "tree.txt").write_text("clone", encoding="utf-8")
    manager.workspaces.save(
        WorkspaceRecord(mr_key="9-8", project_id=9, mr_iid=8, clone_path=str(dest))
    )
    _, first, _ = manager.submit(_trig("review", "first", iid=8))
    assert runner.started.wait(2)
    _, second, _ = manager.submit(_trig("ask", "second", iid=8))
    assert second is not None
    manager.cleanup_mr(CleanupTrigger(project_id=9, mr_iid=8, action="merge"))
    runner.release.set()
    deadline = time.time() + 2
    while time.time() < deadline:
        time.sleep(0.05)
    saved = manager.store.get(second.job_id)
    assert saved is not None
    assert saved.status == "cancelled"
    assert not any(r.startswith("ask:") for r in runner.runs)
    assert manager.queue.queued_ids("9-8") == []
    first_saved = manager.store.get(first.job_id)
    assert first_saved is not None
    assert first_saved.status != "queued"
    manager.shutdown()


def test_cancel_all_does_not_start_queued_job(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    _, first, _ = manager.submit(_trig("review", "first", iid=9))
    assert runner.started.wait(2)
    _, second, _ = manager.submit(_trig("ask", "second", iid=9))
    assert second is not None
    manager.cancel_mr(9, 9, delete_clone_dir=False)
    runner.release.set()
    deadline = time.time() + 2
    while time.time() < deadline:
        time.sleep(0.05)
    saved = manager.store.get(second.job_id)
    assert saved is not None
    assert saved.status == "cancelled"
    assert not any(r.startswith("ask:") for r in runner.runs)
    assert manager.queue.queued_ids("9-9") == []
    manager.shutdown()


def test_cancel_queued_keeps_later_fifo(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    manager.submit(_trig("review", "first", iid=10))
    assert runner.started.wait(2)
    _, second, _ = manager.submit(_trig("ask", "second", iid=10))
    _, third, _ = manager.submit(_trig("ask", "third", iid=10))
    assert second is not None and third is not None
    ok, _ = manager.cancel_job(second.job_id)
    assert ok
    assert manager.store.get(second.job_id).status == "cancelled"
    assert manager.queue.queued_ids("9-10") == [third.job_id]
    runner.started.clear()
    runner.release.set()
    deadline = time.time() + 3
    while time.time() < deadline and "ask:third" not in runner.runs:
        time.sleep(0.05)
        runner.release.set()
    assert "ask:third" in runner.runs
    assert "ask:second" not in runner.runs
    manager.shutdown()


def test_stale_queue_head_is_skipped_not_next_id(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    manager.submit(_trig("review", "first", iid=11))
    assert runner.started.wait(2)
    _, second, _ = manager.submit(_trig("ask", "second", iid=11))
    _, third, _ = manager.submit(_trig("ask", "third", iid=11))
    assert second is not None and third is not None
    stuck = manager.store.get(second.job_id)
    assert stuck is not None
    stuck.status = "cancelled"
    stuck.live = False
    manager.store.save(stuck)
    assert manager.queue.peek("9-11") == second.job_id
    runner.started.clear()
    runner.release.set()
    deadline = time.time() + 3
    while time.time() < deadline and "ask:third" not in runner.runs:
        time.sleep(0.05)
        runner.release.set()
    assert "ask:third" in runner.runs
    assert "ask:second" not in runner.runs
    assert second.job_id not in manager.queue.queued_ids("9-11")
    manager.shutdown()


def test_shutdown_does_not_delete_clone(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    dest = tmp_config.work_dir / "9-7"
    dest.mkdir(parents=True)
    (dest / "keep.txt").write_text("x", encoding="utf-8")
    manager.submit(_trig("review", iid=7))
    assert runner.started.wait(2)
    manager.shutdown()
    assert dest.exists()
