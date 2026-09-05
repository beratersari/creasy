from __future__ import annotations

import pytest

from tester.payloads import DEFAULT_MR_IID, DEFAULT_PROJECT_ID, build_payload


def test_default_ids_are_test_project():
    assert DEFAULT_PROJECT_ID == 84969716
    assert DEFAULT_MR_IID == 30


def test_open_and_update_payloads():
    open_ = build_payload("open", project_id=84969716, mr_iid=30)
    assert open_["object_kind"] == "merge_request"
    assert open_["object_attributes"]["action"] == "open"
    assert open_["object_attributes"]["target_project_id"] == 84969716
    update = build_payload("update", project_id=84969716, mr_iid=30)
    assert update["object_attributes"]["oldrev"]


def test_review_and_ask_notes():
    review = build_payload("review", project_id=1, mr_iid=2, note="focus on auth")
    assert review["object_kind"] == "note"
    assert review["object_attributes"]["note"].startswith("/review")
    ask = build_payload("ask", project_id=1, mr_iid=2, note="why this lock?")
    assert ask["object_attributes"]["note"] == "/ask why this lock?"
    reset = build_payload("reset", project_id=1, mr_iid=2)
    assert reset["object_attributes"]["note"] == "/reset"


def test_unknown_event_raises():
    with pytest.raises(ValueError):
        build_payload("nope", project_id=1, mr_iid=1)
