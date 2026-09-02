from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Protocol

from creasy.config import Config
from creasy.gitlab.client import GitLabClient, MergeRequest
from creasy.jobs.models import JobRecord
from creasy.logging import get_logger
from creasy.opencode.kill import kill_job_tree
from creasy.opencode.serve import ServeHandle, serve_log_path, start_serve, stop_serve
from creasy.opencode.session import OpenCodeClient, OpenCodeError, last_assistant_text, snapshot_chat
from creasy.review.format import format_cancelled, format_failure, format_success
from creasy.review.prompt import build_ask_prompt, build_review_prompt, load_review_rules
from creasy.workspace.gitops import (
    GitError,
    clone_repo,
    delete_clone,
    diff_stat,
    fetch_and_checkout,
    resolve_merge_base,
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


class JobRunner(Protocol):
    def run(self, job: JobRecord, should_stop: Callable[[], bool]) -> RunResult: ...


class OpenCodeRunner:
    def __init__(
        self,
        config: Config,
        workspaces: WorkspaceStore,
        gitlab: Optional[GitLabClient] = None,
    ) -> None:
        self.config = config
        self.workspaces = workspaces
        self.gitlab = gitlab or GitLabClient(config.gitlab_url, config.gitlab_token)

    def run(self, job: JobRecord, should_stop: Callable[[], bool]) -> RunResult:
        result = RunResult()
        handle: Optional[ServeHandle] = None
        client: Optional[OpenCodeClient] = None
        try:
            if should_stop():
                result.cancelled = True
                return result
            mr = self.gitlab.get_merge_request(job.project_id, job.mr_iid)
            workspace = self._ensure_workspace(job, mr, should_stop)
            result.clone_path = workspace.clone_path
            result.sha = workspace.last_sha
            clone = Path(workspace.clone_path)
            merge_base = resolve_merge_base(
                clone,
                target_branch=mr.target_branch,
                preferred_base=mr.base_sha,
                timeout=min(60.0, self.config.git_timeout),
            )
            index = diff_stat(clone, merge_base, timeout=min(60.0, self.config.git_timeout))
            result.merge_base = merge_base
            result.diff_stat = index.stat
            result.changed_paths = list(index.paths)
            logger.info("diff stat for %s:\n%s", job.mr_key, index.stat)

            created_new = False
            prompt = self._prompt(job, mr, index, workspace, created_new=False)
            handle = start_serve(
                bin_name=self.config.opencode_bin,
                cwd=clone,
                log_path=serve_log_path(self.config.serve_dir, job.job_id),
                timeout=self.config.serve_health_timeout,
                should_stop=should_stop,
            )
            result.serve_pid = handle.pid
            result.serve_port = handle.port
            client = OpenCodeClient(handle.base_url, str(clone))
            session_id, created_new = client.resume_or_create(
                workspace.session_id or None,
                title=f"creasy {job.mr_key}",
            )
            result.session_id = session_id
            if created_new and job.trigger == "ask":
                prompt = self._prompt(job, mr, index, workspace, created_new=True)
            last_error = ""
            text = ""
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
                    client.post_message(
                        session_id,
                        prompt,
                        model=self.config.opencode_model,
                        agent=self.config.opencode_agent,
                    )
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
                    if should_stop() or attempt >= self.config.opencode_retry_count:
                        break
            if should_stop():
                result.cancelled = True
                return result
            if last_error and not text:
                result.error = last_error
            result.text = text
            try:
                messages = client.list_messages(session_id)
                result.chat_snapshot = snapshot_chat(messages, session_id)
                if not result.text:
                    result.text = last_assistant_text(messages)
            except Exception:
                pass
            workspace.session_id = session_id
            workspace.last_job_id = job.job_id
            self.workspaces.save(workspace)
            self._post_note(job, result)
            return result
        except GitError as exc:
            result.error = f"git failed: {exc}"
            self._post_note(job, result)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("worker failed job=%s", job.job_id)
            result.error = f"pipeline failed: {exc}"
            self._post_note(job, result)
            return result
        finally:
            if client is not None and result.session_id:
                try:
                    client.abort(result.session_id)
                except Exception:
                    pass
                client.close()
            stop_serve(handle)
            if handle is not None:
                kill_job_tree([handle.pid])

    def _prompt(
        self,
        job: JobRecord,
        mr: MergeRequest,
        index,
        workspace: WorkspaceRecord,
        *,
        created_new: bool,
    ) -> str:
        if job.trigger == "ask":
            sha_changed = bool(workspace.last_sha and mr.sha and workspace.last_sha != mr.sha)
            return build_ask_prompt(
                job.comment_text,
                mr=mr,
                index=index,
                sha_changed=sha_changed,
                previous_sha=workspace.last_sha,
                include_context=created_new or not workspace.session_id,
            )
        rules = load_review_rules(Path(workspace.clone_path))
        return build_review_prompt(mr, index, extra_notes=job.comment_text, rules=rules)

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
        if not dest.exists() or not (dest / ".git").exists():
            if dest.exists():
                delete_clone(dest)
            clone_repo(http_url, dest, self.config.gitlab_token, timeout=self.config.git_timeout)
        sha = fetch_and_checkout(
            dest,
            source_branch=mr.source_branch,
            target_branch=mr.target_branch,
            sha=mr.sha,
            token=self.config.gitlab_token,
            timeout=self.config.git_timeout,
        )
        record.clone_path = str(dest)
        record.source_branch = mr.source_branch
        record.target_branch = mr.target_branch
        record.last_sha = sha
        record.http_url = http_url
        record.web_url = mr.web_url
        record.last_job_id = job.job_id
        return self.workspaces.save(record)

    def _post_note(self, job: JobRecord, result: RunResult) -> None:
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
