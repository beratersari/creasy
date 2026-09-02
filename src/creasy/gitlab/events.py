from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal, Optional, Union

TriggerKind = Literal["open", "update", "reopen", "review", "ask"]

_CMD_RE = re.compile(r"(?:^|\s)/(review|ask)(?=\s|$)", re.IGNORECASE)


@dataclass(frozen=True)
class ReviewTrigger:
    kind: TriggerKind
    project_id: int
    mr_iid: int
    source_branch: str = ""
    target_branch: str = ""
    sha: str = ""
    comment_text: str = ""
    web_url: str = ""
    draft: bool = False
    explicit: bool = False


@dataclass(frozen=True)
class CleanupTrigger:
    project_id: int
    mr_iid: int
    action: str


@dataclass(frozen=True)
class Ignore:
    reason: str


Classified = Union[ReviewTrigger, CleanupTrigger, Ignore]


def first_command(body: str) -> Optional[tuple[str, str]]:
    """Return (command, remainder) for the first /review or /ask token."""
    text = body or ""
    match = _CMD_RE.search(text)
    if not match:
        return None
    command = match.group(1).lower()
    remainder = text[match.end() :].strip()
    return command, remainder


def _attrs(payload: dict[str, Any]) -> dict[str, Any]:
    attrs = payload.get("object_attributes") or {}
    return attrs if isinstance(attrs, dict) else {}


def _project_and_mr(payload: dict[str, Any]) -> Optional[tuple[int, int]]:
    attrs = _attrs(payload)
    mr = payload.get("merge_request")
    mr = mr if isinstance(mr, dict) else {}
    project_id = (
        attrs.get("target_project_id")
        or attrs.get("source_project_id")
        or mr.get("target_project_id")
        or (payload.get("project") or {}).get("id")
    )
    iid = attrs.get("iid") or mr.get("iid")
    try:
        if project_id is None or iid is None:
            return None
        return int(project_id), int(iid)
    except (TypeError, ValueError):
        return None


def _is_draft(payload: dict[str, Any], attrs: dict[str, Any]) -> bool:
    mr = payload.get("merge_request")
    mr = mr if isinstance(mr, dict) else {}
    for blob in (attrs, mr):
        if blob.get("draft") is True or blob.get("work_in_progress") is True:
            return True
    return False


def classify_webhook(
    payload: dict[str, Any],
    *,
    skip_drafts: bool = True,
    bot_user_id: Optional[int] = None,
) -> Classified:
    if not isinstance(payload, dict):
        return Ignore("invalid payload")
    kind = str(payload.get("object_kind") or "").strip().lower()
    if kind == "merge_request":
        return _classify_merge_request(payload, skip_drafts=skip_drafts)
    if kind == "note":
        return _classify_note(payload, bot_user_id=bot_user_id)
    return Ignore(f"object_kind={kind or 'missing'}")


def _classify_merge_request(payload: dict[str, Any], *, skip_drafts: bool) -> Classified:
    attrs = _attrs(payload)
    action = str(attrs.get("action") or "").strip().lower()
    ids = _project_and_mr(payload)
    if ids is None:
        return Ignore("missing project_id or mr_iid")
    project_id, mr_iid = ids
    if action in {"close", "merge"}:
        return CleanupTrigger(project_id=project_id, mr_iid=mr_iid, action=action)
    if action not in {"open", "update", "reopen"}:
        return Ignore(f"action={action or 'missing'}")
    if action == "update" and not str(attrs.get("oldrev") or "").strip():
        return Ignore("update without oldrev")
    draft = _is_draft(payload, attrs)
    if skip_drafts and draft:
        return Ignore("draft MR")
    return ReviewTrigger(
        kind=action,  # type: ignore[arg-type]
        project_id=project_id,
        mr_iid=mr_iid,
        source_branch=str(attrs.get("source_branch") or ""),
        target_branch=str(attrs.get("target_branch") or ""),
        sha=str((attrs.get("last_commit") or {}).get("id") or "")
        if isinstance(attrs.get("last_commit"), dict)
        else "",
        web_url=str(attrs.get("url") or ""),
        draft=draft,
        explicit=False,
    )


def _classify_note(payload: dict[str, Any], *, bot_user_id: Optional[int]) -> Classified:
    attrs = _attrs(payload)
    if str(attrs.get("noteable_type") or "") != "MergeRequest":
        return Ignore("note not on merge request")
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    try:
        user_id = int(user.get("id")) if user.get("id") is not None else None
    except (TypeError, ValueError):
        user_id = None
    if bot_user_id is not None and user_id == bot_user_id:
        return Ignore("bot note")
    parsed = first_command(str(attrs.get("note") or ""))
    if parsed is None:
        return Ignore("no /review or /ask")
    command, remainder = parsed
    if command == "ask" and not remainder:
        return Ignore("empty /ask")
    ids = _project_and_mr(payload)
    if ids is None:
        return Ignore("missing project_id or mr_iid")
    project_id, mr_iid = ids
    mr = payload.get("merge_request") if isinstance(payload.get("merge_request"), dict) else {}
    return ReviewTrigger(
        kind="ask" if command == "ask" else "review",
        project_id=project_id,
        mr_iid=mr_iid,
        source_branch=str(mr.get("source_branch") or ""),
        target_branch=str(mr.get("target_branch") or ""),
        sha=str(mr.get("last_commit", {}).get("id") or "")
        if isinstance(mr.get("last_commit"), dict)
        else "",
        comment_text=remainder,
        web_url=str(mr.get("url") or ""),
        draft=_is_draft(payload, attrs),
        explicit=True,
    )
