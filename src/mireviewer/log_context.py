"""Per-job log context via contextvars (OSM / Virtual Developer)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, Optional

_job_id: ContextVar[Optional[str]] = ContextVar("creasy_job_id", default=None)
_mr_key: ContextVar[Optional[str]] = ContextVar("creasy_mr_key", default=None)
_log_file: ContextVar[Optional[str]] = ContextVar("creasy_log_file", default=None)


def get_job_id() -> Optional[str]:
    return _job_id.get()


def get_mr_key() -> Optional[str]:
    return _mr_key.get()


def get_log_file() -> Optional[str]:
    return _log_file.get()


def bind(
    job_id: Optional[str] = None,
    mr_key: Optional[str] = None,
    log_file: Optional[str] = None,
) -> None:
    if job_id is not None:
        _job_id.set((job_id or "").strip() or None)
    if mr_key is not None:
        _mr_key.set((mr_key or "").strip() or None)
    if log_file is not None:
        _log_file.set((log_file or "").strip() or None)


def clear() -> None:
    _job_id.set(None)
    _mr_key.set(None)
    _log_file.set(None)


@contextmanager
def bound(
    job_id: Optional[str] = None,
    mr_key: Optional[str] = None,
    log_file: Optional[str] = None,
) -> Iterator[None]:
    bind(job_id=job_id, mr_key=mr_key, log_file=log_file)
    try:
        yield
    finally:
        clear()
