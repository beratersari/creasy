from __future__ import annotations

import time
from typing import Any, Callable, Optional

import httpx

from creasy.logging import get_logger

logger = get_logger("session")


class OpenCodeError(RuntimeError):
    def __init__(self, message: str, *, timeout: bool = False) -> None:
        super().__init__(message)
        self.timeout = timeout


def parse_model(model: str) -> tuple[str, str]:
    provider, _, name = (model or "").strip().partition("/")
    return provider, name


def last_assistant_text(messages: list[dict[str, Any]]) -> str:
    texts: list[str] = []
    for message in messages:
        info = message.get("info") if isinstance(message.get("info"), dict) else message
        if (info.get("role") or message.get("role")) != "assistant":
            continue
        parts = message.get("parts") or info.get("parts") or []
        chunk: list[str] = []
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                kind = str(part.get("type") or "text").lower()
                if kind not in {"text", "output", ""}:
                    continue
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                text = part.get("text") or part.get("content") or state.get("text") or ""
                if text:
                    chunk.append(str(text))
        if chunk:
            texts = chunk
    return "\n".join(texts).strip()


def snapshot_chat(messages: list[dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        info = message.get("info") if isinstance(message.get("info"), dict) else message
        role = str(info.get("role") or message.get("role") or "unknown")
        mid = str(info.get("id") or message.get("id") or f"msg_{index}")
        parts_in = message.get("parts") or info.get("parts") or []
        parts: list[dict[str, Any]] = []
        if isinstance(parts_in, list):
            for part in parts_in:
                if isinstance(part, dict):
                    state = part.get("state") if isinstance(part.get("state"), dict) else {}
                    parts.append(
                        {
                            "type": str(part.get("type") or "text"),
                            "text": str(part.get("text") or state.get("text") or ""),
                            "tool": str(part.get("tool") or ""),
                        }
                    )
        out.append({"id": mid, "session_id": session_id, "role": role, "parts": parts})
    return out


class OpenCodeClient:
    def __init__(self, base_url: str, directory: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.directory = directory
        self.headers = {"x-opencode-directory": directory}
        self.http = httpx.Client(base_url=self.base_url, verify=False, timeout=30.0)

    def close(self) -> None:
        self.http.close()

    def health(self) -> bool:
        try:
            response = self.http.get("/global/health", headers=self.headers, timeout=5.0)
            return response.status_code == 200
        except Exception:
            return False

    def get_session(self, session_id: str) -> httpx.Response:
        return self.http.get(f"/session/{session_id}", headers=self.headers, timeout=15.0)

    def create_session(self, title: str) -> str:
        response = self.http.post("/session", json={"title": title}, headers=self.headers, timeout=60.0)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            return data["id"]
        inner = data.get("session") if isinstance(data, dict) else None
        if isinstance(inner, dict) and inner.get("id"):
            return str(inner["id"])
        raise OpenCodeError("session create returned no id")

    def resume_or_create(self, inbound: Optional[str], title: str) -> tuple[str, bool]:
        if inbound and inbound.startswith("ses_"):
            try:
                got = self.get_session(inbound)
                if got.status_code == 200:
                    return inbound, False
                logger.info("session %s rejected (%s); creating new", inbound, got.status_code)
            except Exception as exc:  # noqa: BLE001
                logger.info("session resume failed (%s); creating new", exc)
        sid = self.create_session(title)
        return sid, True

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        response = self.http.get(
            f"/session/{session_id}/message",
            params={"limit": 400},
            headers=self.headers,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return [m for m in data if isinstance(m, dict)]
        if isinstance(data, dict):
            for key in ("messages", "data", "items"):
                if isinstance(data.get(key), list):
                    return [m for m in data[key] if isinstance(m, dict)]
        return []

    def status(self) -> Any:
        try:
            response = self.http.get("/session/status", headers=self.headers, timeout=10.0)
            if response.status_code == 200:
                return response.json()
        except Exception:
            return None
        return None

    def session_busy(self, session_id: str) -> bool:
        status = self.status()
        if not isinstance(status, dict):
            return False
        for value in status.values() if not isinstance(status.get(session_id), dict) else [status.get(session_id)]:
            row = value if isinstance(value, dict) else {}
            kind = str(row.get("type") or row.get("status") or row.get("state") or "").lower()
            if kind in {"busy", "retry", "running", "in_progress"}:
                sid = str(row.get("id") or row.get("sessionID") or row.get("session_id") or "")
                if not sid or sid == session_id:
                    return True
        if session_id in status and isinstance(status[session_id], dict):
            kind = str(status[session_id].get("type") or status[session_id].get("status") or "").lower()
            return kind in {"busy", "retry", "running", "in_progress"}
        return False

    def post_message(self, session_id: str, text: str, *, model: str, agent: str) -> None:
        provider, model_id = parse_model(model)
        body = {
            "agent": agent,
            "parts": [{"type": "text", "text": text}],
            "model": {"providerID": provider, "modelID": model_id},
        }
        try:
            response = self.http.post(
                f"/session/{session_id}/prompt_async",
                json=body,
                headers=self.headers,
                timeout=20.0,
            )
            if response.status_code < 400:
                return
            logger.info("prompt_async HTTP %s; falling back to /message", response.status_code)
        except Exception as exc:  # noqa: BLE001
            logger.info("prompt_async failed (%s); falling back to /message", exc)
        response = self.http.post(
            f"/session/{session_id}/message",
            json=body,
            headers=self.headers,
            timeout=httpx.Timeout(connect=15.0, read=600.0, write=30.0, pool=15.0),
        )
        if response.status_code >= 400:
            raise OpenCodeError(f"user message POST failed: HTTP {response.status_code} {response.text[:200]}")

    def abort(self, session_id: str) -> None:
        if not session_id.startswith("ses_"):
            return
        try:
            self.http.post(f"/session/{session_id}/abort", headers=self.headers, timeout=15.0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("abort failed: %s", exc)

    def wait_idle(
        self,
        session_id: str,
        *,
        timeout: float,
        hang_timeout: float,
        should_stop: Optional[Callable[[], bool]] = None,
        idle_settle: float = 8.0,
    ) -> str:
        deadline = time.time() + timeout
        last_change = time.time()
        last_len = 0
        saw_busy = False
        settle = max(0.0, float(idle_settle))
        while time.time() < deadline:
            if should_stop and should_stop():
                raise OpenCodeError("cancelled")
            if not self.health():
                raise OpenCodeError("serve-dead")
            try:
                messages = self.list_messages(session_id)
            except Exception:
                messages = []
            text = last_assistant_text(messages)
            if len(text) != last_len:
                last_len = len(text)
                last_change = time.time()
            busy = self.session_busy(session_id)
            if busy:
                saw_busy = True
            if not busy and text:
                if saw_busy:
                    return text
                if time.time() - last_change > settle:
                    return text
            if time.time() - last_change > hang_timeout:
                raise OpenCodeError("hang")
            time.sleep(min(1.0, max(0.05, settle if settle else 0.2)))
        raise OpenCodeError("timeout", timeout=True)
