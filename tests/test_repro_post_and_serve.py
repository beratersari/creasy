"""Reproduce: unposted notes marked success, and serve health on a dead child."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from creasy.gitlab.events import ReviewTrigger
from creasy.jobs.manager import Manager
from creasy.jobs.worker import RunResult
from creasy.opencode.serve import wait_health


def _trig() -> ReviewTrigger:
    return ReviewTrigger(
        kind="review",
        project_id=1,
        mr_iid=99,
        explicit=True,
        source_branch="feat",
        target_branch="main",
    )


def test_manager_marks_error_when_overview_note_was_not_posted(tmp_config):
    """GitLab 403 / network drop: runner has text but posted=False.

    The product of a job is the MR note. That result must not be success.
    """

    class UnpostedRunner:
        def run(self, job, should_stop):
            return RunResult(text="looks good", posted=False)

    manager = Manager(tmp_config, UnpostedRunner())
    manager.ready = True
    _, job, _ = manager.submit(_trig())
    assert job is not None
    deadline = time.time() + 3
    saved = job
    while time.time() < deadline:
        saved = manager.store.get(job.job_id) or saved
        if saved.status not in {"queued", "running"}:
            break
        time.sleep(0.05)
    manager.shutdown()
    assert saved.status == "error"
    assert saved.text == "looks good"
    assert saved.error_message == "overview note was not posted"


class _HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        raw = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _serve_health(port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _HealthHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def test_wait_health_rejects_dead_child_even_if_something_else_listens():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    httpd = _serve_health(port)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait(timeout=5)
    assert dead.poll() is not None
    try:
        with pytest.raises((TimeoutError, RuntimeError)):
            wait_health(
                f"http://127.0.0.1:{port}",
                str(Path.cwd()),
                timeout=1.5,
                proc=dead,
            )
    finally:
        httpd.shutdown()



