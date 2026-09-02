from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import httpx

from creasy.logging import get_logger
from creasy.opencode.kill import kill_pid

logger = get_logger("serve")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def serve_log_path(serve_dir: Path, job_id: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in (job_id or "")) or "job"
    return Path(serve_dir) / f"{safe}.log"


def read_serve_log(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


@dataclass
class ServeHandle:
    pid: int
    port: int
    base_url: str
    proc: subprocess.Popen
    log_path: Path


def start_serve(
    *,
    bin_name: str,
    cwd: Path,
    log_path: Path,
    timeout: float,
    should_stop: Optional[Callable[[], bool]] = None,
    on_spawn: Optional[Callable[[ServeHandle], None]] = None,
) -> ServeHandle:
    if should_stop and should_stop():
        raise RuntimeError("manager shutting down")
    binary = shutil.which(bin_name) or bin_name
    port = free_port()
    cmd = [
        binary,
        "serve",
        "--hostname",
        "127.0.0.1",
        "--port",
        str(port),
        "--print-logs",
        "--log-level",
        "INFO",
    ]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "a", encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_f.write(f"\n===== opencode serve start {stamp} port={port} cwd={cwd} =====\n")
    log_f.flush()
    env = os.environ.copy()
    env["OPENCODE_SERVER_PASSWORD"] = ""
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )
    except OSError:
        log_f.close()
        raise
    handle = ServeHandle(
        pid=int(proc.pid),
        port=port,
        base_url=f"http://127.0.0.1:{port}",
        proc=proc,
        log_path=log_path,
    )
    setattr(proc, "_creasy_log_f", log_f)
    if on_spawn is not None:
        on_spawn(handle)
    try:
        wait_health(handle.base_url, str(cwd), timeout=timeout, should_stop=should_stop)
    except Exception:
        stop_serve(handle)
        raise
    logger.info("opencode serve up pid=%s port=%s", handle.pid, handle.port)
    return handle


def wait_health(
    base_url: str,
    directory: str,
    *,
    timeout: float,
    should_stop: Optional[Callable[[], bool]] = None,
) -> dict:
    deadline = time.time() + timeout
    last: Optional[Exception] = None
    headers = {"x-opencode-directory": directory}
    url = base_url.rstrip("/") + "/global/health"
    with httpx.Client(verify=False, timeout=5.0) as client:
        while time.time() < deadline:
            if should_stop and should_stop():
                raise RuntimeError("manager shutting down")
            try:
                response = client.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict):
                        return data
                last = Exception(f"HTTP {response.status_code}")
            except RuntimeError:
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
            time.sleep(0.3)
    raise TimeoutError(f"serve health not ready at {base_url} last={last}")


def stop_serve(handle: Optional[ServeHandle]) -> None:
    if handle is None:
        return
    kill_pid(handle.pid)
    try:
        handle.proc.wait(timeout=5)
    except Exception:
        kill_pid(handle.pid)
    log_f = getattr(handle.proc, "_creasy_log_f", None)
    if log_f is not None:
        try:
            log_f.close()
        except Exception:
            pass
