"""Integration tests for user-facing assign/usage bugs."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from creasy.azure.events import reset_reviewer_cache
from test_azure_webhook import _app, _auth, _pr_with_bot

CHANGED = (
    "Berat ERSARI changed the reviewer list for pull request 12 "
    "(Added overflow) in App"
)
BOT = {"id": "bot-id", "displayName": "creasy"}


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    reset_reviewer_cache()
    yield
    reset_reviewer_cache()


def test_usage_job_must_not_drop_a_tfs_assign(tmp_config):
    """A running usage note must not swallow the following assign."""
    tmp_config.review_mention = "creasy"
    app, manager, runner = _app(tmp_config, reviewers=[BOT])
    client = TestClient(app)
    usage = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.commented",
            "resource": {
                "comment": {"content": "@creasy please look", "author": {"id": "user-1"}},
                "pullRequest": _pr_with_bot(),
            },
        },
        headers=_auth(),
    )
    assert usage.json()["status"] == "accepted", usage.json()
    assign = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.updated",
            "message": {"text": CHANGED},
            "resource": _pr_with_bot(),
        },
        headers=_auth(),
    )
    assert assign.json()["status"] in {"accepted", "queued"}, assign.json()
    reviews = [job for job in manager.store.list_all() if job.trigger == "review"]
    assert reviews, "assign was dropped while a usage job was running"
    runner.release.set()
    manager.shutdown()
