from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

from creasy.config import Config
from creasy.gitlab.client import GitLabClient, GitLabError, MergeRequest
from creasy.gitlab.wipe import WipeCancelled, wipe_author_comments
from creasy.jobs.models import JobRecord
from creasy.cleanup.end import stop_job_holders
from creasy.logging import get_logger
from creasy.opencode.serve import ServeHandle, serve_log_path, start_serve, stop_serve
from creasy.opencode.session import OpenCodeClient, OpenCodeError, last_assistant_text, snapshot_chat
from creasy.jobs.store import JobStore
from creasy.review.findings import Finding, split_findings
from creasy.review.format import format_cancelled, format_failure, format_success
from creasy.review.position import build_position_variants, format_discussion
from creasy.review.similarity import should_skip_similar_reply
from creasy.review.threads import match_creasy_thread, parse_creasy_threads
from creasy.review.prompt import build_ask_prompt, build_review_prompt, hang_resume_prompt
from creasy.workspace.diffmap import parse_unified_diff
from creasy.workspace.gitops import (
    GitError,
    clone_repo,
    delete_clone,
    diff_stat,
    fetch_and_checkout,
    resolve_merge_base,
    unified_diff,
)
from creasy.workspace.identity import clone_path_for
from creasy.workspace.store import WorkspaceRecord, WorkspaceStore

logger = get_logger("worker")


@dataclass
class RunResult:
    text: str = ""
    session_id: str = ""
    error: str = ""
    timeout: bool = False
    cancelled: bool = False
    clone_path: str = ""
    merge_base: str = ""
    sha: str = ""
    diff_stat: str = ""
    changed_paths: list[str] | None = None
    chat_snapshot: list | None = None
    serve_pid: Optional[int] = None
    serve_port: Optional[int] = None
    posted: bool = False
    base_sha: str = ""
    start_sha: str = ""
    findings_posted: int = 0


class JobRunner(Protocol):
    def run(self, job: JobRecord, should_stop: Callable[[], bool]) -> RunResult: ...


class OpenCodeRunner:
    def __init__(
        self,
        config: Config,
        workspaces: WorkspaceStore,
        gitlab: Optional[GitLabClient] = None,
        store: Optional[JobStore] = None,
    ) -> None:
        self.config = config
        self.workspaces = workspaces
        self.gitlab = gitlab or GitLabClient(config.gitlab_url, config.gitlab_token)
        self.store = store

    def _remember_title(self, job: JobRecord, title: str) -> None:
        text = (title or "").strip()
        if not text or job.mr_title == text:
            return
        job.mr_title = text
        if self.store is None:
            return
        try:
            self.store.save(job)
        except Exception:  # noqa: BLE001
            logger.warning("could not persist mr_title job=%s", job.job_id)

    def _append_job_log(self, job: JobRecord, line: str) -> None:
        if not job.log_file:
            return
        path = self.config.log_dir / job.log_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line.rstrip() + "\n")
        except OSError as exc:
            logger.warning("job log write failed job=%s err=%s", job.job_id, exc)

    def _persist_job(self, job: JobRecord, what: str) -> None:
        if self.store is None:
            return
        try:
            self.store.save(job)
        except Exception:  # noqa: BLE001
            logger.warning("could not persist %s job=%s", what, job.job_id)

    def _record_spawn(self, job: JobRecord, handle: ServeHandle) -> None:
        job.serve_pid = handle.pid
        job.serve_port = handle.port
        job.serve_base_url = handle.base_url
        self._persist_job(job, "serve pid")
        logger.info("serve started pid=%s port=%s", handle.pid, handle.port)
        self._append_job_log(job, f"serve started pid={handle.pid} port={handle.port}")

    def _track_git_pid(self, job: JobRecord, pid: int) -> None:
        job.extra_pids = [pid] if pid else []
        self._persist_job(job, "extra pids")

    def _git_kw(self, job: JobRecord, should_stop: Callable[[], bool]) -> dict:
        return {"should_stop": should_stop, "on_pid": lambda pid: self._track_git_pid(job, pid)}

    def run(self, job: JobRecord, should_stop: Callable[[], bool]) -> RunResult:
        if job.trigger == "reset":
            return self._run_reset(job, should_stop)
        result = RunResult()
        handle: Optional[ServeHandle] = None
        client: Optional[OpenCodeClient] = None
        try:
            if should_stop():
                result.cancelled = True
                return result
            prior = self.workspaces.get(job.mr_key)
            previous_sha = prior.last_sha if prior else ""
            mr = self.gitlab.get_merge_request(job.project_id, job.mr_iid)
            self._remember_title(job, mr.title)
            workspace = self._ensure_workspace(job, mr, should_stop)
            result.clone_path = workspace.clone_path
            result.sha = workspace.last_sha or mr.sha
            result.base_sha = mr.base_sha
            result.start_sha = mr.start_sha or mr.base_sha
            clone = Path(workspace.clone_path)
            git_kw = self._git_kw(job, should_stop)
            merge_base = resolve_merge_base(
                clone,
                target_branch=mr.target_branch,
                preferred_base=mr.base_sha,
                timeout=min(60.0, self.config.git_timeout),
                **git_kw,
            )
            index = diff_stat(
                clone,
                merge_base,
                timeout=min(60.0, self.config.git_timeout),
                **git_kw,
            )
            result.merge_base = merge_base
            result.diff_stat = index.stat
            result.changed_paths = list(index.paths)
            job.clone_path = workspace.clone_path
            self._persist_job(job, "clone path")
            logger.info("diff stat for %s:\n%s", job.mr_key, index.stat)

            created_new = False
            prompt = self._prompt(
                job, mr, index, workspace, created_new=False, previous_sha=previous_sha
            )
            job.prompt = prompt
            self._persist_job(job, "prompt")
            handle = start_serve(
                bin_name=self.config.opencode_bin,
                cwd=clone,
                log_path=serve_log_path(self.config.serve_dir, job.job_id),
                timeout=self.config.serve_health_timeout,
                should_stop=should_stop,
                on_spawn=lambda spawned: self._record_spawn(job, spawned),
            )
            result.serve_pid = handle.pid
            result.serve_port = handle.port
            client = OpenCodeClient(handle.base_url, str(clone))
            session_id, created_new = client.resume_or_create(
                workspace.session_id or None,
                title=f"creasy {job.mr_key}",
            )
            try:
                client.list_messages(session_id)
            except OpenCodeError as exc:
                if exc.status_code == 400:
                    logger.warning("session %s unreadable; creating new", session_id)
                    session_id = client.create_session(title=f"creasy {job.mr_key}")
                    created_new = True
                else:
                    raise
            result.session_id = session_id
            job.session_id = session_id
            self._persist_job(job, "session id")
            if created_new and job.trigger == "ask":
                prompt = self._prompt(
                    job, mr, index, workspace, created_new=True, previous_sha=previous_sha
                )
                job.prompt = prompt
                self._persist_job(job, "ask prompt")
            last_error = ""
            text = ""
            original_posted = False
            for attempt in range(1, self.config.opencode_retry_count + 1):
                if should_stop():
                    result.cancelled = True
                    return result
                try:
                    if attempt > 1:
                        if not session_id.startswith("ses_"):
                            raise OpenCodeError("resume rejected; will not open a blank session")
                        got = client.get_session(session_id)
                        if got.status_code != 200:
                            raise OpenCodeError(f"resume rejected: HTTP {got.status_code}")
                    turn = prompt if not original_posted else hang_resume_prompt()
                    client.post_message(
                        session_id,
                        turn,
                        model=self.config.opencode_model,
                        agent=self.config.opencode_agent,
                    )
                    original_posted = True
                    text = client.wait_idle(
                        session_id,
                        timeout=self.config.opencode_timeout,
                        hang_timeout=self.config.hang_timeout,
                        should_stop=should_stop,
                    )
                    last_error = ""
                    break
                except OpenCodeError as exc:
                    last_error = str(exc)
                    result.timeout = exc.timeout
                    logger.warning("attempt %s ended: %s", attempt, exc)
                    self._append_job_log(job, f"attempt {attempt} ended: {exc}")
                    if should_stop() or attempt >= self.config.opencode_retry_count:
                        break
            if should_stop():
                result.cancelled = True
                return result
            try:
                messages = client.list_messages(session_id)
                result.chat_snapshot = snapshot_chat(messages, session_id)
            except Exception:
                messages = []
            if last_error:
                result.error = last_error
                result.text = ""
                workspace.session_id = session_id
                workspace.last_job_id = job.job_id
                self.workspaces.save(workspace)
                self._post_note(job, result)
                return result
            result.text = text or last_assistant_text(messages)
            markdown, findings = split_findings(result.text)
            result.text = markdown
            workspace.session_id = session_id
            workspace.last_job_id = job.job_id
            self.workspaces.save(workspace)
            self._post_note(job, result, findings=findings)
            return result
        except GitError as exc:
            if should_stop() or str(exc) == "cancelled":
                result.cancelled = True
            else:
                result.error = f"git failed: {exc}"
                self._post_note(job, result)
            return result
        except Exception as exc:  # noqa: BLE001
            if should_stop():
                result.cancelled = True
                return result
            logger.exception("worker failed job=%s", job.job_id)
            result.error = f"pipeline failed: {exc}"
            self._post_note(job, result)
            return result
        finally:
            if result.cancelled:
                self._post_note(job, result)
            if client is not None and result.session_id:
                try:
                    client.abort(result.session_id)
                except Exception:
                    pass
                client.close()
            if handle is not None:
                job.serve_pid = handle.pid
                job.serve_port = handle.port
                result.serve_pid = handle.pid
                result.serve_port = handle.port
            clone = Path(result.clone_path) if result.clone_path else None
            try:
                stop_job_holders(job, clone)
            except Exception:  # noqa: BLE001
                logger.exception("job-end stop_job_holders failed job=%s", job.job_id)
            stop_serve(handle)
            # Keep the clone. OSM deletes here; Creasy waits for MR close/merge.

    def _run_reset(self, job: JobRecord, should_stop: Callable[[], bool]) -> RunResult:
        """Delete PAT-authored notes/threads. No OpenCode, no clone, no MR note."""
        result = RunResult()
        if should_stop():
            result.cancelled = True
            return result
        author_id = self.gitlab.current_user_id()
        if author_id is None:
            result.error = "reset failed: could not resolve GITLAB_TOKEN user"
            return result
        try:
            stats = wipe_author_comments(
                self.gitlab,
                job.project_id,
                job.mr_iid,
                author_id,
                should_stop=should_stop,
            )
        except WipeCancelled:
            result.cancelled = True
            return result
        except GitLabError as exc:
            result.error = f"reset failed: {exc}"
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("reset wipe failed job=%s", job.job_id)
            result.error = f"reset failed: {exc}"
            return result
        workspace = self.workspaces.get(job.mr_key)
        if workspace is not None and workspace.session_id:
            workspace.session_id = ""
            self.workspaces.save(workspace)
        result.session_id = ""
        result.text = stats.summary()
        result.posted = True
        if stats.failed:
            logger.warning("reset partial job=%s %s", job.job_id, stats.summary())
        self._append_job_log(job, stats.summary())
        logger.info("reset %s %s", job.mr_key, stats.summary())
        return result

    def _prompt(
        self,
        job: JobRecord,
        mr: MergeRequest,
        index,
        workspace: WorkspaceRecord,
        *,
        created_new: bool,
        previous_sha: str = "",
    ) -> str:
        if job.trigger == "ask":
            current = workspace.last_sha or mr.sha
            sha_changed = bool(previous_sha and current and previous_sha != current)
            return build_ask_prompt(
                job.comment_text,
                mr=mr,
                index=index,
                sha_changed=sha_changed,
                previous_sha=previous_sha,
                include_context=created_new or not workspace.session_id,
            )
        return build_review_prompt(mr, index, extra_notes=job.comment_text)

    def _ensure_workspace(
        self,
        job: JobRecord,
        mr: MergeRequest,
        should_stop: Callable[[], bool],
    ) -> WorkspaceRecord:
        if should_stop():
            raise GitError("cancelled")
        dest = clone_path_for(self.config.work_dir, job.mr_key)
        record = self.workspaces.get(job.mr_key) or WorkspaceRecord(
            mr_key=job.mr_key,
            project_id=job.project_id,
            mr_iid=job.mr_iid,
        )
        http_url = mr.http_url or self.gitlab.resolve_http_url(job.project_id, record.http_url)
        if not http_url:
            raise GitError("no http repo url for project")
        git_kw = self._git_kw(job, should_stop)
        if not dest.exists() or not (dest / ".git").exists():
            if dest.exists():
                delete_clone(dest)
            clone_repo(
                http_url,
                dest,
                self.config.gitlab_token,
                timeout=self.config.git_timeout,
                **git_kw,
            )
        sha = fetch_and_checkout(
            dest,
            source_branch=mr.source_branch,
            target_branch=mr.target_branch,
            sha=mr.sha,
            token=self.config.gitlab_token,
            timeout=self.config.git_timeout,
            **git_kw,
        )
        record.clone_path = str(dest)
        record.source_branch = mr.source_branch
        record.target_branch = mr.target_branch
        record.last_sha = sha
        record.http_url = http_url
        record.web_url = mr.web_url
        record.last_job_id = job.job_id
        return self.workspaces.save(record)

    def _post_note(
        self,
        job: JobRecord,
        result: RunResult,
        *,
        findings: Optional[list[Finding]] = None,
    ) -> None:
        if result.posted:
            return
        shadow = job.model_copy(update={
            "text": result.text,
            "error_message": result.error or None,
            "model": self.config.opencode_model,
        })
        if result.cancelled:
            body = format_cancelled(shadow)
        elif result.error and not result.text:
            body = format_failure(shadow)
        else:
            body = format_success(shadow)
        try:
            self.gitlab.post_note(job.project_id, job.mr_iid, body)
            result.posted = True
        except Exception as exc:  # noqa: BLE001
            logger.error("post note failed: %s", exc)
            if not result.error:
                result.error = f"post note failed: {exc}"
            return
        if result.cancelled or (result.error and not result.text):
            return
        if findings:
            self._post_discussions(job, result, findings)

    def _post_discussions(
        self,
        job: JobRecord,
        result: RunResult,
        findings: list[Finding],
    ) -> None:
        poster = getattr(self.gitlab, "post_discussion", None)
        if not callable(poster):
            return
        clone = Path(result.clone_path) if result.clone_path else None
        if clone is None or not result.merge_base:
            logger.warning("skip discussions job=%s: no clone or merge-base", job.job_id)
            return
        try:
            diffmap = parse_unified_diff(
                unified_diff(clone, result.merge_base, timeout=min(60.0, self.config.git_timeout))
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("skip discussions job=%s: diff failed: %s", job.job_id, exc)
            return
        existing = self._existing_creasy_threads(job)
        used: set[str] = set()
        posted = 0
        replies = 0
        skipped = 0
        for finding in findings:
            variants = build_position_variants(
                finding,
                diffmap,
                base_sha=result.base_sha,
                start_sha=result.start_sha or result.base_sha,
                head_sha=result.sha,
            )
            if not variants:
                logger.warning(
                    "skip finding job=%s path=%s lines=%s-%s: no GitLab position",
                    job.job_id,
                    finding.path,
                    finding.start_line,
                    finding.end_line,
                )
                continue
            body = format_discussion(finding)
            matched = match_creasy_thread(finding, existing, used)
            if matched and should_skip_similar_reply(body, matched.last_body):
                used.add(matched.discussion_id)
                skipped += 1
                logger.info(
                    "skip similar reply job=%s discussion=%s path=%s",
                    job.job_id,
                    matched.discussion_id,
                    finding.path,
                )
                continue
            if matched and self._reply_finding(job, matched.discussion_id, body):
                used.add(matched.discussion_id)
                posted += 1
                replies += 1
                continue
            last_error = ""
            for position in variants:
                try:
                    poster(job.project_id, job.mr_iid, body, position)
                    posted += 1
                    last_error = ""
                    break
                except GitLabError as exc:
                    last_error = str(exc)
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    break
            if last_error:
                logger.warning(
                    "discussion failed job=%s path=%s:%s: %s",
                    job.job_id,
                    finding.path,
                    finding.start_line,
                    last_error,
                )
        result.findings_posted = posted
        if posted or skipped:
            logger.info(
                "posted %s diff thread(s) replies=%s skipped_similar=%s",
                posted,
                replies,
                skipped,
            )
            self._append_job_log(
                job,
                f"posted {posted} diff thread(s) replies={replies} skipped_similar={skipped}",
            )

    def _existing_creasy_threads(self, job: JobRecord):
        lister = getattr(self.gitlab, "list_discussions", None)
        if not callable(lister):
            return []
        try:
            return parse_creasy_threads(lister(job.project_id, job.mr_iid))
        except Exception as exc:  # noqa: BLE001
            logger.warning("list discussions failed job=%s: %s", job.job_id, exc)
            return []

    def _reply_finding(self, job: JobRecord, discussion_id: str, body: str) -> bool:
        replier = getattr(self.gitlab, "reply_to_discussion", None)
        if not callable(replier):
            return False
        try:
            replier(job.project_id, job.mr_iid, discussion_id, body)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "reply failed job=%s discussion=%s: %s",
                job.job_id,
                discussion_id,
                exc,
            )
            return False
