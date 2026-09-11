"""Azure/TFS reviewer add vs remove — classify + GET verification."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from creasy.azure.events import classify_azure_webhook, reset_reviewer_cache
from creasy.gitlab.events import Ignore, ReviewTrigger, classify_webhook
from test_azure_events import _pr
from test_azure_webhook import _app, _auth, _pr_with_bot
from test_events import mr_payload


BOT_ID = "bot-id"
BOT = {
    "id": BOT_ID,
    "displayName": "Creasy",
    "uniqueName": r"company\mberatersari",
}
ALICE = {"id": "alice-id", "displayName": "Alice"}
NAMES = ["creasy", "mberatersari", "Berat ERSARI"]

CHANGED = (
    "Berat ERSARI changed the reviewer list for pull request 12 "
    "(Added overflow) in App"
)
ADDED_BOT = "Dev added Creasy as a reviewer"
ADDED_SELF = r"company\mberatersari added yourself as a reviewer"
ADDED_ALICE = "Dev added Alice as a reviewer"
ADDED_REQUIRED = "Dev added Creasy as a required reviewer"
REMOVED_BOT = "Dev removed Creasy as a reviewer"
REMOVED_SELF = r"company\mberatersari removed themselves as a reviewer"
REMOVED_ALICE = "Dev removed Alice as a reviewer"
UNASSIGNED_BOT = "Dev unassigned Creasy from the reviewer list"
UNSTRUCTURED_BOT = "Reviewers were updated: Creasy was added as a reviewer"
UNSTRUCTURED_ALICE = "Reviewers were updated: Alice was added as a reviewer"
VOTE = "Jamal Hartnett voted on the pull request"


def _update(message: str, reviewers: list[dict], *, notify: str = "ReviewersUpdateNotification") -> dict:
    pr = _pr()
    pr["reviewers"] = reviewers
    return {
        "eventType": "git.pullrequest.updated",
        "notificationType": notify,
        "message": {"text": message},
        "resource": pr,
    }


def _go(payload: dict):
    return classify_azure_webhook(
        payload,
        bot_user_id=BOT_ID,
        mention_names=NAMES,
    )


def _is_review(got) -> bool:
    return isinstance(got, ReviewTrigger) and got.kind == "review"


@pytest.fixture(autouse=True)
def _clear_reviewer_cache() -> None:
    reset_reviewer_cache()
    yield
    reset_reviewer_cache()


@pytest.mark.parametrize(
    "message,reviewers,expect_review",
    [
        (ADDED_BOT, [BOT], True),
        (ADDED_BOT, [BOT, ALICE], True),
        (ADDED_BOT, [ALICE], False),
        (ADDED_SELF, [BOT], True),
        (ADDED_REQUIRED, [BOT], True),
        (ADDED_ALICE, [BOT, ALICE], False),
        (ADDED_ALICE, [ALICE], False),
        (REMOVED_BOT, [ALICE], False),
        (REMOVED_BOT, [], False),
        (REMOVED_SELF, [], False),
        (REMOVED_ALICE, [BOT], False),
        (UNASSIGNED_BOT, [ALICE], False),
        (CHANGED, [BOT], True),
        (CHANGED, [BOT, ALICE], True),
        (CHANGED, [ALICE], False),
        (CHANGED, [], False),
        (UNSTRUCTURED_BOT, [BOT], True),
        (UNSTRUCTURED_ALICE, [BOT, ALICE], False),
        (VOTE, [BOT], False),
    ],
    ids=[
        "added-bot-listed",
        "added-bot-with-alice",
        "added-bot-get-says-gone",
        "added-yourself",
        "added-bot-required",
        "added-alice-bot-listed",
        "added-alice-only",
        "removed-bot",
        "removed-bot-empty",
        "removed-themselves",
        "removed-alice",
        "unassigned-bot",
        "changed-list-bot-listed",
        "changed-list-bot-and-alice",
        "changed-list-alice-only",
        "changed-list-empty",
        "unstructured-bot-added",
        "unstructured-alice-added",
        "vote",
    ],
)
def test_tfs_reviewer_message_matrix(message, reviewers, expect_review):
    notify = "ReviewerVoteNotification" if message == VOTE else "ReviewersUpdateNotification"
    got = _go(_update(message, reviewers, notify=notify))
    assert _is_review(got) is expect_review, (type(got).__name__, getattr(got, "reason", got))


def test_html_added_bot_still_starts_review():
    pr = _pr()
    pr["reviewers"] = [BOT]
    payload = {
        "eventType": "git.pullrequest.updated",
        "notificationType": "ReviewersUpdateNotification",
        "message": {"html": r'<div>Dev added <a>Creasy</a> as a reviewer</div>'},
        "resource": pr,
    }
    assert _is_review(_go(payload))


def test_webhook_get_verifies_add(tmp_config):
    app, manager, runner = _app(tmp_config, reviewers=[BOT])
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json=_update(ADDED_BOT, [ALICE]),
        headers=_auth(),
    )
    assert res.json()["status"] == "accepted", res.json()
    assert app.state.azure.list_calls
    runner.release.set()
    manager.shutdown()


def test_webhook_get_rejects_add_when_bot_not_listed(tmp_config):
    app, manager, runner = _app(tmp_config, reviewers=[ALICE])
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json=_update(ADDED_BOT, [BOT]),
        headers=_auth(),
    )
    assert res.json()["status"] == "ignored"
    assert app.state.azure.list_calls
    manager.shutdown()


def test_webhook_get_does_not_run_on_comment(tmp_config):
    tmp_config.review_mention = "creasy"
    app, manager, runner = _app(tmp_config)
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.commented",
            "resource": {
                "comment": {"content": "@creasy /ask why dest?", "author": {"id": "user-1"}},
                "pullRequest": _pr_with_bot(),
            },
        },
        headers=_auth(),
    )
    assert res.json()["status"] == "accepted"
    assert app.state.azure.list_calls == []
    runner.release.set()
    manager.shutdown()


def test_gitlab_unassign_teammate_while_bot_stays_is_ignored():
    payload = mr_payload("update")
    payload["changes"] = {
        "reviewers": {
            "previous": [{"id": 99, "username": "creasy"}, {"id": 4, "username": "alice"}],
            "current": [{"id": 99, "username": "creasy"}],
        }
    }
    got = classify_webhook(payload, bot_user_id=99, mention_names=["creasy"])
    assert isinstance(got, Ignore)


def test_gitlab_unassign_bot_is_ignored():
    payload = mr_payload("update")
    payload["changes"] = {
        "reviewers": {
            "previous": [{"id": 99, "username": "creasy"}, {"id": 4, "username": "alice"}],
            "current": [{"id": 4, "username": "alice"}],
        }
    }
    got = classify_webhook(payload, bot_user_id=99, mention_names=["creasy"])
    assert isinstance(got, Ignore)


def test_gitlab_assign_bot_still_starts_review():
    payload = mr_payload("update")
    payload["changes"] = {
        "reviewers": {
            "previous": [{"id": 4, "username": "alice"}],
            "current": [{"id": 4, "username": "alice"}, {"id": 99, "username": "creasy"}],
        }
    }
    got = classify_webhook(payload, bot_user_id=99, mention_names=["creasy"])
    assert isinstance(got, ReviewTrigger)
    assert got.kind == "review"


def test_gitlab_incomplete_reviewer_delta_is_ignored():
    payload = mr_payload("update")
    payload["changes"] = {"reviewers": {"current": [{"id": 99, "username": "creasy"}]}}
    got = classify_webhook(payload, bot_user_id=99, mention_names=["creasy"])
    assert isinstance(got, Ignore)
