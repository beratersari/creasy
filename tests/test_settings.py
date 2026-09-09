from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.dashboard import router as dashboard_router
from creasy.gitlab.events import ReviewTrigger
from creasy.jobs.manager import Manager
from creasy.settings import apply_runtime_settings, normalize_model, settings_path
from conftest import FakeRunner


def _app(tmp_config, *, token: str = ""):
    tmp_config.dashboard_token = token
    tmp_config.opencode_model = "opencode/big-pickle"
    tmp_config.opencode_timeout = 1800
    tmp_config.opencode_model_env = "opencode/big-pickle"
    tmp_config.opencode_timeout_env = 1800
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.include_router(dashboard_router)
    return TestClient(app), manager, runner


def _trigger(**kwargs) -> ReviewTrigger:
    data = dict(
        kind="review",
        project_id=1,
        mr_iid=9,
        source_branch="feat",
        target_branch="main",
        sha="abc",
        explicit=True,
        title="Add overflow",
    )
    data.update(kwargs)
    return ReviewTrigger(**data)


def test_normalize_model_requires_provider_id() -> None:
    assert normalize_model("acme/fast") == "acme/fast"
    try:
        normalize_model("nocolon")
    except ValueError as exc:
        assert "provider/id" in str(exc)
    else:
        raise AssertionError("expected SettingsError")


def test_apply_runtime_settings_from_disk(tmp_config) -> None:
    settings_path(tmp_config).write_text(
        json.dumps({"opencode_model": "acme/fast", "opencode_timeout": 90}),
        encoding="utf-8",
    )
    apply_runtime_settings(tmp_config)
    assert tmp_config.opencode_model == "acme/fast"
    assert tmp_config.opencode_timeout == 90


def test_get_settings(tmp_config) -> None:
    client, manager, runner = _app(tmp_config)
    body = client.get("/api/settings").json()
    assert body["opencode_model"] == "opencode/big-pickle"
    assert body["opencode_timeout"] == 1800
    assert "opencode/big-pickle" in body["models"]
    runner.release.set()
    manager.shutdown()


def test_put_settings_updates_config_and_next_job(tmp_config) -> None:
    client, manager, runner = _app(tmp_config)
    res = client.put(
        "/api/settings",
        json={"opencode_model": "acme/fast", "opencode_timeout": 120},
    )
    assert res.status_code == 200
    assert res.json()["opencode_model"] == "acme/fast"
    assert manager.config.opencode_model == "acme/fast"
    assert manager.config.opencode_timeout == 120
    saved = json.loads(settings_path(tmp_config).read_text(encoding="utf-8"))
    assert saved == {"opencode_model": "acme/fast", "opencode_timeout": 120}
    ack, job, _message = manager.submit(_trigger())
    assert ack in {"accepted", "queued"}
    assert job is not None
    assert job.model == "acme/fast"
    runner.release.set()
    manager.shutdown()


def test_put_settings_rejects_bad_model(tmp_config) -> None:
    client, manager, runner = _app(tmp_config)
    res = client.put("/api/settings", json={"opencode_model": "nope", "opencode_timeout": 30})
    assert res.status_code == 400
    assert manager.config.opencode_model == "opencode/big-pickle"
    runner.release.set()
    manager.shutdown()


def test_settings_requires_token_when_set(tmp_config) -> None:
    client, manager, runner = _app(tmp_config, token="dash-secret")
    assert client.get("/api/settings").status_code == 401
    assert client.put("/api/settings", json={"opencode_model": "acme/fast"}).status_code == 401
    ok = client.get("/api/settings", headers={"X-Creasy-Token": "dash-secret"})
    assert ok.status_code == 200
    saved = client.put(
        "/api/settings",
        json={"opencode_model": "acme/fast", "opencode_timeout": 45},
        headers={"X-Creasy-Token": "dash-secret"},
    )
    assert saved.status_code == 200
    runner.release.set()
    manager.shutdown()
