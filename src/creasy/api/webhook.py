from __future__ import annotations

import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from creasy.gitlab.events import CleanupTrigger, Ignore, ReviewTrigger, classify_webhook
from creasy.logging import get_logger

router = APIRouter()
logger = get_logger("webhook")


def _bot_user_id(request: Request) -> Optional[int]:
    cached = getattr(request.app.state, "bot_user_id", None)
    if cached is not None:
        return cached
    gitlab = getattr(request.app.state, "gitlab", None)
    if gitlab is None:
        return None
    uid = gitlab.current_user_id()
    if uid is not None:
        request.app.state.bot_user_id = uid
    return uid


def _verify_secret(request: Request) -> None:
    secret = request.app.state.config.webhook_secret
    if not secret:
        return
    got = request.headers.get("X-Gitlab-Token", "")
    if got != secret:
        raise HTTPException(status_code=401, detail="Invalid secret")


@router.post("/webhook")
async def webhook(request: Request) -> JSONResponse:
    _verify_secret(request)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON")

    config = request.app.state.config
    manager = request.app.state.manager
    bot_id = _bot_user_id(request)
    kind = str(payload.get("object_kind") or "").strip().lower()
    if kind == "note" and bot_id is None:
        return JSONResponse({"status": "ignored", "reason": "bot user unknown"})
    classified = classify_webhook(payload, skip_drafts=config.skip_draft_mrs, bot_user_id=bot_id)
    logger.info("webhook classified=%s", type(classified).__name__)

    if isinstance(classified, Ignore):
        return JSONResponse({"status": "ignored", "reason": classified.reason})

    if isinstance(classified, CleanupTrigger):
        threading.Thread(
            target=manager.cleanup_mr,
            args=(classified,),
            name=f"cleanup-{classified.project_id}-{classified.mr_iid}",
            daemon=True,
        ).start()
        return JSONResponse(
            {
                "status": "accepted",
                "action": "cleanup",
                "project_id": classified.project_id,
                "mr_iid": classified.mr_iid,
            }
        )

    if isinstance(classified, ReviewTrigger):
        ack, job, message = manager.submit(classified)
        body = {"status": ack, "message": message}
        if job:
            body["job_id"] = job.job_id
            body["mr_key"] = job.mr_key
        return JSONResponse(body)

    return JSONResponse({"status": "ignored", "reason": "unhandled"})
