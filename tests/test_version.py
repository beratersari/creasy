from __future__ import annotations

from pathlib import Path

import pytest

from creasy.version import bump_version, parse_version, read_version, write_version


def test_read_version_matches_repo_file():
    expected = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
    assert read_version() == expected
    from creasy import __version__

    assert __version__ == expected


def test_bump_parts():
    assert bump_version("0.2.0", "patch") == "0.2.1"
    assert bump_version("0.2.0", "minor") == "0.3.0"
    assert bump_version("0.2.0", "major") == "1.0.0"


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_version("v1")
    with pytest.raises(ValueError):
        bump_version("1.0.0", "build")


def test_changelog_has_current_version():
    version = (Path(__file__).resolve().parents[1] / "VERSION").read_text(encoding="utf-8").strip()
    log = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## Unreleased" in log
    assert f"## {version}" in log


def test_release_notes_extract():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "scripts" / "release_notes.py"
    spec = importlib.util.spec_from_file_location("creasy_release_notes", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = "## 0.2.0 — x\n\nHello.\n\n## 0.1.0\n\nOld.\n"
    assert mod.extract(text, "0.2.0") == "## 0.2.0 — x\n\nHello.\n"
    notes = mod.downloads_body("0.3.0")
    assert "creasy-0.3.0-windows-x64.zip" in notes
    assert "creasy-0.3.0-linux-x64.zip" in notes
    assert "creasy-0.3.0-darwin-arm64.zip" in notes
    assert "creasy-0.3.0-darwin-x64.zip" not in notes
    assert "windows-linux" not in notes
    assert "Source code" not in notes
    assert "opencoderman" in notes
    assert "install-review-agent" in notes


def test_write_version_roundtrip(tmp_path: Path):
    path = tmp_path / "VERSION"
    assert write_version(path, "1.4.2") == "1.4.2"
    assert path.read_text(encoding="utf-8") == "1.4.2\n"
