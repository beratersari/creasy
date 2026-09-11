"""Integration tests: reviewer-change hooks are verified with GET, not a cache."""

from __future__ import annotations

from fastapi.testclient import TestClient

import pytest

from creasy.azure.client import AzureError
from creasy.azure.events import reset_reviewer_cache
from test_azure_events import _pr
from test_azure_reviewer_delta import ADDED_BOT, BOT, CHANGED
from test_azure_webhook import FakeAzure, _app, _auth, _pr_with_bot


@pytest.fixture(autouse=True)
def _clear_reviewer_cache() -> None:
    reset_reviewer_cache()
    yield
    reset_reviewer_cache()


def _changed(reviewers: list[dict]) -> dict:
    pr = _pr()
    pr["reviewers"] = reviewers
    return {
        "eventType": "git.pullrequest.updated",
        "notificationType": "ReviewersUpdateNotification",
        "message": {"text": CHANGED},
        "resource": pr,
    }


def test_comment_does_not_use_reviewer_cache(tmp_config):
    app, manager, runner = _app(tmp_config)
    client = TestClient(app)
    created = client.post(
        "/creasy/webhook/azure",
        json={"eventType": "git.pullrequest.created", "resource": _pr_with_bot()},
        headers=_auth(),
    )
    assert created.json()["status"] == "accepted"
    runner.release.set()
    thin = _pr()
    thin.pop("reviewers", None)
    comment = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.commented",
            "resource": {
                "comment": {"content": "@creasy /ask why dest?", "author": {"id": "user-1"}},
                "pullRequest": thin,
            },
        },
        headers=_auth(),
    )
    assert comment.json()["status"] in {"accepted", "queued"}
    runner.release.set()
    app.state.azure.reviewers = []
    later = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.updated",
            "notificationType": "ReviewersUpdateNotification",
            "message": {"text": "Dev removed Creasy as a reviewer"},
            "resource": _pr_with_bot(),
        },
        headers=_auth(),
    )
    assert later.json()["status"] == "ignored"
    manager.shutdown()


def test_get_failure_does_not_start_a_review(tmp_config):
    app, manager, runner = _app(tmp_config)

    def boom(project, repo, pr_id):
        raise AzureError("tfs down")

    app.state.azure.list_reviewers = boom
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.updated",
            "notificationType": "ReviewersUpdateNotification",
            "message": {"text": ADDED_BOT},
            "resource": _pr_with_bot(),
        },
        headers=_auth(),
    )
    assert res.json()["status"] == "ignored"
    assert res.json()["reason"] == "reviewers GET failed"
    manager.shutdown()


def test_removed_in_pr_title_does_not_block_real_assign(tmp_config):
    app, manager, runner = _app(tmp_config, reviewers=[BOT])
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.updated",
            "notificationType": "ReviewersUpdateNotification",
            "message": {
                "text": (
                    "Dev added Creasy as a reviewer for pull request 12 "
                    "(Removed overflow) in App"
                )
            },
            "resource": _pr_with_bot(),
        },
        headers=_auth(),
    )
    assert res.json()["status"] == "accepted", res.json()
    runner.release.set()
    manager.shutdown()


def test_fake_azure_is_used_for_get(tmp_config):
    assert isinstance(_app(tmp_config)[0].state.azure, FakeAzure)


def test_reviewer_get_retries_until_bot_listed(tmp_config, monkeypatch):
    import creasy.api.webhook_azure as hook

    monkeypatch.setattr(hook, "REVIEWER_GET_RETRY_DELAYS", (0, 0))
    app, manager, runner = _app(tmp_config)
    app.state.azure.reviewer_queue = [
        [{"id": "alice", "displayName": "Alice"}],
        [{"id": "bot-id", "displayName": "creasy"}],
    ]
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.updated",
            "notificationType": "ReviewersUpdateNotification",
            "message": {"text": ADDED_BOT},
            "resource": _pr_with_bot(),
        },
        headers=_auth(),
    )
    assert res.json()["status"] == "accepted", res.json()
    assert len(app.state.azure.list_calls) == 2
    runner.release.set()
    manager.shutdown()


def test_reviewer_get_does_not_retry_remove(tmp_config, monkeypatch):
    import creasy.api.webhook_azure as hook

    monkeypatch.setattr(hook, "REVIEWER_GET_RETRY_DELAYS", (0, 0))
    app, manager, runner = _app(tmp_config, reviewers=[{"id": "alice", "displayName": "Alice"}])
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.updated",
            "notificationType": "ReviewersUpdateNotification",
            "message": {"text": "Dev removed Creasy as a reviewer"},
            "resource": _pr_with_bot(),
        },
        headers=_auth(),
    )
    assert res.json()["status"] == "ignored"
    assert len(app.state.azure.list_calls) == 1
    manager.shutdown()


def test_reviewer_get_retries_transient_failure(tmp_config, monkeypatch):
    import creasy.api.webhook_azure as hook

    monkeypatch.setattr(hook, "REVIEWER_GET_RETRY_DELAYS", (0,))
    app, manager, runner = _app(tmp_config)
    calls = {"n": 0}

    def flaky(project, repo, pr_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise AzureError("503")
        return [{"id": "bot-id", "displayName": "creasy"}]

    app.state.azure.list_reviewers = flaky
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.updated",
            "notificationType": "ReviewersUpdateNotification",
            "message": {"text": ADDED_BOT},
            "resource": _pr_with_bot(),
        },
        headers=_auth(),
    )
    assert res.json()["status"] == "accepted", res.json()
    assert calls["n"] == 2
    runner.release.set()
    manager.shutdown()


def test_host_only_env_rebases_from_pr_web_url(tmp_config):
    tmp_config.azure_url = "https://tfs02.company.com.tr"
    app, manager, runner = _app(tmp_config, reviewers=[BOT])
    pr = _pr_with_bot()
    pr["_links"] = {
        "web": {
            "href": (
                "https://tfs02.company.com.tr/tfs/ExampleCollection/"
                "App/_git/repo/pullrequest/12"
            )
        }
    }
    pr["repository"]["remoteUrl"] = (
        "https://tfs02.company.com.tr/tfs/ExampleCollection/App/_git/repo"
    )
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.updated",
            "notificationType": "ReviewersUpdateNotification",
            "message": {"text": ADDED_BOT},
            "resource": pr,
            "resourceContainers": {
                "collection": {"baseUrl": "https://tfs02.company.com.tr/tfs/ExampleCollection/"}
            },
        },
        headers=_auth(),
    )
    assert res.json()["status"] == "accepted", res.json()
    assert app.state.azure.apply_calls
    collection, web = app.state.azure.apply_calls[0]
    assert "ExampleCollection" in collection or "ExampleCollection" in web
    runner.release.set()
    manager.shutdown()
