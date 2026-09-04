from __future__ import annotations

from pathlib import Path
from typing import Optional

from creasy.gitlab.client import MergeRequest
from creasy.workspace.gitops import DiffIndex


def load_review_rules(clone_path: Path) -> str:
    for rel in ("agent/rules/CODE_REVIEW.md", ".creasy/CODE_REVIEW.md"):
        path = clone_path / rel
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return ""


def build_review_prompt(
    mr: MergeRequest,
    index: DiffIndex,
    *,
    extra_notes: str = "",
    rules: str = "",
) -> str:
    paths = "\n".join(f"- `{path}` ({index.statuses.get(path, '?')})" for path in index.paths) or "- (none after filters)"
    rules_block = ""
    if rules.strip():
        rules_block = f"\n## Project review rules\n\n{rules.strip()}\n"
    extra = ""
    if extra_notes.strip():
        extra = f"\n## Reviewer notes\n\n{extra_notes.strip()}\n"
    return f"""You are reviewing GitLab merge request !{mr.iid}: {mr.title}

Author: {mr.author}
Branches: `{mr.source_branch}` → `{mr.target_branch}`
HEAD sha: `{mr.sha}`
Separation point (merge-base): `{index.merge_base}`
MR URL: {mr.web_url}

The working tree is the MR source at HEAD. Analyze **from the separation point**, not the whole repo history.

## Diff stat (`git diff --stat {index.merge_base}...HEAD`)

```
{index.stat or '(empty)'}
```

## Changed paths

{paths}
{rules_block}{extra}
## Instructions

1. Run `git log {index.merge_base}..HEAD` and `git diff {index.merge_base}...HEAD` (and per-path diffs) yourself. Do not assume this prompt contains hunks.
2. For each changed path, read the current file and its callers/tests. Review the change in context.
3. Do not commit, push, or edit files. Reply with a markdown review only.
"""


def build_ask_prompt(
    question: str,
    *,
    mr: Optional[MergeRequest] = None,
    index: Optional[DiffIndex] = None,
    sha_changed: bool = False,
    previous_sha: str = "",
    include_context: bool = False,
) -> str:
    parts: list[str] = []
    if sha_changed and index is not None:
        parts.append(
            f"Note: the MR HEAD moved"
            + (f" from `{previous_sha}`" if previous_sha else "")
            + f" to `{mr.sha if mr else ''}`. Updated stat:\n```\n{index.stat}\n```"
        )
    if include_context and mr is not None:
        paths = ", ".join(index.paths[:40]) if index else ""
        parts.append(
            f"MR !{mr.iid} {mr.title} (`{mr.source_branch}` → `{mr.target_branch}`). "
            f"Changed files: {paths or '(see git)'}."
        )
        if index:
            parts.append(f"Separation point: `{index.merge_base}`. Use `git diff {index.merge_base}...HEAD` if needed.")
    parts.append(question.strip())
    return "\n\n".join(p for p in parts if p)


HANG_RESUME = (
    "Continue the previous turn. The last user message was already posted. "
    "Do not restart the review or repeat the full analysis. Finish your answer."
)


def hang_resume_prompt() -> str:
    return HANG_RESUME
