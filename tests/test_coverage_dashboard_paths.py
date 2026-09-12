"""Dashboard route coverage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.dashboard import (
    _chat_payload,
    _live_chat_rows,
    _spa_file,
    attach_spa,
    job_matches_query,
    router,
    spa_dir,
)
from creasy.jobs.manager import Manager
from creasy.jobs.models import JobRecord, mint_job_id
from conftest import FakeRunner


def _app(tmp_config):
    manager = Manager(tmp_config, FakeRunner())
    manager.ready = True
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.include_router(router)
    return TestClient(app), manager


def _job(**kwargs):
    data = dict(
        job_id=mint_job_id(),
        mr_key="1-7",
        project_id=1,
        mr_iid=7,
        trigger="review",
        status="success",
        mr_title="Add overflow",
        prompt="please review",
    )
    data.update(kwargs)
    return JobRecord(**data)


def test_job_matches_query_variants():
    job = _job()
    assert job_matches_query(job, "")
    assert job_matches_query(job, "1-7")
    assert job_matches_query(job, "1-")
    assert job_matches_query(job, "overflow")
    assert job_matches_query(job, "!7")
    assert job_matches_query(job, "7")
    assert job_matches_query(job, "1")
    assert not job_matches_query(job, "zzz")


def test_dashboard_job_routes(tmp_config):
    client, manager = _app(tmp_config)
    job = _job(status="running", live=True, session_id="ses_1", serve_base_url="", clone_path="")
    manager.store.save(job)
    queued = _job(status="queued", trigger="ask")
    manager.store.save(queued)
    err = _job(status="error")
    manager.store.save(err)
    usage = _job(trigger="usage", status="success")
    manager.store.save(usage)
    assert client.get("/api/jobs").status_code == 200
    assert client.get("/api/jobs", params={"filter": "active"}).status_code == 200
    assert client.get("/api/jobs", params={"filter": "queued"}).status_code == 200
    assert client.get("/api/jobs", params={"filter": "error"}).status_code == 200
    assert client.get("/api/jobs", params={"filter": "success"}).status_code == 200
    assert client.get("/api/jobs", params={"filter": "cancelled"}).status_code == 200
    assert client.get("/api/jobs", params={"mr_key": "7"}).status_code == 200
    assert client.get(f"/api/jobs/{job.job_id}").status_code == 200
    assert client.get("/api/jobs/missing").status_code == 404
    assert client.get(f"/api/jobs/{job.job_id}/chat").status_code == 200
    assert client.get(f"/api/jobs/{job.job_id}/prompts").status_code == 200
    assert client.get(f"/api/jobs/{job.job_id}/logs").status_code == 200
    assert client.get(f"/api/jobs/{job.job_id}/serve-log").status_code == 200
    manager.queue.enqueue(job.mr_key, queued.job_id)
    manager.queue.enqueue(job.mr_key, "ghost")
    assert client.get("/api/queue").status_code == 200
    assert client.get("/api/queue", params={"mr_key": "7"}).status_code == 200
    assert client.get("/api/queue", params={"mr_key": "nope"}).status_code == 200
    assert client.get("/api/meta").json()["app_name"] == "creasy"
    assert client.get("/api/settings").status_code == 200
    assert client.put("/api/settings", content="not-json").status_code in {200, 400}
    assert client.put("/api/settings", json=["x"]).status_code in {200, 400}
    assert client.put("/api/settings", json={"opencode_model": "nope"}).status_code == 400
    assert client.get("/api/report-context").status_code == 200
    assert client.get("/api/auth").status_code == 200
    assert client.post("/api/login").status_code == 200
    assert client.post("/api/logout").status_code == 200
    assert client.get(f"/reviews/1/7").status_code == 200
    assert client.post(f"/api/jobs/{job.job_id}/cancel").status_code in {200, 400}
    assert client.post("/api/mrs/1/7/cancel").status_code == 200
    manager.shutdown()


def test_dashboard_auth_login(tmp_config):
    tmp_config.dashboard_user = "u"
    tmp_config.dashboard_password = "p"
    client, manager = _app(tmp_config)
    assert client.get("/api/jobs").status_code == 401
    assert client.post("/api/login", json={"username": "bad", "password": "x"}).status_code == 401
    ok = client.post("/api/login", json={"username": "u", "password": "p"})
    assert ok.status_code == 200
    assert client.get("/api/jobs").status_code == 200
    client.post("/api/logout")
    manager.shutdown()


def test_chat_payload_and_live(monkeypatch):
    job = _job(live=False, prompt="hi", chat_snapshot=[{"role": "assistant", "parts": [{"type": "text", "text": "ok"}]}])
    payload = _chat_payload(job)
    assert payload["messages"]
    job.chat_snapshot = ["nope"]
    payload = _chat_payload(job)
    assert payload["messages"]
    job.prompt = ""
    job.chat_snapshot = []
    _chat_payload(job)
    job.live = True
    job.serve_base_url = "http://x"
    job.session_id = "ses"
    job.clone_path = "/tmp"
    monkeypatch.setattr("creasy.api.dashboard.fetch_live_chat", lambda *a, **k: [{"role": "user", "parts": []}])
    assert _live_chat_rows(job)
    monkeypatch.setattr("creasy.api.dashboard.fetch_live_chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert _live_chat_rows(job) is None
    job.serve_base_url = ""
    assert _live_chat_rows(job) is None
    job.live = False
    assert _live_chat_rows(job) is None


def test_attach_spa_and_pages(tmp_config, tmp_path, monkeypatch):
    dist = tmp_path / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>Creasy</html>", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg></svg>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    monkeypatch.setattr("creasy.api.dashboard.spa_dir", lambda: dist)
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = MagicMock()
    attach_spa(app)
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/jobs").status_code == 200
    assert client.get("/jobs/abc").status_code == 200
    assert client.get("/settings").status_code == 200
    assert client.get("/favicon.svg").status_code == 200
    resp = _spa_file(dist / "index.html")
    assert resp.path
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr("creasy.api.dashboard.spa_dir", lambda: empty)
    app2 = FastAPI()
    app2.state.config = tmp_config
    attach_spa(app2)
    client2 = TestClient(app2)
    body = client2.get("/")
    assert body.status_code == 200
    assert client2.get("/login").status_code == 404
    assert spa_dir().name == "dist"
    monkeypatch.setattr("creasy.paths.bundled_dir", lambda *a, **k: dist)
    assert spa_dir() == dist
