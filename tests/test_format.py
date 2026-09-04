from __future__ import annotations

from creasy.gitlab.events import first_command
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.review.format import (
    format_cancelled,
    format_failure,
    format_success,
    soften_markdown,
)


def _job(**kwargs) -> JobRecord:
    data = dict(
        job_id=mint_job_id(),
        mr_key="1-2",
        project_id=1,
        mr_iid=2,
        trigger="open",
        text="looks fine",
        model="opencode/x",
    )
    data.update(kwargs)
    return JobRecord(**data)


def test_soften_turns_headings_into_bold_and_drops_rules() -> None:
    raw = """# Code Review: MR !30

---

## Summary

Five defects.

### 1. `src/buf.cpp` — overflow

strcpy overflows.

```cpp
#define MAX 8
char dest[MAX];
```

## Looks good
"""
    got = soften_markdown(raw)
    assert got.startswith("### Summary")
    assert "# Code Review" not in got
    assert not any(line.startswith("## ") and not line.startswith("### ") for line in got.splitlines())
    assert "---" not in got
    assert "#### 1. `src/buf.cpp` — overflow" in got
    assert "#define MAX 8" in got
    assert "### Improvement" in got
    assert "\n\n\n" not in got


def test_soften_leaves_include_and_fences_alone() -> None:
    raw = """**Summary**

```cpp
#include <cstring>
#define MAX 8
```

#include is not a heading
"""
    got = soften_markdown(raw)
    assert "#include <cstring>" in got
    assert "#define MAX 8" in got
    assert got.startswith("### Summary")


def test_success_note_is_comment_sized() -> None:
    job = _job(text="# Review\n\n---\n\n## Summary\n\nLooks risky.")
    body = format_success(job)
    assert body.startswith("**Creasy — Review**")
    assert "## Creasy" not in body
    assert "### Summary" in body
    assert "Looks risky." in body
    assert first_command(body) is None


def test_soften_rewrites_old_labels_and_drops_preamble() -> None:
    raw = """Now I have all the information needed for a thorough review.

**Summary**

Five defects.

**Blocking**

1. overflow

**Should fix**

2. leak

**Nits**

3. cmake

**Looks good**

4. layout
"""
    got = soften_markdown(raw)
    assert got.startswith("### Summary")
    assert "Now I have" not in got
    assert "### Critical" in got
    assert "### Major" in got
    assert "### Minor" in got
    assert "### Improvement" in got
    assert "Blocking" not in got
    assert "Should fix" not in got


def test_soften_keeps_group_and_issue_heading_levels() -> None:
    raw = """### Summary

Two defects.

### Critical

#### 1. `src/buf.cpp:6` — overflow

**Code**
```cpp
strcpy(dest, src);
```
"""
    got = soften_markdown(raw)
    assert "### Summary" in got
    assert "### Critical" in got
    assert "#### 1. `src/buf.cpp:6` — overflow" in got
    assert "**Code**" in got


def test_ask_note_uses_answer_label() -> None:
    job = _job(trigger="ask", text="Because the lock is per MR.")
    body = format_success(job)
    assert body.startswith("**Creasy — Answer**")
    assert first_command(body) is None


def test_failure_and_cancel_notes_are_not_commands() -> None:
    job = _job(error_message="boom")
    for body in (format_failure(job), format_cancelled(job)):
        assert body.startswith("**Creasy —")
        assert first_command(body) is None
        assert "## " not in body
