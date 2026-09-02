from __future__ import annotations

import os
import signal
import subprocess
from typing import Iterable, Optional

from creasy.logging import get_logger

logger = get_logger("kill")


def _as_pid(pid: Optional[int]) -> Optional[int]:
    try:
        value = int(pid or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def may_kill(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        parent = os.getppid()
    except Exception:
        parent = 0
    return pid != parent


def kill_pid(pid: Optional[int]) -> None:
    resolved = _as_pid(pid)
    if not resolved or not may_kill(resolved):
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(resolved)],
                capture_output=True,
                check=False,
                timeout=15,
            )
        else:
            try:
                os.killpg(resolved, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(resolved, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("kill_pid failed pid=%s err=%s", resolved, exc)


def kill_job_tree(pids: Iterable[Optional[int]]) -> None:
    for pid in pids:
        kill_pid(pid)
