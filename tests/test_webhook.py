from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.dashboard import router as dashboard_router
from creasy.api.health import router as health_router
from creasy.api.webhook import router as webhook_router
from creasy.jobs.manager import Manager
from conftest import FakeRunner


def _app(tmp_config):
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.state.bot_user_id = 99
    app.include_router(health_router)
    app.include_router(webhook_router)
    app.include_router(dashboard_router)
    return app, manager, runner


def test_secret_required(tmp_config):
    app, _, _ = _app(tmp_config)
    client = TestClient(app)
    res = client.post("/webhook", json={"object_kind": "merge_request"})
    assert res.status_code == 401


def test_open_accepted(tmp_config):
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
        },
    }
    res = client.post("/webhook", json=payload, headers={"X-Gitlab-Token": "secret"})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "accepted"
    assert "job_id" in body
    job = manager.store.get(body["job_id"])
    assert job is not None
    assert job.mr_title == "Fix login timeout"
    assert job.public_dict()["mr_title"] == "Fix login timeout"
    runner.release.set()
    manager.shutdown()


def test_comment_queued_while_busy(tmp_config):
    app, manager, runner = _app(tmp_config)
    client = TestClient(app)
    headers = {"X-Gitlab-Token": "secret"}
    note = {
        "object_kind": "note",
        "user": {"id": 1},
        "object_attributes": {"noteable_type": "MergeRequest", "note": "/review"},
        "merge_request": {"iid": 2, "target_project_id": 5, "source_branch": "f", "target_branch": "main"},
    }
    first = client.post("/webhook", json=note, headers=headers)
    assert first.json()["status"] == "accepted"
    second = {
        **note,
        "object_attributes": {"noteable_type": "MergeRequest", "note": "/ask what about errors?"},
    }
    queued = client.post("/webhook", json=second, headers=headers)
    assert queued.json()["status"] == "queued"
    runner.release.set()
    manager.shutdown()


def test_dashboard_cancel_queued(tmp_config):
    app, manager, runner = _app(tmp_config)
    client = TestClient(app)
    headers = {"X-Gitlab-Token": "secret"}
    note = {
        "object_kind": "note",
        "user": {"id": 1},
        "object_attributes": {"noteable_type": "MergeRequest", "note": "/review"},
        "merge_request": {"iid": 8, "target_project_id": 5, "source_branch": "f", "target_branch": "main"},
    }
    first = client.post("/webhook", json=note, headers=headers).json()
    second = client.post(
        "/webhook",
        json={**note, "object_attributes": {"noteable_type": "MergeRequest", "note": "/ask later?"}},
        headers=headers,
    ).json()
    assert second["status"] == "queued"
    listed = client.get("/api/jobs", params={"mr_key": "5-8"}).json()
    assert listed["total"] >= 2
    cancel = client.post(f"/api/jobs/{second['job_id']}/cancel")
    assert cancel.status_code == 200
    job = client.get(f"/api/jobs/{second['job_id']}").json()["job"]
    assert job["status"] == "cancelled"
    runner.release.set()
    manager.shutdown()


def test_health(tmp_config):
    app, manager, _ = _app(tmp_config)
    client = TestClient(app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"
    manager.shutdown()
