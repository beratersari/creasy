from __future__ import annotations

import re

from creasy.jobs.models import JobRecord
from creasy.logging import redact_userinfo
from creasy.review.findings import split_findings

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_BOLD = re.compile(r"^\*\*(.+?)\*\*\s*$")
_HR = re.compile(r"^-{3,}\s*$")
_FENCE = re.compile(r"^(`{3,}|~{3,})")
_FINDING = re.compile(r"^\d+\.\s+")
_OLD_LABELS = {
    "blocking": "Critical",
    "should fix": "Major",
    "nits": "Minor",
    "looks good": "Improvement",
    "what looks good": "Improvement",
}
_GROUPS = {
    "summary": "Summary",
    "critical": "Critical",
    "major": "Major",
    "minor": "Minor",
    "improvement": "Improvement",
    **_OLD_LABELS,
}


def _inner_label(line: str) -> str:
    stripped = line.strip()
    heading = _HEADING.match(stripped)
    if heading:
        return heading.group(2).strip()
    bold = _BOLD.match(stripped)
    if bold:
        return bold.group(1).strip()
    return stripped


def _normalize_line(line: str) -> str:
    """Promote group/issue titles to ### / ####. Demote huge # / ##."""
    stripped = line.strip()
    heading = _HEADING.match(stripped)
    bold = _BOLD.match(stripped)
    title = heading.group(2).strip() if heading else (bold.group(1).strip() if bold else stripped)
    key = title.lower()
    if key in _GROUPS and (heading or bold):
        return f"### {_GROUPS[key]}"
    if _FINDING.match(title) and (heading or bold):
        return f"#### {title}"
    if heading:
        level = len(heading.group(1))
        if level <= 2:
            return f"### {title}"
        return f"{'#' * min(level, 4)} {title}"
    return line


def _drop_preamble(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if _inner_label(line).lower() == "summary":
            return "\n".join(lines[index:])
    return text


def soften_markdown(text: str) -> str:
    """Make model markdown readable in a GitLab comment.

    Group labels become ``###``. Finding titles become ``####``.
    ``#`` / ``##`` are demoted. Decorative rules are dropped. Fenced
    code is left alone so ``#include`` and snippets stay intact.
    """
    out: list[str] = []
    fence: str | None = None
    blank_run = 0
    for raw in (text or "").splitlines():
        stripped = raw.strip()
        opened = _FENCE.match(stripped)
        if fence:
            out.append(raw)
            if opened and stripped.startswith(fence):
                fence = None
            blank_run = 0
            continue
        if opened:
            fence = opened.group(1)[0] * len(opened.group(1))
            out.append(raw)
            blank_run = 0
            continue
        if _HR.match(stripped):
            continue
        raw = _normalize_line(raw)
        stripped = raw.strip()
        if not stripped:
            blank_run += 1
            if blank_run > 1:
                continue
            out.append("")
            continue
        blank_run = 0
        out.append(raw)
    return _drop_preamble("\n".join(out).strip())


def format_success(job: JobRecord) -> str:
    kind = "Answer" if job.trigger == "ask" else "Review"
    model = job.model or "unknown"
    markdown, _findings = split_findings(job.text or "")
    body = soften_markdown(markdown.strip()) or "_(empty OpenCode response)_"
    return f"**Creasy — {kind}** · `{model}` · `{job.job_id}`\n\n{body}\n"


def format_failure(job: JobRecord) -> str:
    err = redact_userinfo((job.error_message or job.text or "unknown error").strip()) or "unknown error"
    return f"**Creasy — Review failed** · `{job.job_id}`\n\n```\n{err}\n```\n"


def format_cancelled(job: JobRecord) -> str:
    return f"**Creasy — Cancelled** · `{job.job_id}` was cancelled.\n"
