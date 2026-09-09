"""Classify Azure DevOps Service Hook payloads. GitLab classify is untouched."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from creasy.azure.identity import azure_project_num
from creasy.azure.urls import looks_like_azure_resource, normalize_collection_url
from creasy.gitlab.events import CleanupTrigger, Ignore, ReviewTrigger
from creasy.review.comment_range import parse_azure_thread_context
from creasy.review.mention import azure_mention_ids, comment_intent, is_usage_note, user_comment_text
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
_REVIEWERS = frozenset(
    {
        "git.pullrequest.reviewers.update",
        "ms.vss-code.git-pullrequest-reviewers-update-event",
    }
)
_MERGED = frozenset({"git.pullrequest.merged", "git.pullrequest.completed"})
_ADDED_REVIEWER = re.compile(
    r"^(?P<actor>.+?) added (?P<who>.+?) as an? (?:required )?reviewer",
    re.IGNORECASE,
)
_ABANDONED = frozenset({"abandoned", "completed", "closed"})
_PR_LINK = re.compile(
    r"/repositories/([^/]+)/pullRequests/(\d+)",
    re.IGNORECASE,
)
_THREAD_LINK = re.compile(r"/threads/([^/?#]+)(?:/comments/(\d+))?", re.IGNORECASE)
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


def _thread_ref(payload: dict[str, Any]) -> tuple[str, int]:
    """Thread id + parent comment id from a comment Service Hook."""
    comment = _comment(payload)
    resource = _resource(payload)
    for blob in (comment, resource):
        thread = str(blob.get("threadId") or blob.get("thread_id") or "").strip()
        parent = _int_id(blob.get("id") or blob.get("commentId") or blob.get("comment_id"))
        if thread:
            return thread, parent
        for key in ("self", "threads", "replies"):
            href = str(_as_dict(_as_dict(blob.get("_links")).get(key)).get("href") or "")
            match = _THREAD_LINK.search(href)
            if match:
                return match.group(1), _int_id(match.group(2)) or parent
        match = _THREAD_LINK.search(str(blob.get("url") or blob.get("href") or ""))
        if match:
            return match.group(1), _int_id(match.group(2)) or parent
    return "", 0


def _int_id(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


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


def _collection_url(pr: dict[str, Any], payload: dict[str, Any]) -> str:
    containers = _as_dict(payload.get("resourceContainers"))
    for key in ("collection", "account"):
        box = _as_dict(containers.get(key))
        raw = str(box.get("baseUrl") or box.get("base_url") or "").strip()
        got = normalize_collection_url(raw)
        if got:
            return got
    repo = _as_dict(pr.get("repository"))
    for candidate in (
        _web_url(pr, payload),
        pr.get("url"),
        repo.get("remoteUrl"),
        repo.get("url"),
        payload.get("resourceUrl"),
    ):
        text = str(candidate or "").strip()
        if looks_like_azure_resource(text):
            got = normalize_collection_url(text)
            if got:
                return got
    return ""


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


def _notification_type(payload: dict[str, Any]) -> str:
    raw = payload.get("notificationType") or payload.get("notification_type") or ""
    if not raw:
        resource = _resource(payload)
        raw = resource.get("notificationType") or resource.get("notification_type") or ""
    return str(raw).strip()


def _message_text(payload: dict[str, Any]) -> str:
    parts = [
        _as_dict(payload.get("message")).get("text"),
        _as_dict(payload.get("detailedMessage")).get("text"),
    ]
    return "\n".join(str(part or "") for part in parts)


def _azure_reviewer_is_bot(row: dict[str, Any], bot_user_id: Optional[str], names: list[str]) -> bool:
    if bot_user_id and str(row.get("id") or "").strip().lower() == str(bot_user_id).strip().lower():
        return True
    aliases = {name.lower() for name in names if name}
    for key in ("displayName", "uniqueName", "name", "providerDisplayName"):
        value = str(row.get(key) or "").strip()
        if not value:
            continue
        if value.lower() in aliases:
            return True
        if "\\" in value and value.rsplit("\\", 1)[-1].lower() in aliases:
            return True
    return False


def _azure_bot_is_reviewer(pr: dict[str, Any], bot_user_id: Optional[str], names: list[str]) -> bool:
    reviewers = pr.get("reviewers") if isinstance(pr.get("reviewers"), list) else []
    for row in reviewers:
        if isinstance(row, dict) and _azure_reviewer_is_bot(row, bot_user_id, names):
            return True
    return False


def _azure_reviewer_assigned(
    payload: dict[str, Any],
    pr: dict[str, Any],
    *,
    bot_user_id: Optional[str],
    mention_names: list[str],
    event_kind: str,
) -> bool:
    if not bot_user_id and not mention_names:
        return False
    if not _azure_bot_is_reviewer(pr, bot_user_id, mention_names):
        return False
    first_line = (_message_text(payload).splitlines() or [""])[0].strip()
    match = _ADDED_REVIEWER.match(first_line)
    aliases = {name.lower() for name in mention_names if name}
    if match:
        who = match.group("who").strip().lower()
        if who in aliases or ("\\" in who and who.rsplit("\\", 1)[-1] in aliases):
            return True
        return False
    text = _message_text(payload).lower()
    ntype = _notification_type(payload).lower()
    if (ntype == "reviewersupdatenotification" or event_kind in _REVIEWERS) and "added" in text and "reviewer" in text:
        return bool(aliases) and any(alias in text for alias in aliases)
    return False


def classify_azure_webhook(
    payload: dict[str, Any],
    *,
    skip_drafts: bool = True,
    bot_user_id: Optional[str] = None,
    mention_names: Optional[list[str]] = None,
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
    if kind in _UPDATED or kind in _REVIEWERS:
        assigned = _azure_reviewer_assigned(
            payload,
            pr,
            bot_user_id=bot_user_id,
            mention_names=mention_names or [],
            event_kind=kind,
        )
        if assigned:
            return _review_from_pr(pr, payload, kind="review", explicit=True, skip_drafts=False)
        if kind in _UPDATED:
            log_ok(logger, "azure classify Ignore", eventType=kind, reason="action=update")
            return Ignore("action=update")
        log_ok(logger, "azure classify Ignore", eventType=kind, reason="reviewers unchanged")
        return Ignore("reviewers unchanged")
    if kind in _CREATED:
        if not _azure_bot_is_reviewer(pr, bot_user_id, mention_names or []):
            log_ok(logger, "azure classify Ignore", eventType=kind, reason="reviewer not assigned")
            return Ignore("reviewer not assigned")
        return _review_from_pr(pr, payload, kind="open", explicit=False, skip_drafts=skip_drafts)
    if kind in _COMMENTED:
        return _review_from_comment(
            payload,
            pr,
            bot_user_id=bot_user_id,
            mention_names=mention_names or [],
        )
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
    thread_id, parent_id = _thread_ref(payload) if explicit else ("", 0)
    path, side, start, end = ("", "", 0, 0)
    if explicit:
        comment = _comment(payload)
        resource = _resource(payload)
        path, side, start, end = parse_azure_thread_context(
            comment.get("threadContext") or resource.get("threadContext")
        )
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
        azure_collection=_collection_url(pr, payload),
        discussion_id=thread_id,
        parent_comment_id=parent_id,
        comment_path=path,
        comment_side=side,
        comment_start_line=start,
        comment_end_line=end,
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
    mention_names: list[str],
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
    note_text = str(comment.get("content") or comment.get("comments") or "")
    if is_usage_note(note_text):
        log_ok(logger, "azure classify Ignore", reason="usage note", author=author or "-")
        return Ignore("usage note")
    intent = comment_intent(
        note_text,
        mention_names,
        mentioned_ids=azure_mention_ids(note_text),
        bot_id=str(bot_user_id or ""),
    )
    if intent is None:
        log_ok(
            logger,
            "azure classify Ignore",
            reason="no mention+command",
            author=author or "-",
        )
        return Ignore("no mention+command")
    action, command, remainder = intent
    logger.info(
        "azure comment intent=%s command=/%s author=%s remainder=%r",
        action,
        command or "-",
        author or "-",
        remainder[:80],
    )
    user_text = user_comment_text(note_text, mention_names)
    if command == "ask" and not remainder and not user_text:
        log_ok(logger, "azure classify Ignore", reason="empty /ask", author=author or "-")
        return Ignore("empty /ask")
    if not pr or _pr_id(pr) is None:
        log_fail(logger, "azure classify ReviewTrigger", reason="missing pull request on comment", command=command or action)
        return Ignore("missing pull request on comment")
    kind = command
    return _review_from_pr(
        pr,
        payload,
        kind=kind,
        explicit=True,
        skip_drafts=False,
        comment_text=user_text or remainder,
    )
