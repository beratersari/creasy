from __future__ import annotations

import logging
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from creasy import log_context

# https://oauth2:TOKEN@host → https://host  (also user:pass@)
_USERINFO_RE = re.compile(r"(https?://)[^/\s\"'<>]+@", re.IGNORECASE)


def redact_userinfo(text: str) -> str:
    """Strip URL userinfo so clone tokens never appear in logs or notes."""
    if not text:
        return ""
    return _USERINFO_RE.sub(r"\1", str(text))


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        job_id = log_context.get_job_id() or "-"
        jira_id = log_context.get_mr_key() or "-"
        msg = redact_userinfo(record.getMessage())
        return (
            f"[{ts}] [{record.levelname:<7}] [{record.name}] "
            f"[job_id={job_id} jira_id={jira_id}] {msg}"
        )


class _JobFileHandler(logging.Handler):
    """Append the current job's lines to its own file so the UI can filter by job_id."""

    def __init__(self, job_log_dir: Path) -> None:
        super().__init__()
        self.job_log_dir = Path(job_log_dir)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        name = log_context.get_log_file()
        if not name:
            return
        path = self.job_log_dir / Path(name).name
        try:
            line = self.format(record) + "\n"
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
        except Exception:  # noqa: BLE001
            self.handleError(record)


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("creasy")
    fmt = _Formatter()
    if not logger.handlers:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        logger.addHandler(stream)
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
            file_handler.setFormatter(fmt)
            logger.addHandler(file_handler)
        logger.propagate = False
    if log_dir is not None:
        dest = Path(log_dir)
        for handler in list(logger.handlers):
            if isinstance(handler, _JobFileHandler) and handler.job_log_dir != dest:
                logger.removeHandler(handler)
        if not any(isinstance(h, _JobFileHandler) for h in logger.handlers):
            job_h = _JobFileHandler(dest)
            job_h.setFormatter(fmt)
            logger.addHandler(job_h)
    return logger


def _line_for_job(line: str, job_id: str) -> bool:
    if not job_id or not line:
        return False
    return f"job_id={job_id}" in line or f"job={job_id}" in line


def _as_log_row(line: str, job_id: str, mr_key: str) -> dict[str, str]:
    ts = line[1:24] if line.startswith("[") and len(line) >= 24 else ""
    return {
        "timestamp": ts,
        "message": line,
        "job_id": job_id,
        "jira_id": mr_key,
    }


def read_job_log_lines(
    log_dir: Path,
    job_id: str,
    *,
    mr_key: str = "",
    log_file: Optional[str] = None,
    limit: int = 2000,
) -> list[dict[str, str]]:
    """Return this job's lines from its file, plus any app.log hits for the same job_id."""
    root = Path(log_dir)
    seen: set[str] = set()
    out: list[dict[str, str]] = []

    def add(line: str) -> None:
        text = line.rstrip("\n")
        if not text or text in seen:
            return
        seen.add(text)
        out.append(_as_log_row(text, job_id, mr_key))

    path: Optional[Path] = None
    if log_file:
        candidate = root / Path(log_file).name
        if candidate.is_file():
            path = candidate
    if path is None and job_id:
        matches = sorted(root.glob(f"*-{job_id}.log"))
        path = matches[-1] if matches else None
    if path is not None and path.is_file():
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                add(line)
        except OSError:
            pass

    app_log = root / "app.log"
    if app_log.is_file():
        try:
            for line in app_log.read_text(encoding="utf-8", errors="replace").splitlines():
                if _line_for_job(line, job_id):
                    add(line)
        except OSError:
            pass

    if limit and limit > 0:
        return out[-int(limit) :]
    return out


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"creasy.{name}" if not name.startswith("creasy") else name)
    return logging.getLogger("creasy")


def _fmt_cmd(argv: Sequence[Any]) -> str:
    return redact_userinfo(" ".join(str(part) for part in argv))


def _clip(value: Any, limit: int) -> str:
    text = redact_userinfo("" if value is None else str(value))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def log_command(
    logger: logging.Logger,
    argv: Sequence[Any],
    *,
    cwd: Any = ".",
    timeout: Any = None,
    pid: Any = None,
    extra: str = "",
) -> None:
    tail = f" {extra}" if extra else ""
    logger.info(
        "command argv=%s cwd=%s timeout=%s pid=%s%s",
        _fmt_cmd(argv),
        cwd or ".",
        timeout if timeout is not None else "-",
        pid if pid is not None else "-",
        tail,
    )


def log_command_result(
    logger: logging.Logger,
    argv: Sequence[Any],
    *,
    returncode: Any,
    stdout: Any = "",
    stderr: Any = "",
    cwd: Any = ".",
    timeout: Any = None,
    pid: Any = None,
) -> None:
    code = 0 if returncode is None else int(returncode)
    if code == 0:
        logger.info(
            "command ok exit=0 argv=%s cwd=%s pid=%s stdout=%s",
            _fmt_cmd(argv),
            cwd or ".",
            pid if pid is not None else "-",
            _clip(stdout, 400) or "(empty)",
        )
        return
    logger.error(
        "command FAIL exit=%s argv=%s cwd=%s timeout=%s pid=%s stdout=%s stderr=%s",
        code,
        _fmt_cmd(argv),
        cwd or ".",
        timeout if timeout is not None else "-",
        pid if pid is not None else "-",
        _clip(stdout, 1200),
        _clip(stderr, 1200),
    )


def log_fail(logger: logging.Logger, headline: str, **fields: Any) -> None:
    bits = [f"{key}={_clip(value, 500)}" for key, value in fields.items()]
    logger.error("%s %s", headline, " ".join(bits) if bits else "")
