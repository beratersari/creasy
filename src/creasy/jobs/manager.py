from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable, Optional

from creasy.config import Config
from creasy.gitlab.events import CleanupTrigger, ReviewTrigger
from creasy.jobs.models import JobRecord, mint_job_id, utc_now
from creasy.jobs.queue import JobQueue
from creasy.jobs.store import JobStore
from creasy.jobs.worker import JobRunner, RunResult
from creasy.cleanup.end import delete_clone_path, protect_pids, stop_job_holders
from creasy.cleanup.kill import kill_job_tree, reap_work_dir
from creasy.logging import get_logger
from creasy.workspace.identity import clone_path_for, mr_key
from creasy.workspace.store import WorkspaceStore

logger = get_logger("manager")


class Manager:
    def __init__(
        self,
        config: Config,
        runner: JobRunner,
        store: Optional[JobStore] = None,
        queue: Optional[JobQueue] = None,
        workspaces: Optional[WorkspaceStore] = None,
    ) -> None:
        self.config = config
        self.runner = runner
        self.store = store or JobStore(config.job_dir)
        self.queue = queue or JobQueue(config.data_dir / "queue.json")
        self.workspaces = workspaces or WorkspaceStore(config.data_dir / "workspace_meta")
        self.ready = False
        self.stopping = False
        self._lock = threading.RLock()
        self._running = 0
        self._running_mr: set[str] = set()
        self._cancel: dict[str, threading.Event] = {}
        self._threads: list[threading.Thread] = []

    def boot(self) -> None:
        leftover = [j for j in self.store.list_all() if j.status in {"queued", "running"}]
        pids: list[Optional[int]] = []
        for job in leftover:
            pids.extend([job.serve_pid, *list(job.extra_pids or [])])
        if pids:
            kill_job_tree(pids)
        try:
            reap_work_dir(self.config.work_dir, protect={os.getpid()})
        except Exception:  # noqa: BLE001
            logger.exception("boot reap_work_dir failed")
        for job in leftover:
            if job.status == "running":
                self._finish(
                    job,
                    RunResult(error="process restarted; leftover job was not resumed"),
                    status="error",
                )
            elif job.status == "queued":
                self.queue.enqueue(job.mr_key, job.job_id)
        # queued leftovers stay in the persisted queue and will dispatch
        self.ready = True
        logger.info("boot finished leftover_running_failed=%s", len([j for j in leftover if j.status == "running"]))
        self._dispatch()

    def shutdown(self) -> None:
        self.stopping = True
        self.ready = False
        with self._lock:
            events = list(self._cancel.values())
        for event in events:
            event.set()
        live = [j for j in self.store.list_all() if j.status in {"queued", "running"}]
        guarded = protect_pids()
        for job in live:
            if job.status == "running":
                clone = Path(job.clone_path) if job.clone_path else None
                try:
                    stop_job_holders(job, clone, protect=guarded)
                except Exception:  # noqa: BLE001
                    logger.exception("shutdown stop_job_holders failed job=%s", job.job_id)
            elif job.status == "queued":
                self.queue.remove(job.mr_key, job.job_id)
                self._finish(job, RunResult(error="manager shutting down", cancelled=True), status="cancelled")
        for thread in list(self._threads):
            thread.join(timeout=15)
        leftover = [j for j in self.store.list_all() if j.status == "running"]
        for job in leftover:
            self._finish(job, RunResult(error="manager shutting down", cancelled=True), status="cancelled")

    def submit(self, trigger: ReviewTrigger) -> tuple[str, JobRecord | None, str]:
        """Return (ack, job, message). ack is accepted|queued|ignored."""
        if not self.ready or self.stopping:
            return "ignored", None, "manager is not accepting jobs"
        key = mr_key(trigger.project_id, trigger.mr_iid)
        with self._lock:
            running = self.store.running_for_mr(key)
            queued_ids = self.queue.queued_ids(key)
            if not trigger.explicit and (running or queued_ids):
                logger.info("skip auto %s for %s (already busy)", trigger.kind, key)
                return "ignored", None, "MR already has a running or queued job"
            job = JobRecord(
                job_id=mint_job_id(),
                mr_key=key,
                project_id=trigger.project_id,
                mr_iid=trigger.mr_iid,
                trigger=trigger.kind,
                status="queued",
                live=True,
                explicit=trigger.explicit,
                comment_text=trigger.comment_text,
                source_branch=trigger.source_branch,
                target_branch=trigger.target_branch,
                sha=trigger.sha,
                web_url=trigger.web_url,
                model=self.config.opencode_model,
                agent=self.config.opencode_agent,
                accepted_at=utc_now(),
                log_file=f"{key}-{mint_job_id()}.log",
            )
            # fix log file to use job id
            job.log_file = f"{key}-{job.job_id}.log"
            self.store.save(job)
            self.queue.enqueue(key, job.job_id)
            started = self._try_start_locked(key)
        ack = "accepted" if started else "queued"
        logger.info("%s %s job=%s trigger=%s", ack, key, job.job_id, trigger.kind)
        return ack, job, f"{ack} {job.job_id}"

    def cleanup_mr(self, trigger: CleanupTrigger) -> None:
        key = mr_key(trigger.project_id, trigger.mr_iid)
        logger.info("cleanup %s action=%s", key, trigger.action)
        # OSM cascade. Trigger is MR close/merge, not job end.
        self.cancel_mr(
            trigger.project_id,
            trigger.mr_iid,
            delete_clone_dir=True,
            delete_reason=f"mr-{trigger.action}",
        )

    def cancel_job(self, job_id: str) -> tuple[bool, str]:
        job = self.store.get(job_id)
        if not job:
            return False, "not found"
        if job.status not in {"queued", "running"}:
            return False, f"job is {job.status}"
        if job.status == "queued":
            self.queue.remove(job.mr_key, job.job_id)
            self._finish(job, RunResult(cancelled=True, error="cancelled"), status="cancelled")
            return True, "cancelled queued job"
        event = self._cancel.get(job.job_id)
        if event:
            event.set()
        clone = Path(job.clone_path) if job.clone_path else None
        try:
            stop_job_holders(job, clone, protect=protect_pids())
        except Exception:  # noqa: BLE001
            logger.exception("cancel stop_job_holders failed job=%s", job.job_id)
            kill_job_tree([job.serve_pid, *list(job.extra_pids or [])])
        return True, "cancel requested"

    def cancel_mr(
        self,
        project_id: int,
        mr_iid: int,
        *,
        delete_clone_dir: bool = False,
        delete_reason: str = "mr-merge",
    ) -> tuple[int, str]:
        key = mr_key(project_id, mr_iid)
        cancelled = 0
        running = self.store.running_for_mr(key)
        if running:
            ok, _ = self.cancel_job(running.job_id)
            if ok:
                cancelled += 1
        for job_id in self.queue.drain(key):
            job = self.store.get(job_id)
            if job and job.status == "queued":
                self._finish(job, RunResult(cancelled=True, error="cancelled"), status="cancelled")
                cancelled += 1
        if delete_clone_dir:
            if running:
                for thread in list(self._threads):
                    if thread.name == running.job_id:
                        thread.join(timeout=15)
                        break
            record = self.workspaces.get(key)
            path = Path(record.clone_path) if record and record.clone_path else clone_path_for(self.config.work_dir, key)
            holders = running
            if holders is None:
                holders = JobRecord(
                    job_id="cleanup",
                    mr_key=key,
                    project_id=project_id,
                    mr_iid=mr_iid,
                    trigger="review",
                )
            try:
                stop_job_holders(holders, path, protect=protect_pids())
            except Exception:  # noqa: BLE001
                logger.exception("stop_job_holders failed %s", key)
            try:
                delete_clone_path(path, reason=delete_reason)
            except Exception as exc:  # noqa: BLE001
                logger.warning("delete clone failed %s: %s", key, exc)
            self.workspaces.delete(key)
        return cancelled, key

    def _try_start_locked(self, mr_key_value: str) -> bool:
        if self.stopping or not self.ready:
            return False
        if mr_key_value in self._running_mr:
            return False
        if self._running >= self.config.max_concurrent_jobs:
            return False
        job_id = self.queue.peek(mr_key_value)
        if not job_id:
            return False
        job = self.store.get(job_id)
        if not job or job.status != "queued":
            self.queue.pop(mr_key_value)
            return False
        self.queue.pop(mr_key_value)
        self._running += 1
        self._running_mr.add(mr_key_value)
        event = threading.Event()
        self._cancel[job.job_id] = event
        job.status = "running"
        job.live = True
        job.started_at = utc_now()
        self.store.save(job)
        thread = threading.Thread(target=self._run_job, args=(job.job_id, event), name=job.job_id, daemon=True)
        self._threads.append(thread)
        thread.start()
        return True

    def _dispatch(self) -> None:
        with self._lock:
            # prefer MRs that are idle
            keys = {item["mr_key"] for item in self.queue.public_items()}
            for key in list(keys):
                if self._running >= self.config.max_concurrent_jobs:
                    break
                self._try_start_locked(key)

    def _run_job(self, job_id: str, event: threading.Event) -> None:
        job = self.store.get(job_id)
        if not job:
            self._after_job(None)
            return
        try:
            result = self.runner.run(job, event.is_set)
        except Exception as exc:  # noqa: BLE001
            logger.exception("runner crashed job=%s", job_id)
            result = RunResult(error=f"worker crashed: {exc}")
        job = self.store.get(job_id) or job
        if event.is_set() or result.cancelled:
            status = "cancelled"
        elif result.timeout:
            status = "timeout"
        elif result.error and not result.text:
            status = "error"
        else:
            status = "success"
        self._finish(job, result, status=status)
        self._after_job(job.mr_key)

    def _after_job(self, mr_key_value: Optional[str]) -> None:
        with self._lock:
            self._running = max(0, self._running - 1)
            if mr_key_value:
                self._running_mr.discard(mr_key_value)
        if mr_key_value:
            with self._lock:
                self._try_start_locked(mr_key_value)
        self._dispatch()

    def _finish(self, job: JobRecord, result: RunResult, *, status: str) -> None:
        job.status = status  # type: ignore[assignment]
        job.live = False
        job.completed_at = utc_now()
        job.text = result.text or job.text
        job.error_message = result.error or None
        if result.session_id:
            job.session_id = result.session_id
        if result.clone_path:
            job.clone_path = result.clone_path
        if result.merge_base:
            job.merge_base = result.merge_base
        if result.sha:
            job.sha = result.sha
        if result.diff_stat:
            job.diff_stat = result.diff_stat
        if result.changed_paths is not None:
            job.changed_paths = result.changed_paths
        if result.chat_snapshot is not None:
            job.chat_snapshot = result.chat_snapshot
        if result.serve_pid:
            job.serve_pid = result.serve_pid
        if result.serve_port:
            job.serve_port = result.serve_port
        self.store.save(job)
        self._cancel.pop(job.job_id, None)
        logger.info("job %s finished status=%s", job.job_id, status)

    def health(self) -> dict:
        jobs = self.store.list_all()
        return {
            "ready": self.ready,
            "running": sum(1 for j in jobs if j.status == "running"),
            "queued": sum(1 for j in jobs if j.status == "queued"),
            "workspaces": len(self.workspaces.list_all()),
        }
