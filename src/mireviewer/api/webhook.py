from __future__ import annotations

import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from mireviewer.gitlab.events import CleanupTrigger, Ignore, ReviewTrigger, classify_webhook
from mireviewer.logging import get_logger, log_fail, log_ok
from mireviewer.review.mention import collect_names, parse_mention_aliases

router = APIRouter()
logger = get_logger("webhook")


def _remember_gitlab_user(request: Request, user: object) -> None:
    request.app.state.bot_user_resolved = True
    if not isinstance(user, dict):
        return
    uid = user.get("id")
    if uid is not None:
        request.app.state.bot_user_id = uid
    names = list(user.get("names") or [])
    if names:
        request.app.state.bot_mention_names = names


def _bot_user_id(request: Request) -> Optional[int]:
    cached = getattr(request.app.state, "bot_user_id", None)
    if cached is not None:
        return cached
    if getattr(request.app.state, "bot_user_resolved", False):
        return None
    gitlab = getattr(request.app.state, "gitlab", None)
    if gitlab is None:
        request.app.state.bot_user_resolved = True
        return None
    current = getattr(gitlab, "current_user", None)
    if callable(current):
        user = current()
        _remember_gitlab_user(request, user)
        cached = getattr(request.app.state, "bot_user_id", None)
        if cached is not None:
            return cached
        try:
            return int(user["id"]) if isinstance(user, dict) and user.get("id") is not None else None
        except (TypeError, ValueError):
            return None
    uid_fn = getattr(gitlab, "current_user_id", None)
    uid = uid_fn() if callable(uid_fn) else None
    request.app.state.bot_user_resolved = True
    if uid is not None:
        request.app.state.bot_user_id = uid
    return uid


def _mention_names(request: Request) -> list[str]:
    cfg = request.app.state.config
    cached = getattr(request.app.state, "bot_mention_names", None) or []
    aliases = parse_mention_aliases(getattr(cfg, "review_mention", ""))
    if not getattr(request.app.state, "bot_user_resolved", False):
        gitlab = getattr(request.app.state, "gitlab", None)
        current = getattr(gitlab, "current_user", None) if gitlab is not None else None
        if callable(current):
            _remember_gitlab_user(request, current())
            cached = getattr(request.app.state, "bot_mention_names", None) or cached
        else:
            request.app.state.bot_user_resolved = True
    return collect_names(aliases, cached)


def _verify_secret(request: Request) -> None:
    secret = request.app.state.config.webhook_secret
    if not secret:
        log_ok(logger, "webhook secret", check="skipped", reason="WEBHOOK_SECRET unset")
        return
    got = request.headers.get("X-Gitlab-Token", "")
    if got != secret:
        log_fail(logger, "webhook secret", reason="mismatch")
        raise HTTPException(status_code=401, detail="Invalid secret")
    log_ok(logger, "webhook secret", check="matched")


@router.post("/mireviewer/webhook/gitlab")
@router.post("/creasy/webhook/gitlab", include_in_schema=False)
async def webhook(request: Request) -> JSONResponse:
    _verify_secret(request)
    try:
        payload = await request.json()
    except Exception as exc:
        log_fail(logger, "webhook JSON", err=exc)
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    if not isinstance(payload, dict):
        log_fail(logger, "webhook JSON", reason="not an object")
        raise HTTPException(status_code=400, detail="Invalid JSON")

    config = request.app.state.config
    manager = request.app.state.manager
    bot_id = _bot_user_id(request)
    mention_names = _mention_names(request)
    kind = str(payload.get("object_kind") or "").strip().lower()
    if kind == "note":
        logger.info(
            "webhook identity bot_id=%s mention_names=%s review_mention=%s",
            bot_id if bot_id is not None else "-",
            ",".join(mention_names) or "-",
            (config.review_mention or "").strip() or "-",
        )
    if kind == "note" and bot_id is None and not mention_names:
        log_fail(logger, "webhook bot user", reason="GITLAB_TOKEN user unknown")
        return JSONResponse({"status": "ignored", "reason": "bot user unknown"})
    classified = classify_webhook(
        payload,
        skip_drafts=config.skip_draft_mrs,
        bot_user_id=bot_id,
        mention_names=mention_names,
    )

    if isinstance(classified, Ignore):
        log_ok(logger, "webhook ignored", object_kind=kind or "missing", reason=classified.reason)
        return JSONResponse({"status": "ignored", "reason": classified.reason})

    if isinstance(classified, CleanupTrigger):
        threading.Thread(
            target=manager.cleanup_mr,
            args=(classified,),
            name=f"cleanup-{classified.project_id}-{classified.mr_iid}",
            daemon=True,
        ).start()
        log_ok(
            logger,
            "webhook cleanup started",
            action=classified.action,
            project=classified.project_id,
            mr=classified.mr_iid,
        )
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
        fields = {
            "ack": ack,
            "kind": classified.kind,
            "project": classified.project_id,
            "mr": classified.mr_iid,
            "job": job.job_id if job else "-",
            "message": message,
        }
        if ack == "ignored":
            log_fail(logger, "webhook submit", **fields)
        else:
            log_ok(logger, "webhook submit", **fields)
        return JSONResponse(body)

    log_ok(logger, "webhook ignored", object_kind=kind or "missing", reason="unhandled")
    return JSONResponse({"status": "ignored", "reason": "unhandled"})
