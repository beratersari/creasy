"""Dashboard login uses env user/password and an httpOnly session cookie."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from creasy.api.dashboard import router as dashboard_router
from creasy.api.dashboard_auth import (
    SESSION_COOKIE,
    credentials_ok,
    dashboard_auth_required,
    make_session,
    session_ok,
)
from creasy.jobs.manager import Manager
from conftest import FakeRunner


def _client(tmp_config, **fields) -> tuple[TestClient, Manager]:
    for key, value in fields.items():
        setattr(tmp_config, key, value)
    runner = FakeRunner()
    manager = Manager(tmp_config, runner)
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.include_router(dashboard_router)
    return TestClient(app), manager


def test_credentials_require_user_and_password(tmp_config) -> None:
    tmp_config.dashboard_user = "berat"
    tmp_config.dashboard_password = "s3cret"
    assert dashboard_auth_required(tmp_config)
    assert credentials_ok(tmp_config, "berat", "s3cret")
    assert not credentials_ok(tmp_config, "other", "s3cret")
    assert not credentials_ok(tmp_config, "berat", "wrong")
    assert not credentials_ok(tmp_config, "berat", "")


def test_legacy_token_is_accepted_as_password(tmp_config) -> None:
    tmp_config.dashboard_token = "dash-secret"
    assert credentials_ok(tmp_config, "", "dash-secret")
    assert not credentials_ok(tmp_config, "", "nope")


def test_session_roundtrip_and_expiry(tmp_config) -> None:
    tmp_config.dashboard_password = "s3cret"
    issued = 1_700_000_000
    cookie = make_session(tmp_config, now=issued)
    assert session_ok(tmp_config, cookie, now=issued + 60)
    assert not session_ok(tmp_config, cookie, now=issued + 13 * 60 * 60)
    assert not session_ok(tmp_config, "v1.nope.sig", now=issued)


def test_login_sets_httponly_cookie_and_unlocks_jobs(tmp_config) -> None:
    client, manager = _client(
        tmp_config, dashboard_user="berat", dashboard_password="s3cret"
    )
    assert client.get("/api/jobs").status_code == 401
    denied = client.post("/api/login", json={"username": "berat", "password": "wrong"})
    assert denied.status_code == 401
    ok = client.post("/api/login", json={"username": "berat", "password": "s3cret"})
    assert ok.status_code == 200
    cookie = ok.cookies.get(SESSION_COOKIE)
    assert cookie
    header = ok.headers.get("set-cookie") or ""
    assert "HttpOnly" in header or "httponly" in header.lower()
    assert client.get("/api/jobs").status_code == 200
    auth = client.get("/api/auth").json()
    assert auth["required"] is True
    assert auth["authenticated"] is True
    assert auth["has_username"] is True
    client.post("/api/logout")
    assert client.get("/api/jobs").status_code == 401
    manager.shutdown()


def test_dashboard_token_header_still_works(tmp_config) -> None:
    client, manager = _client(tmp_config, dashboard_token="dash-secret")
    assert client.get("/api/jobs").status_code == 401
    ok = client.get("/api/jobs", headers={"X-Creasy-Token": "dash-secret"})
    assert ok.status_code == 200
    bearer = client.get("/api/jobs", headers={"Authorization": "Bearer dash-secret"})
    assert bearer.status_code == 200
    manager.shutdown()


def test_open_when_user_and_password_empty(tmp_config) -> None:
    client, manager = _client(
        tmp_config,
        dashboard_user="",
        dashboard_password="",
        dashboard_token="",
    )
    auth = client.get("/api/auth").json()
    assert auth["required"] is False
    assert auth["authenticated"] is True
    assert auth["has_username"] is False
    assert client.get("/api/jobs").status_code == 200
    login = client.post("/api/login", json={"username": "", "password": ""})
    assert login.status_code == 200
    manager.shutdown()


def test_user_alone_does_not_lock_dashboard(tmp_config) -> None:
    client, manager = _client(
        tmp_config,
        dashboard_user="berat",
        dashboard_password="",
        dashboard_token="",
    )
    auth = client.get("/api/auth").json()
    assert auth["required"] is False
    assert client.get("/api/jobs").status_code == 200
    manager.shutdown()


def test_password_only_hides_username_and_logs_in(tmp_config) -> None:
    client, manager = _client(
        tmp_config,
        dashboard_user="",
        dashboard_password="s3cret",
        dashboard_token="",
    )
    auth = client.get("/api/auth").json()
    assert auth["required"] is True
    assert auth["authenticated"] is False
    assert auth["has_username"] is False
    assert client.get("/api/jobs").status_code == 401
    assert client.post("/api/login", json={"username": "", "password": "wrong"}).status_code == 401
    ok = client.post("/api/login", json={"username": "", "password": "s3cret"})
    assert ok.status_code == 200
    assert client.get("/api/jobs").status_code == 200
    # leftover username is ignored when DASHBOARD_USER is empty
    other = TestClient(client.app)
    assert other.post("/api/login", json={"username": "anyone", "password": "s3cret"}).status_code == 200
    manager.shutdown()


def test_token_only_login_uses_password_field(tmp_config) -> None:
    client, manager = _client(
        tmp_config,
        dashboard_user="",
        dashboard_password="",
        dashboard_token="dash-secret",
    )
    auth = client.get("/api/auth").json()
    assert auth["required"] is True
    assert auth["has_username"] is False
    assert client.get("/api/jobs").status_code == 401
    assert client.post("/api/login", json={"username": "", "password": "nope"}).status_code == 401
    ok = client.post("/api/login", json={"username": "", "password": "dash-secret"})
    assert ok.status_code == 200
    assert client.get("/api/jobs").status_code == 200
    manager.shutdown()


def test_ws_accepts_session_cookie(tmp_config) -> None:
    client, manager = _client(
        tmp_config, dashboard_user="berat", dashboard_password="s3cret"
    )
    try:
        with client.websocket_connect("/ws"):
            raise AssertionError("unauthenticated websocket should not connect")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008
    client.post("/api/login", json={"username": "berat", "password": "s3cret"})
    with client.websocket_connect("/ws") as ws:
        payload = ws.receive_json()
        assert "running" in payload
    manager.shutdown()
