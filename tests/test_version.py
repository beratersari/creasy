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


def test_write_version_roundtrip(tmp_path: Path):
    path = tmp_path / "VERSION"
    assert write_version(path, "1.4.2") == "1.4.2"
    assert path.read_text(encoding="utf-8") == "1.4.2\n"
