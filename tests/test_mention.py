from __future__ import annotations

from creasy.review.mention import (
    USAGE_MARKER,
    azure_mention_ids,
    collect_names,
    comment_intent,
    extract_mentioned_names,
    is_usage_note,
    parse_mention_aliases,
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
    assert comment_intent("hey @creasy check auth", ["creasy"]) is None
    assert comment_intent("/review focus", ["creasy"]) is None
    leftover = comment_intent("hey @creasy /review check auth", ["creasy"])
    assert leftover == ("run", "review", "check auth")
    promoted = comment_intent("@creasy /ask please do a new review", ["creasy"])
    assert promoted == ("run", "review", "please do a new review")
    assert comment_intent("/ask why", ["creasy"]) is None
    ask = comment_intent("/ask why @creasy", ["creasy"])
    assert ask == ("run", "ask", "why")
    domain = comment_intent(r"@company\mberatersari /ask asdfasf", ["mberatersari"])
    assert domain == ("run", "ask", "asdfasf")


def test_extract_mentioned_names_from_plain_and_html() -> None:
    assert extract_mentioned_names('@mberatersari /ask "why"') == ["mberatersari"]
    assert extract_mentioned_names(r"@company\mberatersari /ask why") == [r"company\mberatersari"]
    html = (
        '<a href="#" data-vss-mention="version:2.0,aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee">'
        '@Berat ERSARI</a>/ask "why this lock?"'
    )
    assert extract_mentioned_names(html) == ["Berat ERSARI"]


def test_comment_intent_ask_without_space_after_mention() -> None:
    assert comment_intent("@creasy/ask why", ["creasy"]) == ("run", "ask", "why")
    quoted = comment_intent('@creasy /ask "question"', ["creasy"])
    assert quoted == ("run", "ask", '"question"')
    html = (
        '<a href="#" data-vss-mention="version:2.0,aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee">'
        '@Berat ERSARI</a>/ask "why this lock?"'
    )
    got = comment_intent(html, ["Berat ERSARI"])
    assert got == ("run", "ask", '"why this lock?"')
    display = comment_intent("@Berat ERSARI /ask why", ["mberatersari", "Berat ERSARI"])
    assert display == ("run", "ask", "why")


def test_azure_html_mention_needs_command() -> None:
    html = '<a href="#" data-vss-mention="version:2.0,aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee">@X</a>'
    assert azure_mention_ids(html) == ["aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"]
    ids = azure_mention_ids(html)
    assert comment_intent(html, [], mentioned_ids=ids, bot_id="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE") is None
    paired = comment_intent(
        html + " /ask why",
        [],
        mentioned_ids=ids,
        bot_id="AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",
    )
    assert paired == ("run", "ask", "why")


def test_old_usage_note_marker_is_detected() -> None:
    text = f"{USAGE_MARKER}\n@creasy /ask why is this lock held?"
    assert is_usage_note(text) is True
    assert is_usage_note("prefix\n" + text) is True
    assert is_usage_note("@creasy /ask why") is False
