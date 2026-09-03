from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Sequence


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("creasy")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter(
        "[%(asctime)s.%(msecs)03d] [%(levelname)-7s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    if name:
        return logging.getLogger(f"creasy.{name}" if not name.startswith("creasy") else name)
    return logging.getLogger("creasy")


def _fmt_cmd(argv: Sequence[Any]) -> str:
    return " ".join(str(part) for part in argv)


def _clip(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
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
