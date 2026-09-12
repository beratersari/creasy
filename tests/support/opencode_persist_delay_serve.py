"""opencode serve stand-in that keeps ses_* across job processes.

Each Creasy job starts a new serve. Real OpenCode persists the session
on disk; this stub writes messages.json in cwd (the clone) so a later
job can resume the same transcript.

If CREASY_STUB_DELAY_SEC is set and a prior assistant turn already
exists, prompt_async returns 200 immediately and only appends the new
user + assistant messages after that delay. GET /message keeps serving
the previous turn until then. Status stays idle. That is the
wait_idle / prompt_async race.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

LOCK = threading.Lock()
STATE_PATH = Path("creasy_stub_state.json")
DELAY_UNTIL = 0.0


def _port(argv: list[str]) -> int:
    for index, arg in enumerate(argv):
        if arg == "--port" and index + 1 < len(argv):
            return int(argv[index + 1])
        if arg.startswith("--port="):
            return int(arg.split("=", 1)[1])
    raise SystemExit("missing --port")


def _assistants() -> list[str]:
    path = os.environ.get("CREASY_STUB_ASSISTANTS_FILE", "")
    raw = os.environ.get("CREASY_STUB_ASSISTANTS", "")
    for candidate in (path, "assistants.json"):
        if candidate and os.path.isfile(candidate):
            raw = open(candidate, encoding="utf-8").read()
            break
    if not raw.strip() or raw.strip() == "file":
        return ["### Summary\nLooks fine."]
    data = json.loads(raw)
    if isinstance(data, list):
        return [str(item) for item in data]
    return [str(data)]


def _load() -> dict:
    if STATE_PATH.is_file():
        try:
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"id": "ses_stub1", "title": "", "messages": [], "busy": False, "next": 0}


def _save(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def _delay_sec() -> float:
    raw = os.environ.get("CREASY_STUB_DELAY_SEC", "0")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0


def _append_turn(user_text: str) -> None:
    global DELAY_UNTIL
    with LOCK:
        state = _load()
        assistants = _assistants()
        index = int(state.get("next") or 0)
        text = assistants[index] if index < len(assistants) else assistants[-1]
        state["next"] = index + 1
        state["messages"] = list(state.get("messages") or [])
        state["messages"].append(
            {
                "info": {"id": f"msg_user_{len(state['messages'])}", "role": "user"},
                "parts": [{"type": "text", "text": user_text}],
            }
        )
        state["messages"].append(
            {
                "info": {
                    "id": f"msg_asst_{len(state['messages'])}",
                    "role": "assistant",
                    "structured_output": True,
                },
                "parts": [{"type": "text", "text": text}],
            }
        )
        state["busy"] = False
        _save(state)
        DELAY_UNTIL = 0.0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _json(self, payload, status: int = 200) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        with LOCK:
            state = _load()
        if path == "/global/health":
            self._json({"ok": True})
            return
        if path == "/session/status":
            busy = bool(state.get("busy"))
            if DELAY_UNTIL and time.time() < DELAY_UNTIL:
                busy = False
            self._json({state["id"]: {"type": "busy" if busy else "idle", "id": state["id"]}})
            return
        if path == f"/session/{state['id']}":
            self._json({"id": state["id"], "title": state.get("title") or ""})
            return
        if path == f"/session/{state['id']}/message":
            self._json(list(state.get("messages") or []))
            return
        self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        global DELAY_UNTIL
        path = urlparse(self.path).path
        body = self._read_json()
        with LOCK:
            state = _load()
        if path == "/session":
            with LOCK:
                state = _load()
                state["title"] = str((body or {}).get("title") or "")
                state["id"] = "ses_stub1"
                _save(state)
            self._json({"id": "ses_stub1"})
            return
        if path in {
            f"/session/{state['id']}/prompt_async",
            f"/session/{state['id']}/message",
        }:
            parts = (body or {}).get("parts") or []
            user_text = ""
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict):
                        user_text += str(part.get("text") or "")
            has_prior = any(
                (m.get("info") or {}).get("role") == "assistant"
                for m in (state.get("messages") or [])
                if isinstance(m, dict)
            )
            wait = _delay_sec()
            if has_prior and wait > 0:
                DELAY_UNTIL = time.time() + wait
                threading.Thread(target=lambda: (time.sleep(wait), _append_turn(user_text)), daemon=True).start()
                self._json({"ok": True})
                return
            _append_turn(user_text)
            self._json({"ok": True})
            return
        if path == f"/session/{state['id']}/abort":
            with LOCK:
                state = _load()
                state["busy"] = False
                _save(state)
            self._json({"ok": True})
            return
        self._json({"error": "not found"}, status=404)


def main() -> None:
    host = "127.0.0.1"
    argv = sys.argv[1:]
    if "serve" in argv:
        argv = argv[argv.index("serve") + 1 :]
    for index, arg in enumerate(argv):
        if arg == "--hostname" and index + 1 < len(argv):
            host = argv[index + 1]
        elif arg.startswith("--hostname="):
            host = arg.split("=", 1)[1]
    httpd = ThreadingHTTPServer((host, _port(sys.argv)), Handler)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
