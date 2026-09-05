"""GET /api/report-context — process extras for a client-built issue zip."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from creasy import __version__
from creasy.config import Config
from creasy.logging import redact_userinfo

_MAX_APP_LOG = 512 * 1024
_MAX_CRASH_LOG = 256 * 1024
_MAX_WRAPPER_LOG = 128 * 1024
_MAX_OPENCODE_LOG = 256 * 1024
_MAX_OPENCODE_FILES = 3
_CLI_TIMEOUT = 4.0


def build_report_context(manager: Any) -> dict[str, Any]:
    """Safe process snapshot for the dashboard report zip. Never 500s."""
    try:
        cfg: Config = manager.config
    except Exception:  # noqa: BLE001
        cfg = Config()
    try:
        health = manager.health()
    except Exception:  # noqa: BLE001
        health = {}
    running = int(health.get("running") or 0)
    queued = int(health.get("queued") or 0)
    return {
        "meta": {
            "app_name": "creasy",
            "version": __version__,
            "server_time": _now(),
        },
        "runtime": _runtime(cfg, running=running, queued=queued),
        "settings": public_settings(cfg),
        "queue": {
            "items": _queue_items(manager),
            "queued_count": queued,
        },
        "live": {"running": running, "queued": queued},
        "app_log": read_capped_text(cfg.log_dir / "app.log", max_bytes=_MAX_APP_LOG),
        "crash_log": read_capped_text(cfg.log_dir / "crash.log", max_bytes=_MAX_CRASH_LOG),
        "wrapper_exit_log": read_capped_text(
            Path.cwd() / "logs" / "wrapper-exit.log",
            max_bytes=_MAX_WRAPPER_LOG,
        ),
        "opencode_logs": _opencode_cli_logs(),
        "serve_logs_present": _serve_log_names(cfg),
        "server_time": _now(),
    }


def public_settings(cfg: Config) -> dict[str, Any]:
    return {
        "host": cfg.host,
        "port": cfg.port,
        "gitlab_url": cfg.gitlab_url,
        "gitlab_token_set": bool(cfg.gitlab_token),
        "webhook_secret_set": bool(cfg.webhook_secret),
        "dashboard_token_set": bool(cfg.dashboard_token),
        "opencode_model": cfg.opencode_model,
        "opencode_timeout": cfg.opencode_timeout,
        "opencode_retry_count": cfg.opencode_retry_count,
        "opencode_agent": cfg.opencode_agent,
        "opencode_bin": cfg.opencode_bin,
        "max_concurrent_jobs": cfg.max_concurrent_jobs,
        "data_dir": str(cfg.data_dir),
        "work_dir": str(cfg.work_dir),
        "log_dir": str(cfg.log_dir),
        "serve_dir": str(cfg.serve_dir),
        "skip_draft_mrs": cfg.skip_draft_mrs,
        "log_level": cfg.log_level,
        "git_timeout": cfg.git_timeout,
        "serve_health_timeout": cfg.serve_health_timeout,
        "hang_timeout": cfg.hang_timeout,
    }


def read_capped_text(path: Path, *, max_bytes: int) -> dict[str, Any]:
    dest = Path(path) if path else Path()
    if not path or not dest.is_file():
        return {"text": "", "missing": True, "truncated": False, "path": str(path or "")}
    try:
        data = dest.read_bytes()
    except OSError as exc:
        return {"text": f"(unreadable: {exc})\n", "missing": False, "truncated": False, "path": str(dest)}
    truncated = len(data) > max_bytes
    if truncated:
        data = data[-max_bytes:]
    text = redact_userinfo(data.decode("utf-8", errors="replace"))
    if truncated:
        text = f"[truncated to last {max_bytes} bytes]\n{text}"
    if text and not text.endswith("\n"):
        text += "\n"
    return {"text": text, "missing": False, "truncated": truncated, "path": str(dest)}


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _queue_items(manager: Any) -> list[dict[str, Any]]:
    try:
        return list(manager.queue.public_items())
    except Exception:  # noqa: BLE001
        return []


def _runtime(cfg: Config, *, running: int, queued: int) -> dict[str, Any]:
    oc = (cfg.opencode_bin or "opencode").strip() or "opencode"
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python": sys.version,
        "python_executable": sys.executable,
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
        "creasy_version": __version__,
        "which": {"git": shutil.which("git"), "opencode": shutil.which(oc)},
        "cli_versions": {"git": _cli_version("git"), "opencode": _cli_version(oc)},
        "live": {"running": running, "queued": queued},
    }


def _cli_version(binary: str) -> dict[str, Any]:
    path = shutil.which(binary) or binary
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT,
            check=False,
            env=env,
        )
        text = ((proc.stdout or "") + (proc.stderr or "")).strip()
        return {
            "path": path,
            "exit_code": proc.returncode,
            "output": text.splitlines()[0] if text else "",
        }
    except FileNotFoundError:
        return {"path": path, "error": "not found"}
    except Exception as exc:  # noqa: BLE001
        return {"path": path, "error": str(exc)}


def _opencode_cli_logs() -> list[dict[str, Any]]:
    roots = [
        Path.home() / ".local" / "share" / "opencode" / "log",
        Path.home() / ".opencode" / "log",
    ]
    added: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            files = sorted(
                [p for p in root.iterdir() if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            continue
        for path in files:
            if len(added) >= _MAX_OPENCODE_FILES:
                return added
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            blob = read_capped_text(path, max_bytes=_MAX_OPENCODE_LOG)
            if blob.get("missing"):
                continue
            added.append({"name": path.name, **blob})
    return added


def _serve_log_names(cfg: Config) -> list[str]:
    serve_dir = cfg.serve_dir
    if not serve_dir or not Path(serve_dir).is_dir():
        return []
    try:
        names = sorted(
            p.name for p in Path(serve_dir).iterdir() if p.is_file() and p.suffix.lower() == ".log"
        )
    except OSError:
        return []
    return names[:50]
