from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.webhook import router as gitlab_router
from creasy.api.webhook_azure import router as azure_router
from creasy.azure.identity import azure_project_num
from creasy.jobs.manager import Manager
from conftest import FakeRunner
from test_azure_events import PROJECT, REPO, _pr


class FakeAzure:
    def __init__(self, reviewers: list | None = None) -> None:
        self.reviewers = list(reviewers) if reviewers is not None else [{"id": "bot-id", "displayName": "creasy"}]
        self.reviewer_queue: list[list] = []
        self.list_calls: list[tuple[str, str, int]] = []
        self.apply_calls: list[tuple[str, str]] = []

    def list_reviewers(self, project: str, repo: str, pr_id: int, **kwargs) -> list:
        self.list_calls.append((project, repo, int(pr_id)))
        if self.reviewer_queue:
            return list(self.reviewer_queue.pop(0))
        return list(self.reviewers)

    def apply_collection(self, collection: str = "", web_url: str = "") -> None:
        self.apply_calls.append((collection or "", web_url or ""))
        return None


def _pr_with_bot(**extra):
    pr = _pr(**extra)
    pr["reviewers"] = [{"id": "bot-id", "displayName": "creasy"}]
    return pr


def _app(tmp_config, *, azure=True, reviewers=None):
    tmp_config.review_mention = tmp_config.review_mention or "creasy"
    if azure:
        tmp_config.azure_url = "https://ado.example/tfs/DefaultCollection"
        tmp_config.azure_token = "pat-test"
        tmp_config.azure_webhook_password = "secret"
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.state.azure = FakeAzure(reviewers=reviewers) if azure else None
    app.state.azure_bot_user_id = "bot-id"
    app.include_router(gitlab_router)
    app.include_router(azure_router)
    return app, manager, runner


def _auth(password: str = "secret", user: str = "") -> dict[str, str]:
    blob = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {blob}"}


def test_azure_route_does_not_change_gitlab_webhook(tmp_config):
    app, manager, runner = _app(tmp_config)
    client = TestClient(app)
    payload = {
        "object_kind": "merge_request",
        "object_attributes": {
            "action": "open",
            "iid": 1,
            "target_project_id": 5,
            "source_branch": "f",
            "target_branch": "main",
            "draft": False,
            "title": "Fix login timeout",
            "reviewer_ids": [99],
        },
        "reviewers": [{"id": 99, "username": "creasy"}],
    }
    res = client.post("/creasy/webhook/gitlab", json=payload, headers={"X-Gitlab-Token": "secret"})
    assert res.status_code == 200
    assert res.json()["status"] == "accepted"
    job = manager.store.get(res.json()["job_id"])
    assert job is not None
    assert job.provider == "gitlab"
    runner.release.set()
    manager.shutdown()


def test_azure_payload_on_gitlab_route_is_ignored(tmp_config):
    app, manager, _runner = _app(tmp_config)
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/gitlab",
        json={"eventType": "git.pullrequest.created", "resource": _pr()},
        headers={"X-Gitlab-Token": "secret"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ignored"
    assert manager.store.list_all() == []
    manager.shutdown()


def test_azure_created_accepted(tmp_config):
    app, manager, runner = _app(tmp_config)
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={"eventType": "git.pullrequest.created", "resource": _pr_with_bot()},
        headers=_auth(),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "accepted"
    job = manager.store.get(body["job_id"])
    assert job is not None
    assert job.provider == "azure"
    assert job.azure_project == PROJECT
    assert job.azure_repo == REPO
    assert job.mr_iid == 12
    assert job.project_id == azure_project_num(PROJECT, REPO)
    assert job.trigger == "open"
    runner.release.set()
    manager.shutdown()


def test_reviewer_change_get_confirms_add(tmp_config):
    app, manager, runner = _app(tmp_config, reviewers=[{"id": "bot-id", "displayName": "creasy"}])
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.updated",
            "notificationType": "ReviewersUpdateNotification",
            "message": {"text": "Jamal Hartnett added creasy as a reviewer"},
            "resource": _pr_with_bot(),
        },
        headers=_auth(),
    )
    assert res.json()["status"] == "accepted", res.json()
    assert app.state.azure.list_calls
    job = manager.store.get(res.json()["job_id"])
    assert job is not None
    assert job.trigger == "review"
    runner.release.set()
    manager.shutdown()


def test_reviewer_change_get_without_bot_ignores_stale_add(tmp_config):
    """Hook says added, live GET says the bot is gone → ignore."""
    app, manager, runner = _app(tmp_config, reviewers=[{"id": "alice", "displayName": "Alice"}])
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.updated",
            "notificationType": "ReviewersUpdateNotification",
            "message": {"text": "Jamal Hartnett added creasy as a reviewer"},
            "resource": _pr_with_bot(),
        },
        headers=_auth(),
    )
    assert res.json()["status"] == "ignored"
    assert app.state.azure.list_calls
    manager.shutdown()


def test_reviewer_change_get_listed_generic_message_starts_review(tmp_config):
    app, manager, runner = _app(tmp_config, reviewers=[{"id": "bot-id", "displayName": "creasy"}])
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.updated",
            "notificationType": "ReviewersUpdateNotification",
            "message": {
                "text": (
                    "Berat ERSARI changed the reviewer list for pull request 12 "
                    "(Added overflow) in App"
                )
            },
            "resource": _pr_with_bot(),
        },
        headers=_auth(),
    )
    assert res.json()["status"] == "accepted", res.json()
    assert app.state.azure.list_calls
    runner.release.set()
    manager.shutdown()


def test_second_assign_hook_is_ignored_while_review_runs(tmp_config):
    app, manager, runner = _app(tmp_config, reviewers=[{"id": "bot-id", "displayName": "creasy"}])
    client = TestClient(app)
    payload = {
        "eventType": "git.pullrequest.updated",
        "message": {
            "text": (
                "Berat ERSARI changed the reviewer list for pull request 12 "
                "(Added overflow) in App"
            )
        },
        "resource": _pr_with_bot(),
    }
    first = client.post("/creasy/webhook/azure", json=payload, headers=_auth())
    assert first.json()["status"] == "accepted", first.json()
    second = client.post("/creasy/webhook/azure", json=payload, headers=_auth())
    assert second.json()["status"] == "ignored"
    assert second.json()["message"] == "MR already has a running or queued job"
    reviews = [job for job in manager.store.list_all() if job.trigger == "review"]
    assert len(reviews) == 1
    runner.release.set()
    manager.shutdown()


def test_old_azure_webhook_path_is_gone(tmp_config):
    app, manager, _runner = _app(tmp_config)
    client = TestClient(app)
    res = client.post(
        "/webhook/azure",
        json={"eventType": "git.pullrequest.created", "resource": _pr_with_bot()},
        headers=_auth(),
    )
    assert res.status_code == 404
    manager.shutdown()


def test_azure_mention_comment_is_accepted(tmp_config):
    tmp_config.review_mention = "creasy"
    app, manager, runner = _app(tmp_config)
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={
            "eventType": "git.pullrequest.commented",
            "resource": {
                "comment": {"content": "@creasy /ask check the lock", "author": {"id": "user-1"}},
                "pullRequest": _pr(pullRequestId=14),
            },
        },
        headers=_auth(),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "accepted"
    job = manager.store.get(res.json()["job_id"])
    assert job is not None
    assert job.provider == "azure"
    assert job.trigger == "ask"
    assert job.explicit is True
    runner.release.set()
    manager.shutdown()


def test_azure_update_ignored(tmp_config):
    app, manager, _runner = _app(tmp_config)
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={"eventType": "git.pullrequest.updated", "resource": _pr()},
        headers=_auth(),
    )
    assert res.json()["status"] == "ignored"
    assert manager.store.list_all() == []
    manager.shutdown()


def test_gitlab_secret_does_not_lock_azure_route(tmp_config):
    """Dual install: GitLab WEBHOOK_SECRET must not force Azure Basic auth."""
    tmp_config.webhook_secret = "gitlab-only"
    tmp_config.azure_url = "https://ado.example/tfs/DefaultCollection"
    tmp_config.azure_token = "pat-test"
    tmp_config.azure_webhook_password = ""
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.state.azure = None
    app.state.azure_bot_user_id = "bot-id"
    app.include_router(azure_router)
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={"eventType": "git.pullrequest.created", "resource": _pr_with_bot(pullRequestId=3)},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "accepted"
    runner.release.set()
    manager.shutdown()


def test_azure_secret_required_when_set(tmp_config):
    app, manager, _runner = _app(tmp_config)
    client = TestClient(app)
    res = client.post(
        "/creasy/webhook/azure",
        json={"eventType": "git.pullrequest.created", "resource": _pr()},
    )
    assert res.status_code == 401
    manager.shutdown()


def test_azure_bot_id_is_resolved_before_collection_rebase(tmp_config):
    """Comment classify resolves the bot before git APIs rebase onto /tfs/Collection."""
    tmp_config.azure_url = "https://tfs02.company.com.tr"
    tmp_config.azure_token = "pat-test"
    tmp_config.azure_webhook_password = ""
    tmp_config.review_mention = "creasy"
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    order: list[str] = []

    class Azure:
        base_url = "https://tfs02.company.com.tr"

        def current_user_id(self):
            order.append(f"user:{self.base_url}")
            return None

        def apply_collection(self, collection="", web_url=""):
            order.append(f"apply:{web_url}")
            self.base_url = "https://tfs02.company.com.tr/tfs/ExampleCollection"

    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.state.azure = Azure()
    app.state.azure_bot_user_id = None
    app.include_router(azure_router)
    client = TestClient(app)
    payload = {
        "eventType": "git.pullrequest.commented",
        "resource": {
            "comment": {"content": "@creasy /ask why", "author": {"id": "bot-id"}},
            "pullRequest": _pr(
                pullRequestId=9,
                url="https://tfs02.company.com.tr/tfs/ExampleCollection/App/_git/app/pullrequest/9",
            ),
        },
    }
    res = client.post("/creasy/webhook/azure", json=payload)
    assert res.status_code == 200
    assert res.json()["status"] == "accepted"
    assert order[0].startswith("user:")
    assert order[0] == "user:https://tfs02.company.com.tr"
    assert any(item.startswith("apply:") for item in order)
    assert order.index("user:https://tfs02.company.com.tr") < [
        i for i, item in enumerate(order) if item.startswith("apply:")
    ][0]
    runner.release.set()
    manager.shutdown()


def test_azure_disabled_is_ignored(tmp_config):
    app, manager, _runner = _app(tmp_config, azure=False)
    client = TestClient(app)
    res = client.post("/creasy/webhook/azure", json={"eventType": "git.pullrequest.created", "resource": _pr()})
    assert res.status_code == 200
    assert res.json()["reason"] == "azure not configured"
    manager.shutdown()