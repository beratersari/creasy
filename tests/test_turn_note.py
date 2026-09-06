"""Overview note is this turn's review, not a later wrap-up on ses_*."""

from __future__ import annotations

from creasy.opencode.session import last_assistant_text, looks_like_review, turn_assistant_text
from creasy.review.prompt import HANG_RESUME


def _msg(role: str, text: str) -> dict:
    return {"info": {"role": role}, "parts": [{"type": "text", "text": text}]}


REVIEW = """### Summary
1 Critical.

#### 1. `src/buf.cpp:6` — overflow

strcpy overflows.
"""

WRAP = "The review is complete — no further steps remain on my side."
ASK = "The lock is held across the wait because the waiter owns it."


def test_last_assistant_text_is_still_the_final_message() -> None:
    messages = [_msg("assistant", REVIEW), _msg("assistant", WRAP)]
    assert last_assistant_text(messages) == WRAP


def test_turn_picks_the_review_when_a_wrapup_follows() -> None:
    messages = [_msg("user", "please review"), _msg("assistant", REVIEW), _msg("assistant", WRAP)]
    got = turn_assistant_text(messages, prefer_review=True)
    assert "strcpy overflows" in got
    assert "complete" not in got


def test_turn_does_not_republish_a_previous_jobs_review() -> None:
    messages = [
        _msg("user", "first review"),
        _msg("assistant", REVIEW),
        _msg("user", "review again"),
        _msg("assistant", WRAP),
    ]
    got = turn_assistant_text(messages, prefer_review=True)
    assert got == WRAP
    assert looks_like_review(REVIEW)
    assert not looks_like_review(WRAP)


def test_ask_uses_the_last_text_of_this_turn() -> None:
    messages = [
        _msg("user", "review"),
        _msg("assistant", REVIEW),
        _msg("user", "why the lock?"),
        _msg("assistant", ASK),
    ]
    assert turn_assistant_text(messages, prefer_review=False) == ASK


def test_hang_resume_is_not_a_new_turn() -> None:
    messages = [
        _msg("user", "full review prompt"),
        _msg("assistant", REVIEW),
        _msg("user", HANG_RESUME),
        _msg("assistant", WRAP),
    ]
    got = turn_assistant_text(messages, prefer_review=True)
    assert "strcpy overflows" in got
