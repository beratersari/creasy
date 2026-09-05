"""GET /api/report-context for the client-built issue zip."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.dashboard import router as dashboard_router
from creasy.jobs.manager import Manager
from conftest import FakeRunner


def _client(tmp_config, token: str = "") -> TestClient:
    tmp_config.dashboard_token = token
    tmp_config.gitlab_token = "glpat-SHOULD-NOT-LEAK"
    tmp_config.webhook_secret = "hook-secret"
    tmp_config.log_dir.mkdir(parents=True, exist_ok=True)
    (tmp_config.log_dir / "app.log").write_text(
        "https://oauth2:glpat-SHOULD-NOT-LEAK@gitlab.example/g/r.git\nready\n",
        encoding="utf-8",
    )
    manager = Manager(tmp_config, FakeRunner())
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.include_router(dashboard_router)
    return TestClient(app), manager


def test_report_context_is_safe_and_includes_logs(tmp_config) -> None:
    client, manager = _client(tmp_config)
    body = client.get("/api/report-context").json()
    assert body["meta"]["app_name"] == "creasy"
    assert "glpat-SHOULD-NOT-LEAK" not in str(body)
    assert "hook-secret" not in str(body)
    assert body["settings"]["gitlab_token_set"] is True
    assert body["settings"]["webhook_secret_set"] is True
    assert "gitlab_token" not in body["settings"]
    assert body["app_log"]["missing"] is False
    assert "gitlab.example" in body["app_log"]["text"]
    assert "oauth2:" not in body["app_log"]["text"]
    assert "runtime" in body
    assert "queue" in body
    manager.shutdown()


def test_report_context_requires_token_when_set(tmp_config) -> None:
    client, manager = _client(tmp_config, token="dash-secret")
    assert client.get("/api/report-context").status_code == 401
    ok = client.get("/api/report-context", headers={"X-Creasy-Token": "dash-secret"})
    assert ok.status_code == 200
    manager.shutdown()
