"""Daily-usage issues: what a reviewer or operator actually hits.

These go through classify_webhook, the webhook HTTP handler, or the
full webhook → git → serve → GitLab path. No live GitLab or opencode.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.dashboard import job_matches_query, router as dashboard_router
from creasy.api.webhook import router as webhook_router
from creasy.gitlab.events import Ignore, ReviewTrigger, classify_webhook
from creasy.jobs.manager import Manager
from creasy.jobs.models import JobRecord
from conftest import FakeRunner
from test_events import note_payload
from test_user_facing_integration import _boot, _note_body, _shutdown, _wait_job

ASK_WITH_FINDINGS = """C++17 is required — C++98 is not enough.

```opencoderman-findings
{"findings": [{"path": "src/app.py", "start_line": 2, "end_line": 2, "side": "new", "severity": "major", "title": "C++17 string_view", "body": "string_view does not exist in C++98."}]}
```
"""


def _webhook_app(tmp_config):
    tmp_config.review_mention = tmp_config.review_mention or "creasy"
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.state.bot_user_id = 99
    app.include_router(webhook_router)
    app.include_router(dashboard_router)
    return app, manager, runner


def test_review_with_a_period_starts_a_job(tmp_config):
    """Someone types `/review.` and expects a review, not silence."""
    app, manager, runner = _webhook_app(tmp_config)
    client = TestClient(app)
    res = client.post(
        "/webhook",
        json=note_payload("@creasy /review."),
        headers={"X-Gitlab-Token": "secret"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "accepted", body
    job = manager.store.get(body["job_id"])
    assert job is not None
    assert job.trigger == "review"
    runner.release.set()
    manager.shutdown()


def test_ask_with_a_question_mark_still_asks(tmp_config):
    app, manager, runner = _webhook_app(tmp_config)
    client = TestClient(app)
    res = client.post(
        "/webhook",
        json=note_payload("@creasy /ask? is C++98 enough?"),
        headers={"X-Gitlab-Token": "secret"},
    )
    assert res.json()["status"] == "accepted"
    job = manager.store.get(res.json()["job_id"])
    assert job is not None
    assert job.trigger == "ask"
    assert "C++98" in job.comment_text
    runner.release.set()
    manager.shutdown()


def test_editing_a_review_comment_does_not_start_another_job(tmp_config):
    """GitLab refires the Note Hook with action=update when you edit."""
    app, manager, runner = _webhook_app(tmp_config)
    client = TestClient(app)
    headers = {"X-Gitlab-Token": "secret"}
    created = note_payload("@creasy /review focus on auth")
    created["object_attributes"]["action"] = "create"
    first = client.post("/webhook", json=created, headers=headers)
    assert first.json()["status"] == "accepted"
    edited = note_payload("@creasy /review focus on auth and tests")
    edited["object_attributes"]["action"] = "update"
    second = client.post("/webhook", json=edited, headers=headers)
    assert second.json()["status"] == "ignored"
    assert second.json()["reason"] == "note edit"
    assert len(manager.store.list_all()) == 1
    runner.release.set()
    manager.shutdown()


def test_job_search_matches_what_operators_type():
    job = JobRecord(
        job_id="job_ab",
        mr_key="42-7",
        project_id=42,
        mr_iid=7,
        trigger="review",
        mr_title="Add overflow",
    )
    assert job_matches_query(job, "42-7")
    assert job_matches_query(job, "7")
    assert job_matches_query(job, "!7")
    assert job_matches_query(job, "Add overflow")
    assert job_matches_query(job, "overflow")
    assert not job_matches_query(job, "login")
    assert not job_matches_query(job, "99")


def test_ask_answer_with_findings_opens_new_diff_threads(tmp_config, tmp_path: Path):
    """Daily (seen on 84969716-30): `/ask` 'Does this assume C++17?'
    posted an Answer note *and* new unresolved diff threads.

    The follow-up is a question. Findings in that answer still become
    GitLab discussions the same way a /review does.
    """
    client, manager, state, httpd = _boot(
        tmp_config, tmp_path, assistants=[ASK_WITH_FINDINGS]
    )
    try:
        ask = client.post(
            "/webhook",
            json=_note_body("@creasy /ask Does this change assume C++17, or is C++98 enough?"),
            headers={"X-Gitlab-Token": "secret"},
        )
        assert ask.json()["status"] == "accepted"
        job = _wait_job(manager, ask.json()["job_id"])
        assert job.status == "success", job.error_message
        assert job.trigger == "ask"
        assert state.notes, "expected an Answer note"
        assert any("Answer" in n["body"] for n in state.notes)
        assert "opencoderman-findings" not in state.notes[0]["body"]
        # This is the daily issue: a question opened Changes-tab threads.
        assert state.discussions, " /ask posted diff threads, not just an Answer note"
        assert "string_view" in state.discussions[0]["notes"][0]["body"]
    finally:
        _shutdown(manager, httpd)


def test_edited_note_classify_is_ignore_not_review():
    payload = note_payload("@creasy /reset")
    payload["object_attributes"]["action"] = "update"
    got = classify_webhook(payload, mention_names=["creasy"])
    assert isinstance(got, Ignore)
    create = note_payload("@creasy /reset")
    create["object_attributes"]["action"] = "create"
    got2 = classify_webhook(create, mention_names=["creasy"])
    assert isinstance(got2, ReviewTrigger)
    assert got2.kind == "reset"