from __future__ import annotations

from creasy.gitlab.events import CleanupTrigger, Ignore, ReviewTrigger, classify_webhook, first_command


def mr_payload(action: str, **attrs):
    object_attributes = {
        "action": action,
        "iid": 7,
        "target_project_id": 42,
        "source_branch": "feat",
        "target_branch": "main",
        "url": "http://gl/mr/7",
        "draft": False,
        **attrs,
    }
    return {"object_kind": "merge_request", "object_attributes": object_attributes}


def note_payload(note: str, *, user_id: int = 1, draft: bool = False):
    return {
        "object_kind": "note",
        "user": {"id": user_id},
        "object_attributes": {"noteable_type": "MergeRequest", "note": note},
        "merge_request": {
            "iid": 7,
            "target_project_id": 42,
            "source_branch": "feat",
            "target_branch": "main",
            "url": "http://gl/mr/7",
            "draft": draft,
        },
    }


def test_open_enqueues_review():
    got = classify_webhook(mr_payload("open"))
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "open"
    assert got.project_id == 42
    assert got.mr_iid == 7
    assert got.explicit is False


def test_mr_title_comes_from_webhook():
    got = classify_webhook(mr_payload("open", title="Fix login timeout"))
    assert isinstance(got, ReviewTrigger)
    assert got.title == "Fix login timeout"
    note = classify_webhook(note_payload("/review"))
    assert isinstance(note, ReviewTrigger)
    assert note.title == ""
    titled = note_payload("/ask why")
    titled["merge_request"]["title"] = "Fix login timeout"
    ask = classify_webhook(titled)
    assert isinstance(ask, ReviewTrigger)
    assert ask.title == "Fix login timeout"


def test_update_with_oldrev():
    got = classify_webhook(mr_payload("update", oldrev="abc123"))
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "update"


def test_update_without_oldrev_ignored():
    got = classify_webhook(mr_payload("update"))
    assert isinstance(got, Ignore)
    assert "oldrev" in got.reason


def test_close_and_merge_cleanup():
    close = classify_webhook(mr_payload("close"))
    merge = classify_webhook(mr_payload("merge"))
    assert isinstance(close, CleanupTrigger)
    assert isinstance(merge, CleanupTrigger)
    assert merge.action == "merge"


def test_draft_auto_skipped_explicit_allowed():
    draft = classify_webhook(mr_payload("open", draft=True), skip_drafts=True)
    assert isinstance(draft, Ignore)
    note = classify_webhook(note_payload("/review please", draft=True), skip_drafts=True)
    assert isinstance(note, ReviewTrigger)
    assert note.explicit is True


def test_review_and_ask_notes():
    review = classify_webhook(note_payload("/review focus on auth"))
    ask = classify_webhook(note_payload("/ask why is this nullable?"))
    assert isinstance(review, ReviewTrigger)
    assert review.kind == "review"
    assert review.comment_text == "focus on auth"
    assert isinstance(ask, ReviewTrigger)
    assert ask.kind == "ask"
    assert "nullable" in ask.comment_text


def test_empty_ask_ignored():
    got = classify_webhook(note_payload("/ask   "))
    assert isinstance(got, Ignore)


def test_bot_note_ignored():
    got = classify_webhook(note_payload("/review"), bot_user_id=9)
    assert isinstance(got, ReviewTrigger)
    bot = classify_webhook(note_payload("/review"), bot_user_id=1)
    assert isinstance(bot, Ignore)


def test_unrelated_and_preview_ignored():
    assert isinstance(classify_webhook(note_payload("looks good")), Ignore)
    assert isinstance(classify_webhook(note_payload("nice preview of the UI")), Ignore)
    assert first_command("please /review this") == ("review", "this")


def test_first_command_wins():
    got = classify_webhook(note_payload("/ask first then /review later"))
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "ask"
    got2 = classify_webhook(note_payload("/review now /ask later"))
    assert isinstance(got2, ReviewTrigger)
    assert got2.kind == "review"


def test_reopen():
    got = classify_webhook(mr_payload("reopen"))
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "reopen"
