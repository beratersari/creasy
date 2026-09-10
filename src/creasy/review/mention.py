"""Detect @mentions of the review bot in GitLab notes and Azure comments."""

from __future__ import annotations

import re
from typing import Iterable, Optional, Sequence

_VSS_MENTION = re.compile(
    r'data-vss-mention\s*=\s*["\'][^"\']*?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-'
    r'[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})',
    re.IGNORECASE,
)
_HTML_MENTION = re.compile(r"<a\b[^>]*data-vss-mention[^>]*>.*?</a>", re.IGNORECASE | re.DOTALL)
_CMD_RE = re.compile(r"(?:^|\s)/(ask|review)(?=[\s.,!?:;)]|$)", re.IGNORECASE)
_CMD_TRAIL = ".,!?:;)"


def parse_mention_aliases(raw: str) -> list[str]:
    names: list[str] = []
    for part in str(raw or "").split(","):
        name = part.strip().lstrip("@")
        if name:
            names.append(name)
    return names


def collect_names(*groups: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for group in groups:
        for raw in group:
            text = str(raw or "").strip().lstrip("@")
            if not text:
                continue
            if text not in seen:
                seen.append(text)
            if "\\" in text:
                tail = text.rsplit("\\", 1)[-1].strip()
                if tail and tail not in seen:
                    seen.append(tail)
    return seen


def azure_mention_ids(text: str) -> list[str]:
    return [match.group(1) for match in _VSS_MENTION.finditer(text or "")]


def first_slash_command(body: str) -> Optional[tuple[str, str]]:
    """Return (command, remainder) for the first /ask or /review token."""
    text = body or ""
    match = _CMD_RE.search(text)
    if not match:
        return None
    command = match.group(1).lower()
    remainder = text[match.end() :].lstrip(_CMD_TRAIL).strip()
    return command, remainder


def has_bot_mention(
    body: str,
    names: Sequence[str],
    *,
    mentioned_ids: Sequence[str] = (),
    bot_id: str = "",
    extra_ids: Sequence[str] = (),
) -> bool:
    text = body or ""
    known = {str(bot_id or "").strip().lower()}
    known.update(str(item or "").strip().lower() for item in extra_ids)
    known.discard("")
    if known and any(str(item or "").strip().lower() in known for item in mentioned_ids):
        return True
    return _mention_match(text, names) is not None


def _mention_match(text: str, names: Sequence[str]) -> Optional[re.Match[str]]:
    aliases = collect_names(names)
    aliases.sort(key=len, reverse=True)
    for alias in aliases:
        pattern = re.escape(alias).replace(r"\ ", r"\s+")
        match = re.search(
            rf"(?<![A-Za-z0-9._-])@(?:[^\s@]+\\)?{pattern}(?![A-Za-z0-9._-])",
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return match
    return None


def strip_bot_mentions(text: str, names: Sequence[str]) -> str:
    cleaned = _HTML_MENTION.sub(" ", text or "")
    while True:
        match = _mention_match(cleaned, names)
        if not match:
            break
        cleaned = f"{cleaned[: match.start()]} {cleaned[match.end() :]}"
    return " ".join(cleaned.split()).strip(".,;:")


def user_comment_text(body: str, names: Sequence[str]) -> str:
    """User words with the bot mention and slash command removed."""
    text = strip_bot_mentions(body or "", names)
    parsed = first_slash_command(text)
    if parsed is None:
        return text
    match = _CMD_RE.search(text)
    if not match:
        return parsed[1]
    leftover = f"{text[: match.start()]} {text[match.end() :].lstrip(_CMD_TRAIL)}"
    return " ".join(leftover.split()).strip()


def comment_intent(
    body: str,
    names: Sequence[str],
    *,
    mentioned_ids: Sequence[str] = (),
    bot_id: str = "",
    extra_ids: Sequence[str] = (),
) -> Optional[tuple[str, str, str]]:
    """Parse a comment.

    Returns ``("run", "ask", remainder)`` when the bot is mentioned and
    ``/ask`` is present. Otherwise ``None``.
    """
    mentioned = has_bot_mention(
        body,
        names,
        mentioned_ids=mentioned_ids,
        bot_id=bot_id,
        extra_ids=extra_ids,
    )
    parsed = first_slash_command(body)
    if not mentioned or not parsed:
        return None
    command, remainder = parsed
    remainder = strip_bot_mentions(remainder, names)
    leftover = user_comment_text(body, names)
    if command == "ask":
        from creasy.review.ask import ask_wants_new_review

        if ask_wants_new_review(leftover or remainder):
            command = "review"
    return "run", command, remainder


USAGE_HEADING = "**Creasy — how to run a command**"
USAGE_MARKER = "<!-- creasy-usage -->"


def is_usage_note(body: str) -> bool:
    """True for an old help note, so its examples do not start a job."""
    text = body or ""
    return USAGE_MARKER in text or USAGE_HEADING in text
