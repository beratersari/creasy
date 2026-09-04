from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

TriggerKind = Literal["open", "update", "reopen", "review", "ask"]
JobStatus = Literal[
    "queued",
    "running",
    "success",
    "error",
    "timeout",
    "cancelled",
]

LIVE_STATUSES = frozenset({"queued", "running"})
ERROR_STATUSES = frozenset({"error", "timeout"})


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def mint_job_id() -> str:
    return "job_" + uuid.uuid4().hex[:16]


class JobRecord(BaseModel):
    job_id: str
    mr_key: str
    project_id: int
    mr_iid: int
    trigger: TriggerKind
    status: JobStatus = "queued"
    live: bool = True
    explicit: bool = False
    comment_text: str = ""
    source_branch: str = ""
    target_branch: str = ""
    sha: str = ""
    base_sha: str = ""
    merge_base: str = ""
    web_url: str = ""
    mr_title: str = ""
    clone_path: str = ""
    session_id: str = ""
    model: str = ""
    agent: str = ""
    prompt: str = ""
    text: str = ""
    error_message: Optional[str] = None
    serve_pid: Optional[int] = None
    serve_port: Optional[int] = None
    serve_base_url: str = ""
    extra_pids: list[int] = Field(default_factory=list)
    accepted_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    updated_at: Optional[str] = None
    log_file: str = ""
    chat_snapshot: list[dict[str, Any]] = Field(default_factory=list)
    diff_stat: str = ""
    changed_paths: list[str] = Field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        data = self.model_dump()
        data.pop("prompt", None)
        data.pop("extra_pids", None)
        data["jira_id"] = data.get("mr_key") or ""
        data["agent_mode"] = data.get("agent") or ""
        data["repo_url"] = data.get("web_url") or ""
        data.setdefault("attempt", 1)
        data.setdefault("retry_count", 1)
        return data
