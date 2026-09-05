from __future__ import annotations

from pathlib import Path
from typing import Optional

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from creasy.jobs.models import ERROR_STATUSES, LIVE_STATUSES
from creasy.api.report import build_report_context
from creasy.logging import get_logger, read_job_log_lines
from creasy.opencode.serve import read_serve_log, serve_log_path
from creasy.workspace.identity import mr_key

router = APIRouter()
logger = get_logger("dashboard")


def _check_token(request: Request) -> None:
    token = request.app.state.config.dashboard_token
    if not token:
        return
    auth = request.headers.get("Authorization") or ""
    header = request.headers.get("X-Creasy-Token") or ""
    bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if header != token and bearer != token:
        raise HTTPException(status_code=401, detail="dashboard token required")


def _mgr(request: Request):
    return request.app.state.manager


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _chat_payload(job) -> dict:
    messages = []
    for index, row in enumerate(job.chat_snapshot or []):
        if not isinstance(row, dict):
            continue
        messages.append(
            {
                "id": row.get("id") or f"msg_{index}",
                "session_id": row.get("session_id") or job.session_id or "",
                "role": row.get("role") or "unknown",
                "parts": row.get("parts") or [],
            }
        )
    return {
        "job_id": job.job_id,
        "session_id": job.session_id,
        "session_ids": [job.session_id] if job.session_id else [],
        "messages": messages,
        "chat": job.chat_snapshot,
        "text": job.text,
    }


@router.get("/api/jobs")
def api_jobs(
    request: Request,
    mr_key: Optional[str] = None,
    jira_id: Optional[str] = None,
    filter: str = Query(default="all"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict:
    _check_token(request)
    jobs = _mgr(request).store.list_all()
    key = (mr_key or jira_id or "").strip()
    if key:
        jobs = [j for j in jobs if j.mr_key == key]
    filt = (filter or "all").strip().lower()
    if filt == "active":
        jobs = [j for j in jobs if j.status in LIVE_STATUSES]
    elif filt == "queued":
        jobs = [j for j in jobs if j.status == "queued"]
    elif filt == "error":
        jobs = [j for j in jobs if j.status in ERROR_STATUSES]
    elif filt == "completed":
        jobs = [j for j in jobs if j.status == "success"]
    total = len(jobs)
    start = (page - 1) * page_size
    slice_ = jobs[start : start + page_size]
    return {
        "jobs": [j.public_dict() for j in slice_],
        "total": total,
        "page": page,
        "page_size": page_size,
        "filter": filt,
        "server_time": _now(),
    }


@router.get("/api/jobs/{job_id}")
def api_job(job_id: str, request: Request) -> dict:
    _check_token(request)
    job = _mgr(request).store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"No job {job_id}")
    logs = read_job_log_lines(
        request.app.state.config.log_dir,
        job.job_id,
        mr_key=job.mr_key,
        log_file=job.log_file,
        limit=2000,
    )
    return {"job": job.public_dict(), "system_logs": logs}


@router.get("/api/jobs/{job_id}/chat")
def api_chat(job_id: str, request: Request) -> dict:
    _check_token(request)
    job = _mgr(request).store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"No job {job_id}")
    return _chat_payload(job)


@router.get("/api/jobs/{job_id}/prompts")
def api_prompts(job_id: str, request: Request) -> dict:
    _check_token(request)
    job = _mgr(request).store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"No job {job_id}")
    prompts = []
    if job.prompt:
        prompts.append(
            {
                "id": "user",
                "text": job.prompt,
                "posted_at": job.accepted_at or job.started_at or "",
            }
        )
    return {"prompts": prompts}


@router.get("/api/jobs/{job_id}/logs")
def api_logs(job_id: str, request: Request, limit: int = Query(default=2000, ge=0)) -> dict:
    _check_token(request)
    manager = _mgr(request)
    job = manager.store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"No job {job_id}")
    lines = read_job_log_lines(
        manager.config.log_dir,
        job.job_id,
        mr_key=job.mr_key,
        log_file=job.log_file,
        limit=limit,
    )
    return {"job_id": job.job_id, "lines": lines}


@router.get("/api/jobs/{job_id}/serve-log")
def api_serve_log(job_id: str, request: Request) -> dict:
    _check_token(request)
    manager = _mgr(request)
    job = manager.store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"No job {job_id}")
    path = serve_log_path(manager.config.serve_dir, job.job_id)
    return {"job_id": job.job_id, "missing": not path.is_file(), "text": read_serve_log(path)}


@router.get("/api/queue")
def api_queue(request: Request, mr_key: Optional[str] = None, jira_id: Optional[str] = None) -> dict:
    _check_token(request)
    key = (mr_key or jira_id or "").strip() or None
    raw = _mgr(request).queue.public_items(mr_key=key)
    items = []
    for row in raw:
        job = _mgr(request).store.get(row["job_id"])
        items.append(job.public_dict() if job else {"job_id": row["job_id"], "jira_id": row["mr_key"], "mr_key": row["mr_key"], "status": "queued", "live": False})
    running = []
    for job in _mgr(request).store.list_all():
        if job.status == "running" and (key is None or job.mr_key == key):
            running.append({"mr_key": job.mr_key, "job_id": job.job_id, "trigger": job.trigger})
    return {"items": items, "queued_count": len(items), "running": running}


@router.get("/api/meta")
def api_meta(request: Request) -> dict:
    from creasy import __version__

    return {"version": __version__, "server_time": _now(), "app_name": "creasy"}


@router.get("/api/report-context")
def api_report_context(request: Request) -> dict:
    _check_token(request)
    return build_report_context(_mgr(request))


@router.websocket("/ws")
async def dashboard_ws(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            manager = ws.app.state.manager
            health = manager.health()
            await ws.send_json(
                {
                    "running": health.get("running") or 0,
                    "queue_queued": health.get("queued") or 0,
                }
            )
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        return
    except Exception:
        return


@router.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str, request: Request) -> dict:
    _check_token(request)
    ok, message = _mgr(request).cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "job_id": job_id}


@router.post("/api/mrs/{project_id}/{mr_iid}/cancel")
def api_cancel_mr(project_id: int, mr_iid: int, request: Request) -> dict:
    _check_token(request)
    count, key = _mgr(request).cancel_mr(project_id, mr_iid, delete_clone_dir=False)
    return {"ok": True, "cancelled": count, "mr_key": key}


@router.get("/reviews/{project_id}/{mr_iid}")
def api_reviews(project_id: int, mr_iid: int, request: Request) -> dict:
    _check_token(request)
    key = mr_key(project_id, mr_iid)
    jobs = [j.public_dict() for j in _mgr(request).store.list_all() if j.mr_key == key]
    workspace = _mgr(request).workspaces.get(key)
    return {
        "mr_key": key,
        "workspace": workspace.__dict__ if workspace else None,
        "jobs": jobs,
        "queue": _mgr(request).queue.public_items(mr_key=key),
    }


def spa_dir() -> Path:
    # Served UI is the Vite build only. web/index.html is the dev entry
    # (loads /src/main.tsx) and must not be used as a fallback.
    return Path(__file__).resolve().parents[3] / "web" / "dist"


def attach_spa(app) -> None:
    dist = spa_dir()
    index = dist / "index.html"
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    def _index() -> FileResponse:
        if not index.is_file():
            raise HTTPException(status_code=404, detail="dashboard not built")
        return FileResponse(index)

    @app.get("/")
    def root() -> FileResponse:
        if index.is_file():
            return _index()
        return JSONResponse({"service": "creasy", "docs": "/jobs"})

    @app.get("/jobs")
    def jobs_page() -> FileResponse:
        return _index()

    @app.get("/jobs/{job_id}")
    def job_page(job_id: str) -> FileResponse:
        return _index()

    favicon = dist / "favicon.svg"
    if favicon.is_file():

        @app.get("/favicon.svg")
        def favicon_svg() -> FileResponse:
            return FileResponse(favicon)
