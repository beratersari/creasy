from __future__ import annotations

from creasy.jobs.models import JobRecord


def format_success(job: JobRecord) -> str:
    kind = "Answer" if job.trigger == "ask" else "Automated Code Review"
    model = job.model or "unknown"
    body = (job.text or "").strip() or "_(empty OpenCode response)_"
    return f"## Creasy — {kind}\n\nReviewed with `{model}` · job `{job.job_id}`\n\n---\n\n{body}\n"


def format_failure(job: JobRecord) -> str:
    err = (job.error_message or job.text or "unknown error").strip()
    return f"## Creasy — Review failed\n\nJob `{job.job_id}`\n\n```\n{err}\n```\n"


def format_cancelled(job: JobRecord) -> str:
    return f"## Creasy — Cancelled\n\nJob `{job.job_id}` was cancelled.\n"
