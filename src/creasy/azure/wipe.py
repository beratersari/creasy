"""Delete PR threads/comments authored by the Azure PAT user."""

from __future__ import annotations

from typing import Any, Callable, Optional

from creasy.gitlab.wipe import WipeCancelled, WipeStats
from creasy.logging import get_logger, log_fail, log_ok

logger = get_logger("azure.wipe")


def _author_id(comment: dict[str, Any]) -> str:
    author = comment.get("author") if isinstance(comment.get("author"), dict) else {}
    return str(author.get("id") or "").strip()


def _comment_id(comment: dict[str, Any]) -> Optional[int]:
    raw = comment.get("id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def wipe_azure_comments(
    client,
    project: str,
    repo: str,
    pr_id: int,
    author_id: str,
    should_stop: Optional[Callable[[], bool]] = None,
) -> WipeStats:
    stats = WipeStats()
    want = (author_id or "").strip()
    if not want:
        log_fail(logger, "azure wipe", pr=pr_id, reason="empty author id")
        return stats
    log_ok(logger, "azure wipe start", pr=pr_id, project=project, repo=repo, author=want)
    threads = client.list_threads(project, repo, pr_id)
    for thread in threads:
        if should_stop is not None and should_stop():
            raise WipeCancelled()
        if not isinstance(thread, dict):
            continue
        thread_id = str(thread.get("id") or "").strip()
        comments = [c for c in (thread.get("comments") or []) if isinstance(c, dict)]
        if not thread_id or not comments:
            continue
        first = comments[0]
        first_id = _comment_id(first)
        if first_id is not None and _author_id(first) == want:
            ok = client.delete_comment(project, repo, pr_id, thread_id, first_id)
            if ok:
                stats.threads += 1
                log_ok(logger, "azure wipe thread", pr=pr_id, thread=thread_id, comment=first_id)
            else:
                stats.failed += 1
                stats.errors.append(f"thread {thread_id}")
                log_fail(logger, "azure wipe thread", pr=pr_id, thread=thread_id, comment=first_id)
            continue
        for comment in comments[1:]:
            if should_stop is not None and should_stop():
                raise WipeCancelled()
            cid = _comment_id(comment)
            if cid is None or _author_id(comment) != want:
                continue
            ok = client.delete_comment(project, repo, pr_id, thread_id, cid)
            if ok:
                stats.replies += 1
                log_ok(logger, "azure wipe reply", pr=pr_id, thread=thread_id, comment=cid)
            else:
                stats.failed += 1
                stats.errors.append(f"reply {cid}")
                log_fail(logger, "azure wipe reply", pr=pr_id, thread=thread_id, comment=cid)
    if stats.failed:
        log_fail(logger, "azure wipe done", pr=pr_id, summary=stats.summary())
    else:
        log_ok(logger, "azure wipe done", pr=pr_id, summary=stats.summary())
    return stats