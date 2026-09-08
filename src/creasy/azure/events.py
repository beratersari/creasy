"""Classify Azure DevOps Service Hook payloads. GitLab classify is untouched."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from creasy.azure.identity import azure_project_num
from creasy.gitlab.events import CleanupTrigger, Ignore, ReviewTrigger, first_command
from creasy.logging import get_logger, log_fail, log_ok

logger = get_logger("azure.events")

_CREATED = frozenset({"git.pullrequest.created", "git.pullrequest.opened"})
_COMMENTED = frozenset(
    {
        "git.pullrequest.commented",
        "ms.vss-code.git-pullrequest-comment-event",
        "git.pullrequest.comment",
    }
)
_UPDATED = frozenset({"git.pullrequest.updated", "git.pullrequest.updatedevent"})
_MERGED = frozenset({"git.pullrequest.merged", "git.pullrequest.completed"})
_ABANDONED = frozenset({"abandoned", "completed", "closed"})
_PR_LINK = re.compile(
    r"/repositories/([^/]+)/pullRequests/(\d+)",
    re.IGNORECASE,
)
_EDIT_MSG = re.compile(
    r"edited a (pull request )?comment|has edited a",
    re.IGNORECASE,
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _event_type(payload: dict[str, Any]) -> str:
    raw = payload.get("eventType") or payload.get("event_type") or payload.get("eventName") or ""
    return str(raw).strip().lower()


def _resource(payload: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(payload.get("resource"))


def _comment(payload: dict[str, Any]) -> dict[str, Any]:
    resource = _resource(payload)
    comment = _as_dict(resource.get("comment"))
    if comment:
        return comment
    return _as_dict(payload.get("comment"))


def _ids_from_links(blob: dict[str, Any]) -> tuple[str, Optional[int]]:
    """Parse repo GUID + PR id from Azure comment _links (self / repository)."""
    links = _as_dict(blob.get("_links"))
    for key in ("self", "repository", "threads", "pullRequests"):
        href = str(_as_dict(links.get(key)).get("href") or "")
        match = _PR_LINK.search(href)
        if match:
            try:
                return match.group(1), int(match.group(2))
            except (TypeError, ValueError):
                return match.group(1), None
    for raw in (blob.get("url"), blob.get("href")):
        match = _PR_LINK.search(str(raw or ""))
        if match:
            try:
                return match.group(1), int(match.group(2))
            except (TypeError, ValueError):
                return match.group(1), None
    return "", None


def _container_project(payload: dict[str, Any]) -> dict[str, Any]:
    containers = _as_dict(payload.get("resourceContainers"))
    return _as_dict(containers.get("project"))


def _pull_request(payload: dict[str, Any]) -> dict[str, Any]:
    """PR object from nested pullRequest, the resource itself, or comment links."""
    resource = _resource(payload)
    nested = _as_dict(resource.get("pullRequest") or resource.get("pull_request"))
    if nested:
        pr = dict(nested)
        source = "resource.pullRequest"
    elif resource.get("pullRequestId") is not None or resource.get("pullRequestID") is not None:
        pr = dict(resource)
        source = "resource"
    else:
        pr = {}
        source = "empty"

    comment = _comment(payload)
    link_repo, link_pr = _ids_from_links(comment)
    if not link_repo:
        link_repo, link_pr = _ids_from_links(resource)
    project_box = _container_project(payload)

    repo = dict(_as_dict(pr.get("repository")))
    project = dict(_as_dict(repo.get("project") or pr.get("project") or project_box))
    if project_box.get("id") and not project.get("id"):
        project["id"] = project_box.get("id")
    if project_box.get("name") and not project.get("name"):
        project["name"] = project_box.get("name")
    if link_repo and not repo.get("id"):
        repo["id"] = link_repo
        source = f"{source}+comment-link"
    if project:
        repo["project"] = project
    if repo:
        pr["repository"] = repo
    if link_pr is not None and _pr_id(pr) is None:
        pr["pullRequestId"] = link_pr
        source = f"{source}+comment-link-pr"

    logger.info(
        "azure PR extract source=%s project=%s repo=%s pr=%s",
        source,
        project.get("id") or project.get("name") or "-",
        repo.get("id") or repo.get("name") or "-",
        _pr_id(pr),
    )
    return pr


def _repo_and_project(pr: dict[str, Any]) -> tuple[str, str]:
    repo = _as_dict(pr.get("repository"))
    project = _as_dict(repo.get("project") or pr.get("project"))
    repo_id = str(repo.get("id") or repo.get("name") or "").strip()
    project_id = str(project.get("id") or project.get("name") or "").strip()
    return project_id, repo_id


def _pr_id(pr: dict[str, Any]) -> Optional[int]:
    raw = pr.get("pullRequestId") if pr.get("pullRequestId") is not None else pr.get("pullRequestID")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _ref_name(value: Any) -> str:
    text = str(value or "").strip()
    for prefix in ("refs/heads/", "refs/tags/"):
        if text.startswith(prefix):
            return text[len(prefix) :]
    return text


def _sha(pr: dict[str, Any]) -> str:
    for key in ("lastMergeSourceCommit", "lastMergeCommit", "sourceCommit", "commit"):
        blob = _as_dict(pr.get(key))
        commit = str(blob.get("commitId") or blob.get("id") or "").strip()
        if commit:
            return commit
    return str(pr.get("lastMergeSourceCommitId") or "").strip()


def _web_url(pr: dict[str, Any], payload: dict[str, Any]) -> str:
    links = _as_dict(pr.get("_links"))
    web = _as_dict(links.get("web"))
    for candidate in (web.get("href"), pr.get("url"), payload.get("resourceUrl")):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _is_draft(pr: dict[str, Any]) -> bool:
    return bool(pr.get("isDraft") or pr.get("is_draft"))


def _status(pr: dict[str, Any]) -> str:
    return str(pr.get("status") or "").strip().lower()


def _author_id(blob: dict[str, Any]) -> str:
    author = _as_dict(blob.get("author") or blob.get("createdBy") or blob.get("user"))
    return str(author.get("id") or author.get("uniqueName") or "").strip()


def _parse_dt(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _is_comment_edit(payload: dict[str, Any], comment: dict[str, Any]) -> bool:
    """True only for a real edit, not a create that also sets lastUpdatedDate."""
    msg = " ".join(
        [
            str(_as_dict(payload.get("message")).get("text") or ""),
            str(_as_dict(payload.get("detailedMessage")).get("text") or ""),
        ]
    )
    if _EDIT_MSG.search(msg):
        logger.info("azure comment treated as edit (message text)")
        return True
    published = _parse_dt(comment.get("publishedDate") or comment.get("published_date"))
    content_changed = _parse_dt(comment.get("lastContentUpdatedDate") or comment.get("last_content_updated_date"))
    if published and content_changed:
        delta = abs((content_changed - published).total_seconds())
        if delta > 2:
            logger.info("azure comment treated as edit (content updated %.1fs later)", delta)
            return True
    return False


def classify_azure_webhook(
    payload: dict[str, Any],
    *,
    skip_drafts: bool = True,
    bot_user_id: Optional[str] = None,
) -> CleanupTrigger | ReviewTrigger | Ignore:
    if not isinstance(payload, dict):
        log_ok(logger, "azure classify Ignore", reason="invalid payload")
        return Ignore("invalid payload")
    kind = _event_type(payload)
    pr = _pull_request(payload)
    logger.info(
        "azure classify eventType=%s status=%s draft=%s",
        kind or "missing",
        _status(pr) or "-",
        _is_draft(pr),
    )
    if kind in _MERGED or (kind in _UPDATED and _status(pr) in _ABANDONED):
        action = "merge" if _status(pr) == "completed" or kind in _MERGED else "close"
        got = _cleanup(pr, action=action)
        return got
    if kind in _UPDATED:
        log_ok(logger, "azure classify Ignore", eventType=kind, reason="action=update")
        return Ignore("action=update")
    if kind in _CREATED:
        return _review_from_pr(pr, payload, kind="open", explicit=False, skip_drafts=skip_drafts)
    if kind in _COMMENTED:
        return _review_from_comment(payload, pr, bot_user_id=bot_user_id)
    if kind:
        log_ok(logger, "azure classify Ignore", eventType=kind, reason=f"eventType={kind}")
        return Ignore(f"eventType={kind}")
    log_ok(logger, "azure classify Ignore", reason="eventType=missing")
    return Ignore("eventType=missing")


def _cleanup(pr: dict[str, Any], *, action: str) -> CleanupTrigger | Ignore:
    project_id, repo_id = _repo_and_project(pr)
    iid = _pr_id(pr)
    if not project_id or not repo_id or iid is None:
        log_fail(logger, "azure classify Cleanup", reason="missing ids", project=project_id or "-", repo=repo_id or "-", pr=iid)
        return Ignore("missing project, repo, or pullRequestId")
    log_ok(logger, "azure classify Cleanup", project=project_id, repo=repo_id, pr=iid, action=action)
    return CleanupTrigger(
        project_id=azure_project_num(project_id, repo_id),
        mr_iid=iid,
        action=action,
    )


def _review_from_pr(
    pr: dict[str, Any],
    payload: dict[str, Any],
    *,
    kind: str,
    explicit: bool,
    skip_drafts: bool,
    comment_text: str = "",
) -> ReviewTrigger | Ignore:
    project_id, repo_id = _repo_and_project(pr)
    iid = _pr_id(pr)
    if not project_id or not repo_id or iid is None:
        log_fail(logger, "azure classify ReviewTrigger", reason="missing ids", project=project_id or "-", repo=repo_id or "-", pr=iid)
        return Ignore("missing project, repo, or pullRequestId")
    draft = _is_draft(pr)
    if skip_drafts and draft and not explicit:
        log_ok(logger, "azure classify Ignore", reason="draft PR", pr=iid)
        return Ignore("draft PR")
    trigger = ReviewTrigger(
        kind=kind,  # type: ignore[arg-type]
        project_id=azure_project_num(project_id, repo_id),
        mr_iid=iid,
        source_branch=_ref_name(pr.get("sourceRefName") or pr.get("sourceRef")),
        target_branch=_ref_name(pr.get("targetRefName") or pr.get("targetRef")),
        sha=_sha(pr),
        comment_text=comment_text,
        web_url=_web_url(pr, payload),
        title=str(pr.get("title") or ""),
        draft=draft,
        explicit=explicit,
        provider="azure",
        azure_project=project_id,
        azure_repo=repo_id,
    )
    log_ok(
        logger,
        "azure classify ReviewTrigger",
        kind=trigger.kind,
        explicit=trigger.explicit,
        pr=iid,
        project=project_id,
        repo=repo_id,
        sha=trigger.sha or "-",
        source=trigger.source_branch or "-",
        target=trigger.target_branch or "-",
    )
    return trigger


def _review_from_comment(
    payload: dict[str, Any],
    pr: dict[str, Any],
    *,
    bot_user_id: Optional[str],
) -> ReviewTrigger | Ignore:
    comment = _comment(payload)
    if not comment:
        log_ok(logger, "azure classify Ignore", reason="missing comment")
        return Ignore("missing comment")
    if comment.get("isDeleted") is True:
        log_ok(logger, "azure classify Ignore", reason="deleted comment")
        return Ignore("deleted comment")
    if _is_comment_edit(payload, comment):
        log_ok(logger, "azure classify Ignore", reason="note edit")
        return Ignore("note edit")
    author = _author_id(comment)
    if bot_user_id and author and author.lower() == bot_user_id.lower():
        log_ok(logger, "azure classify Ignore", reason="bot note", author=author)
        return Ignore("bot note")
    parsed = first_command(str(comment.get("content") or comment.get("comments") or ""))
    if parsed is None:
        log_ok(logger, "azure classify Ignore", reason="no /review, /ask, or /reset", author=author or "-")
        return Ignore("no /review, /ask, or /reset")
    command, remainder = parsed
    logger.info("azure comment command=/%s author=%s remainder=%r", command, author or "-", remainder[:80])
    if command == "ask" and not remainder:
        log_ok(logger, "azure classify Ignore", reason="empty /ask", author=author or "-")
        return Ignore("empty /ask")
    if not pr or _pr_id(pr) is None:
        log_fail(logger, "azure classify ReviewTrigger", reason="missing pull request on comment", command=command)
        return Ignore("missing pull request on comment")
    return _review_from_pr(
        pr,
        payload,
        kind=command,
        explicit=True,
        skip_drafts=False,
        comment_text=remainder,
    )
