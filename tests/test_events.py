from __future__ import annotations

from creasy.gitlab.events import CleanupTrigger, Ignore, ReviewTrigger, classify_webhook, first_command
from creasy.review.mention import usage_note


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
    note = classify_webhook(note_payload("@creasy /review"), mention_names=["creasy"])
    assert isinstance(note, ReviewTrigger)
    assert note.title == ""
    titled = note_payload("@creasy /ask why")
    titled["merge_request"]["title"] = "Fix login timeout"
    ask = classify_webhook(titled, mention_names=["creasy"])
    assert isinstance(ask, ReviewTrigger)
    assert ask.title == "Fix login timeout"


def test_update_with_oldrev_ignored():
    got = classify_webhook(mr_payload("update", oldrev="abc123"))
    assert isinstance(got, Ignore)
    assert got.reason == "action=update"


def test_update_without_oldrev_ignored():
    got = classify_webhook(mr_payload("update"))
    assert isinstance(got, Ignore)
    assert got.reason == "action=update"


def test_mark_as_ready_does_not_enqueue():
    payload = mr_payload("update", draft=False)
    payload["changes"] = {"draft": {"previous": True, "current": False}}
    got = classify_webhook(payload, skip_drafts=True)
    assert isinstance(got, Ignore)
    assert got.reason == "action=update"


def test_close_and_merge_cleanup():
    close = classify_webhook(mr_payload("close"))
    merge = classify_webhook(mr_payload("merge"))
    assert isinstance(close, CleanupTrigger)
    assert isinstance(merge, CleanupTrigger)
    assert merge.action == "merge"


def test_draft_auto_skipped_explicit_allowed():
    draft = classify_webhook(mr_payload("open", draft=True), skip_drafts=True)
    assert isinstance(draft, Ignore)
    note = classify_webhook(
        note_payload("@creasy /review please", draft=True),
        skip_drafts=True,
        mention_names=["creasy"],
    )
    assert isinstance(note, ReviewTrigger)
    assert note.explicit is True


def test_diff_note_keeps_range_and_user_question():
    payload = note_payload("This overflow looks wrong.\n@creasy /ask is this UB?")
    payload["object_attributes"]["type"] = "DiffNote"
    payload["object_attributes"]["discussion_id"] = "disc_range"
    payload["object_attributes"]["position"] = {
        "new_path": "src/buf.cpp",
        "new_line": 12,
        "line_range": {"start": {"new_line": 12}, "end": {"new_line": 18}},
    }
    got = classify_webhook(payload, mention_names=["creasy"])
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "ask"
    assert got.discussion_id == "disc_range"
    assert got.comment_path == "src/buf.cpp"
    assert got.comment_start_line == 12
    assert got.comment_end_line == 18
    assert "overflow" in got.comment_text
    assert "UB" in got.comment_text


def test_note_keeps_discussion_id_for_thread_reply():
    payload = note_payload("@creasy /review focus on auth")
    payload["object_attributes"]["discussion_id"] = "abc123def"
    got = classify_webhook(payload, mention_names=["creasy"])
    assert isinstance(got, ReviewTrigger)
    assert got.discussion_id == "abc123def"
    open_ = classify_webhook(mr_payload("open"))
    assert isinstance(open_, ReviewTrigger)
    assert open_.discussion_id == ""


def test_review_and_ask_notes():
    review = classify_webhook(note_payload("@creasy /review focus on auth"), mention_names=["creasy"])
    ask = classify_webhook(note_payload("@creasy /ask why is this nullable?"), mention_names=["creasy"])
    assert isinstance(review, ReviewTrigger)
    assert review.kind == "review"
    assert review.comment_text == "focus on auth"
    assert isinstance(ask, ReviewTrigger)
    assert ask.kind == "ask"
    assert "nullable" in ask.comment_text


def test_empty_ask_ignored():
    got = classify_webhook(note_payload("@creasy /ask   "), mention_names=["creasy"])
    assert isinstance(got, Ignore)


def test_reset_note():
    got = classify_webhook(note_payload("@creasy /reset"), mention_names=["creasy"])
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "reset"
    assert got.explicit is True
    extra = classify_webhook(note_payload("@creasy /reset please"), mention_names=["creasy"])
    assert isinstance(extra, ReviewTrigger)
    assert extra.kind == "reset"
    draft = classify_webhook(
        note_payload("@creasy /reset", draft=True),
        skip_drafts=True,
        mention_names=["creasy"],
    )
    assert isinstance(draft, ReviewTrigger)
    assert draft.explicit is True


def test_bot_note_ignored():
    got = classify_webhook(note_payload("@creasy /review"), bot_user_id=9, mention_names=["creasy"])
    assert isinstance(got, ReviewTrigger)
    bot = classify_webhook(note_payload("@creasy /review"), bot_user_id=1, mention_names=["creasy"])
    assert isinstance(bot, Ignore)


def test_unrelated_and_preview_ignored():
    assert isinstance(classify_webhook(note_payload("looks good")), Ignore)
    assert isinstance(classify_webhook(note_payload("nice preview of the UI")), Ignore)
    assert isinstance(classify_webhook(note_payload("please reset this")), Ignore)
    assert first_command("please /review this") == ("review", "this")
    assert first_command("please /reset this") == ("reset", "this")


def test_pasted_usage_note_is_ignored():
    got = classify_webhook(note_payload(usage_note(["creasy"])), mention_names=["creasy"])
    assert isinstance(got, Ignore)
    assert got.reason == "usage note"
    wrapped = classify_webhook(
        note_payload("<p>" + usage_note(["creasy"]) + "</p>"),
        mention_names=["creasy"],
    )
    assert isinstance(wrapped, Ignore)


def test_mention_or_command_alone_is_usage():
    mention = classify_webhook(note_payload("@creasy please check auth"), mention_names=["creasy"])
    assert isinstance(mention, ReviewTrigger)
    assert mention.kind == "usage"
    command = classify_webhook(note_payload("/review focus on auth"), mention_names=["creasy"])
    assert isinstance(command, ReviewTrigger)
    assert command.kind == "usage"
    other = classify_webhook(note_payload("@someone else"), mention_names=["creasy"])
    assert isinstance(other, Ignore)
    email = classify_webhook(note_payload("mail creasy@company.com"), mention_names=["creasy"])
    assert isinstance(email, Ignore)


def test_mention_plus_command_runs():
    got = classify_webhook(
        note_payload("@creasy /review please check auth"),
        mention_names=["creasy", "Creasy Bot"],
    )
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "review"
    assert got.explicit is True
    assert "auth" in got.comment_text
    spaced = classify_webhook(note_payload("@Creasy Bot /ask why"), mention_names=["Creasy Bot"])
    assert isinstance(spaced, ReviewTrigger)
    assert spaced.kind == "ask"


def test_first_command_wins_over_mention():
    got = classify_webhook(note_payload("@creasy /ask why this lock?"), mention_names=["creasy"])
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "ask"


def test_first_command_wins():
    got = classify_webhook(note_payload("@creasy /ask first then /review later"), mention_names=["creasy"])
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "ask"
    got2 = classify_webhook(note_payload("@creasy /review now /ask later"), mention_names=["creasy"])
    assert isinstance(got2, ReviewTrigger)
    assert got2.kind == "review"
    got3 = classify_webhook(note_payload("@creasy /reset then /review"), mention_names=["creasy"])
    assert isinstance(got3, ReviewTrigger)
    assert got3.kind == "reset"


def test_reopen_ignored():
    got = classify_webhook(mr_payload("reopen"))
    assert isinstance(got, Ignore)
    assert got.reason == "action=reopen"


def test_trailing_punctuation_still_runs_the_command():
    """People type /review. or /ask? at the end of a sentence."""
    review = classify_webhook(note_payload("@creasy /review."), mention_names=["creasy"])
    assert isinstance(review, ReviewTrigger)
    assert review.kind == "review"
    assert review.comment_text == ""
    review_notes = classify_webhook(note_payload("@creasy /review. focus on auth"), mention_names=["creasy"])
    assert isinstance(review_notes, ReviewTrigger)
    assert review_notes.comment_text == "focus on auth"
    ask = classify_webhook(note_payload("@creasy /ask? why is this nullable?"), mention_names=["creasy"])
    assert isinstance(ask, ReviewTrigger)
    assert ask.kind == "ask"
    assert "nullable" in ask.comment_text
    assert isinstance(classify_webhook(note_payload("@creasy /ask?"), mention_names=["creasy"]), Ignore)
    reset = classify_webhook(note_payload("@creasy /reset!"), mention_names=["creasy"])
    assert isinstance(reset, ReviewTrigger)
    assert reset.kind == "reset"
    assert first_command("please /review.") == ("review", "")
    assert first_command("/reviews") is None


def test_edited_note_is_ignored():
    """GitLab 16.11+ refires the Note Hook with action=update on edit."""
    payload = note_payload("@creasy /review focus on auth")
    payload["object_attributes"]["action"] = "update"
    got = classify_webhook(payload, mention_names=["creasy"])
    assert isinstance(got, Ignore)
    assert "edit" in got.reason
    created = note_payload("@creasy /review focus on auth")
    created["object_attributes"]["action"] = "create"
    assert isinstance(classify_webhook(created, mention_names=["creasy"]), ReviewTrigger)
    # Older GitLab omits action — still a new comment.
    assert isinstance(classify_webhook(note_payload("@creasy /review"), mention_names=["creasy"]), ReviewTrigger)
