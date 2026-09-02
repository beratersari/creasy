from __future__ import annotations

from fastapi import APIRouter, Request

from creasy import __version__

router = APIRouter()


@router.get("/health")
def health(request: Request) -> dict:
    manager = request.app.state.manager
    payload = manager.health()
    payload["status"] = "healthy" if payload.get("ready") else "starting"
    payload["version"] = __version__
    return payload
