from __future__ import annotations

from creasy.gitlab.client import MergeRequest
from creasy.review.prompt import build_ask_prompt, build_review_prompt
from creasy.workspace.gitops import DiffIndex


def _mr() -> MergeRequest:
    return MergeRequest(
        project_id=1,
        iid=2,
        title="Add login",
        description="desc",
        author="berat",
        source_branch="feat",
        target_branch="main",
        sha="aaa",
        base_sha="bbb",
        start_sha="bbb",
        web_url="http://gl/mr/2",
        http_url="http://gl/repo.git",
        draft=False,
        state="opened",
    )


def test_review_prompt_has_map_not_full_diff():
    index = DiffIndex(
        merge_base="bbb",
        stat=" src/a.py | 3 +++\n 1 file changed",
        paths=["src/a.py"],
        statuses={"src/a.py": "M"},
    )
    prompt = build_review_prompt(_mr(), index, extra_notes="be strict", rules="No bare except")
    assert "bbb" in prompt
    assert "src/a.py" in prompt
    assert "git diff bbb...HEAD" in prompt
    assert "Do not assume this prompt contains hunks" in prompt
    assert "@@ " not in prompt
    assert "No bare except" in prompt
    assert "be strict" in prompt
    assert "Required reply format" not in prompt
    assert "**Critical**" not in prompt
    assert "**Blocking**" not in prompt
    assert "**Why it is an issue and where**" not in prompt
    assert "**Suggested fix**" not in prompt


def test_ask_prompt_is_question():
    text = build_ask_prompt("why this lock?")
    assert text == "why this lock?"
    with_ctx = build_ask_prompt("why?", mr=_mr(), index=DiffIndex("b", "stat", ["a.py"], {"a.py": "M"}), include_context=True)
    assert "why?" in with_ctx
    assert "Add login" in with_ctx
    moved = build_ask_prompt(
        "why?",
        mr=_mr(),
        index=DiffIndex("b", "stat", ["a.py"], {"a.py": "M"}),
        sha_changed=True,
        previous_sha="oldsha",
    )
    assert "oldsha" in moved
    assert "aaa" in moved
    assert "why?" in moved


def test_hang_resume_is_not_the_review_prompt():
    from creasy.review.prompt import hang_resume_prompt

    text = hang_resume_prompt()
    assert "already posted" in text.lower()
    assert "@@ " not in text
    assert "Do not assume this prompt contains hunks" not in text
