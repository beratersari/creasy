"""Local webhook tester. Sends fake GitLab events at a running Creasy."""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tester") not in sys.path:
    sys.path.insert(0, str(ROOT / "tester"))

from payloads import (  # noqa: E402
    DEFAULT_MR_IID,
    DEFAULT_PROJECT_ID,
    EVENTS,
    REPOS,
    build_payload,
)

PAGE = Path(__file__).resolve().parent / "index.html"


def _env() -> dict[str, str]:
    load_dotenv(ROOT / ".env", override=False)
    return {
        "creasy_url": os.getenv("CREASY_URL", "http://127.0.0.1:9001").rstrip("/"),
        "webhook_secret": (os.getenv("WEBHOOK_SECRET") or "").strip(),
        "gitlab_url": (os.getenv("GITLAB_URL") or "https://gitlab.com").rstrip("/"),
        "gitlab_token": (os.getenv("GITLAB_TOKEN") or "").strip(),
    }


def _json_body(handler: BaseHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length") or "0")
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _send(handler: BaseHTTPRequestHandler, code: int, body: Any, *, html: bool = False) -> None:
    if html:
        data = body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
        ctype = "text/html; charset=utf-8"
    else:
        data = json.dumps(body).encode("utf-8")
        ctype = "application/json"
    handler.send_response(code)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _http(method: str, url: str, *, headers: dict[str, str] | None = None, data: bytes | None = None, timeout: float = 20):
    req = Request(url, data=data, method=method, headers=headers or {})
    try:
        with urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
            parsed: Any
            try:
                parsed = json.loads(text) if text else {}
            except json.JSONDecodeError:
                parsed = {"text": text}
            return resp.status, parsed
    except HTTPError as exc:
        text = exc.read().decode("utf-8")
        try:
            parsed = json.loads(text) if text else {"detail": text}
        except json.JSONDecodeError:
            parsed = {"detail": text}
        return exc.code, parsed
    except URLError as exc:
        return 0, {"detail": str(exc.reason)}


def _list_mrs(project_id: int) -> list[dict[str, Any]]:
    cfg = _env()
    if not cfg["gitlab_token"]:
        return []
    status, body = _http(
        "GET",
        f"{cfg['gitlab_url']}/api/v4/projects/{project_id}/merge_requests?state=opened&per_page=50",
        headers={"PRIVATE-TOKEN": cfg["gitlab_token"]},
        timeout=15,
    )
    if status != 200 or not isinstance(body, list):
        return []
    out: list[dict[str, Any]] = []
    for row in body:
        if not isinstance(row, dict):
            continue
        refs = row.get("diff_refs") or {}
        out.append(
            {
                "iid": row.get("iid"),
                "title": row.get("title") or "",
                "source_branch": row.get("source_branch") or "",
                "target_branch": row.get("target_branch") or "",
                "sha": row.get("sha") or refs.get("head_sha") or "",
                "web_url": row.get("web_url") or "",
                "draft": bool(row.get("draft") or row.get("work_in_progress")),
            }
        )
    return out


def _fire(body: dict[str, Any]) -> tuple[int, Any, dict[str, Any]]:
    cfg = _env()
    event = str(body.get("event") or "")
    project_id = int(body.get("project_id") or DEFAULT_PROJECT_ID)
    mr_iid = int(body.get("mr_iid") or DEFAULT_MR_IID)
    payload = build_payload(
        event,
        project_id=project_id,
        mr_iid=mr_iid,
        source_branch=str(body.get("source_branch") or ""),
        target_branch=str(body.get("target_branch") or ""),
        note=str(body.get("note") or ""),
        sha=str(body.get("sha") or ""),
        web_url=str(body.get("web_url") or ""),
    )
    headers = {"Content-Type": "application/json"}
    if cfg["webhook_secret"]:
        headers["X-Gitlab-Token"] = cfg["webhook_secret"]
    status, resp = _http(
        "POST",
        cfg["creasy_url"] + "/webhook",
        headers=headers,
        data=json.dumps(payload).encode("utf-8"),
    )
    return status, resp, payload


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            _send(self, 200, PAGE.read_bytes(), html=True)
            return
        if parsed.path == "/api/meta":
            cfg = _env()
            _send(
                self,
                200,
                {
                    "creasy_url": cfg["creasy_url"],
                    "has_secret": bool(cfg["webhook_secret"]),
                    "has_gitlab": bool(cfg["gitlab_token"]),
                    "repos": REPOS,
                    "events": EVENTS,
                    "default_project_id": DEFAULT_PROJECT_ID,
                    "default_mr_iid": DEFAULT_MR_IID,
                },
            )
            return
        if parsed.path == "/api/mrs":
            qs = parse_qs(parsed.query)
            project_id = int((qs.get("project_id") or [DEFAULT_PROJECT_ID])[0])
            _send(self, 200, {"project_id": project_id, "mrs": _list_mrs(project_id)})
            return
        if parsed.path == "/api/creasy-health":
            cfg = _env()
            status, body = _http("GET", cfg["creasy_url"] + "/health", timeout=5)
            _send(self, 200, {"http": status, "body": body})
            return
        _send(self, 404, {"detail": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/fire":
            _send(self, 404, {"detail": "not found"})
            return
        try:
            body = _json_body(self)
            status, resp, payload = _fire(body)
        except Exception as exc:  # noqa: BLE001
            _send(self, 400, {"detail": str(exc)})
            return
        _send(self, 200, {"http": status, "response": resp, "payload": payload})


def main() -> None:
    parser = argparse.ArgumentParser(description="Creasy webhook tester")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    cfg = _env()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Creasy tester  http://{args.host}:{args.port}/")
    print(f"Target         {cfg['creasy_url']}/webhook")
    print(f"Default repo   {REPOS[0]['path']}  project={DEFAULT_PROJECT_ID}  MR !{DEFAULT_MR_IID}")
    print(f"Secret         {'set' if cfg['webhook_secret'] else 'MISSING (.env WEBHOOK_SECRET)'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ntester stopped")


if __name__ == "__main__":
    main()
