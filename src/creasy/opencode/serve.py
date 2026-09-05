from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
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


def restricted_child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Env for opencode serve / git children on restricted Windows boxes."""
    env = dict(base if base is not None else os.environ)
    env["OPENCODE_SERVER_PASSWORD"] = ""
    # Skip models.dev (stalls / log-spam on intercept or air-gapped nets).
    env["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSL_NO_VERIFY"] = "1"
    env.setdefault("GIT_ASKPASS", "echo")
    env["GCM_INTERACTIVE"] = "never"
    env["GCM_MODAL_PROMPT"] = "false"
    env["GCM_GUI_PROMPT"] = "false"
    return env


def _vendor_ripgrep() -> Path | None:
    name = "rg.exe" if os.name == "nt" else "rg"
    root = Path(__file__).resolve().parents[3]
    vendor = root / "vendor" / "bin"
    tags: list[str] = []
    if os.name == "nt":
        tags = ["windows"]
    elif sys.platform == "darwin":
        machine = platform.machine().lower()
        tags = ["darwin-arm64" if machine in {"arm64", "aarch64"} else "darwin-x64"]
    else:
        tags = ["linux"]
    candidates = [vendor / tag / name for tag in tags]
    candidates.append(vendor / name)
    for path in candidates:
        if path.is_file():
            return path
    return None


def seed_ripgrep_cache(user_home: Path | None = None) -> Path | None:
    """Copy vendored rg into ~/.cache/opencode/bin so serve does not download it."""
    name = "rg.exe" if os.name == "nt" else "rg"
    home = Path(user_home) if user_home is not None else Path(
        os.environ.get("USERPROFILE") or Path.home()
    )
    dest = home / ".cache" / "opencode" / "bin" / name
    if dest.is_file():
        return dest
    sources = [
        home / ".opencode" / "bin" / name,
        _vendor_ripgrep(),
    ]
    for src in sources:
        if src is None or not src.is_file():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        if os.name != "nt":
            dest.chmod(dest.stat().st_mode | 0o111)
        logger.info("seeded ripgrep %s", dest)
        return dest
    return None


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
    seed_ripgrep_cache()
    env = restricted_child_env()
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
        wait_health(
            handle.base_url,
            str(cwd),
            timeout=timeout,
            should_stop=should_stop,
            proc=handle.proc,
        )
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
    proc: Optional[subprocess.Popen] = None,
) -> dict:
    deadline = time.time() + timeout
    last: Optional[Exception] = None
    headers = {"x-opencode-directory": directory}
    url = base_url.rstrip("/") + "/global/health"
    with httpx.Client(verify=False, timeout=5.0) as client:
        while time.time() < deadline:
            if should_stop and should_stop():
                raise RuntimeError("manager shutting down")
            if proc is not None and proc.poll() is not None:
                raise RuntimeError(
                    f"serve process exited before health pid={proc.pid} code={proc.returncode}"
                )
            try:
                response = client.get(url, headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, dict):
                        if proc is not None and proc.poll() is not None:
                            raise RuntimeError(
                                f"serve process exited before health pid={proc.pid} code={proc.returncode}"
                            )
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
