from __future__ import annotations

from creasy.review.mention import (
    azure_mention_ids,
    collect_names,
    comment_intent,
    is_usage_note,
    parse_mention_aliases,
    usage_note,
)


def test_parse_and_collect_names() -> None:
    assert parse_mention_aliases(" @creasy, Creasy Bot,") == ["creasy", "Creasy Bot"]
    names = collect_names(["DOMAIN\\creasy"], ["Creasy"])
    assert "DOMAIN\\creasy" in names
    assert "creasy" in names
    assert "Creasy" in names


def test_comment_intent_requires_mention_and_command() -> None:
    assert comment_intent("ping creasy@company.com", ["creasy"]) is None
    assert comment_intent("@other please", ["creasy"]) is None
    assert comment_intent("@creasy-bot", ["creasy"]) is None
    assert comment_intent("hey @creasy check auth", ["creasy"]) == ("usage", "", "")
    assert comment_intent("/review focus", ["creasy"]) == ("usage", "", "")
    got = comment_intent("hey @creasy /review check auth", ["creasy"])
    assert got == ("run", "review", "check auth")
    ask = comment_intent("/ask why @creasy", ["creasy"])
    assert ask == ("run", "ask", "why")


def test_azure_html_mention_needs_command() -> None:
    html = '<a href="#" data-vss-mention="version:2.0,aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee">@X</a>'
    assert azure_mention_ids(html) == ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]
    ids = azure_mention_ids(html)
    assert comment_intent(html, [], mentioned_ids=ids, bot_id="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE") == (
        "usage",
        "",
        "",
    )
    paired = comment_intent(
        html + " /review",
        [],
        mentioned_ids=ids,
        bot_id="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
    )
    assert paired == ("run", "review", "")


def test_usage_note_shows_examples_and_is_ignored() -> None:
    text = usage_note(["creasy"])
    assert "`@creasy /review`" in text
    assert "`@creasy /ask why is this lock held?`" in text
    assert "`@creasy /reset`" in text
    assert is_usage_note(text) is True
    assert is_usage_note("prefix\n" + text) is True
    assert is_usage_note("@creasy /review") is False
