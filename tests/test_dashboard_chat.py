"""Dashboard chat: finished snapshot + live pull from this job's serve."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from creasy.api.dashboard import router as dashboard_router
from creasy.jobs.manager import Manager
from creasy.jobs.models import JobRecord, mint_job_id
from creasy.opencode.session import snapshot_chat
from conftest import FakeRunner


def test_snapshot_chat_keeps_tool_input_and_output() -> None:
    raw = [
        {
            "info": {"id": "msg_1", "role": "assistant"},
            "parts": [
                {
                    "type": "tool",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": "git diff --stat"},
                        "output": "app.py | 2 +-\n",
                    },
                },
                {"type": "text", "text": "Looks fine."},
            ],
        }
    ]
    rows = snapshot_chat(raw, "ses_1")
    assert rows[0]["role"] == "assistant"
    tool = rows[0]["parts"][0]
    assert tool["tool"] == "bash"
    assert tool["status"] == "completed"
    assert tool["input"]["command"] == "git diff --stat"
    assert "app.py" in tool["output"]
    assert rows[0]["parts"][1]["text"] == "Looks fine."


def test_finished_job_chat_uses_snapshot(tmp_config) -> None:
    manager = Manager(tmp_config, FakeRunner())
    manager.ready = True
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="1-1",
        project_id=1,
        mr_iid=1,
        trigger="review",
        status="success",
        live=False,
        session_id="ses_done",
        chat_snapshot=[
            {
                "id": "u1",
                "session_id": "ses_done",
                "role": "user",
                "parts": [{"type": "text", "text": "review this"}],
            }
        ],
    )
    manager.store.save(job)
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    client = TestClient(app)
    app.include_router(dashboard_router)
    body = client.get(f"/api/jobs/{job.job_id}/chat").json()
    assert body["messages"][0]["parts"][0]["text"] == "review this"
    manager.shutdown()


def test_live_chat_reads_from_this_jobs_serve(tmp_config) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/message"):
            return httpx.Response(
                200,
                json=[
                    {
                        "info": {"id": "msg_live", "role": "assistant"},
                        "parts": [{"type": "text", "text": "reading the tree"}],
                    }
                ],
            )
        return httpx.Response(404)

    manager = Manager(tmp_config, FakeRunner())
    manager.ready = True
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="2-2",
        project_id=2,
        mr_iid=2,
        trigger="review",
        status="running",
        live=True,
        session_id="ses_live",
        clone_path=str(tmp_config.work_dir / "2-2"),
        serve_base_url="http://127.0.0.1:9",
        serve_port=9,
    )
    manager.store.save(job)

    transport = httpx.MockTransport(handler)

    class FakeClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    import creasy.opencode.session as session_mod

    original = session_mod.httpx.Client
    session_mod.httpx.Client = FakeClient  # type: ignore[misc]
    try:
        app = FastAPI()
        app.state.config = tmp_config
        app.state.manager = manager
        app.include_router(dashboard_router)
        client = TestClient(app)
        body = client.get(f"/api/jobs/{job.job_id}/chat").json()
    finally:
        session_mod.httpx.Client = original  # type: ignore[misc]
        manager.shutdown()

    texts = [p.get("text") for m in body["messages"] for p in m.get("parts") or []]
    assert "reading the tree" in texts
    assert body["live"] is True


def test_live_chat_falls_back_to_prompt_when_serve_unreachable(tmp_config) -> None:
    manager = Manager(tmp_config, FakeRunner())
    manager.ready = True
    job = JobRecord(
        job_id=mint_job_id(),
        mr_key="3-3",
        project_id=3,
        mr_iid=3,
        trigger="review",
        status="running",
        live=True,
        session_id="ses_x",
        clone_path=str(tmp_config.work_dir / "3-3"),
        serve_base_url="http://127.0.0.1:1",
        prompt="full review prompt",
    )
    manager.store.save(job)
    app = FastAPI()
    app.state.config = tmp_config
    app.state.manager = manager
    app.include_router(dashboard_router)
    client = TestClient(app)
    body = client.get(f"/api/jobs/{job.job_id}/chat").json()
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["parts"][0]["text"] == "full review prompt"
    manager.shutdown()
