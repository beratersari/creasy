"""Repo vs PyInstaller-frozen locations."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def meipass() -> Path | None:
    if not is_frozen():
        return None
    raw = getattr(sys, "_MEIPASS", None)
    return Path(raw) if raw else None


def executable_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def bundled_file(*parts: str) -> Path | None:
    root = meipass()
    if root is None:
        return None
    path = root.joinpath(*parts)
    return path if path.is_file() else None


def bundled_dir(*parts: str) -> Path | None:
    root = meipass()
    if root is None:
        return None
    path = root.joinpath(*parts)
    return path if path.is_dir() else None
