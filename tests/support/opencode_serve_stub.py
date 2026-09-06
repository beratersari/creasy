"""Minimal `opencode serve` stand-in for integration tests.

The worker launches this the same way it launches the real CLI:
`opencode serve --hostname 127.0.0.1 --port <n>`. It speaks the
OpenCode HTTP surface Creasy uses (health, session, prompt, messages).
Reply text comes from CREASY_STUB_ASSISTANTS (JSON list of strings).
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


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


ASSISTANTS = _assistants()
SESSION = {"id": "ses_stub1", "title": ""}
MESSAGES: list[dict] = []
BUSY = False


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
        if path == "/global/health":
            self._json({"ok": True})
            return
        if path == "/session/status":
            kind = "busy" if BUSY else "idle"
            self._json({SESSION["id"]: {"type": kind, "id": SESSION["id"]}})
            return
        if path == f"/session/{SESSION['id']}":
            self._json({"id": SESSION["id"], "title": SESSION["title"]})
            return
        if path == f"/session/{SESSION['id']}/message":
            self._json(list(MESSAGES))
            return
        self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        global BUSY
        path = urlparse(self.path).path
        body = self._read_json()
        if path == "/session":
            SESSION["title"] = str((body or {}).get("title") or "")
            SESSION["id"] = "ses_stub1"
            self._json({"id": SESSION["id"]})
            return
        if path in {
            f"/session/{SESSION['id']}/prompt_async",
            f"/session/{SESSION['id']}/message",
        }:
            parts = (body or {}).get("parts") or []
            user_text = ""
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict):
                        user_text += str(part.get("text") or "")
            MESSAGES.append(
                {
                    "info": {"id": f"msg_user_{len(MESSAGES)}", "role": "user"},
                    "parts": [{"type": "text", "text": user_text}],
                }
            )
            for index, text in enumerate(ASSISTANTS):
                MESSAGES.append(
                    {
                        "info": {
                            "id": f"msg_asst_{len(MESSAGES)}",
                            "role": "assistant",
                            "structured_output": True,
                        },
                        "parts": [{"type": "text", "text": text}],
                    }
                )
            BUSY = False
            self._json({"ok": True})
            return
        if path == f"/session/{SESSION['id']}/abort":
            BUSY = False
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
