"""Frozen vs repo resource paths."""

from __future__ import annotations

from pathlib import Path

from creasy.api import dashboard
from creasy.paths import bundled_dir
from creasy.version import version_path


def test_spa_dir_uses_repo_web_dist_when_not_frozen() -> None:
    root = Path(__file__).resolve().parents[1]
    assert dashboard.spa_dir() == root / "web" / "dist"
    assert bundled_dir("web", "dist") is None


def test_version_path_finds_repo_version() -> None:
    root = Path(__file__).resolve().parents[1]
    assert version_path() == root / "VERSION"
