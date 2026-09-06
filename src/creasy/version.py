"""Product version. ``VERSION`` at the repo root is the source of truth."""

from __future__ import annotations

import re
from pathlib import Path

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


def version_path() -> Path | None:
    here = Path(__file__).resolve().parent
    for folder in (here, *here.parents):
        candidate = folder / "VERSION"
        if not candidate.is_file():
            continue
        if folder == here or (folder / "pyproject.toml").is_file():
            return candidate
    return None


def read_version() -> str:
    path = version_path()
    if path is not None:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    try:
        from importlib.metadata import version

        return version("creasy")
    except Exception:
        return "0.0.0-dev"


def parse_version(text: str) -> tuple[int, int, int, str]:
    match = _SEMVER.match((text or "").strip())
    if not match:
        raise ValueError(f"invalid version {text!r}; expected MAJOR.MINOR.PATCH")
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4) or ""


def bump_version(current: str, part: str) -> str:
    major, minor, patch, _pre = parse_version(current)
    key = (part or "").strip().lower()
    if key == "major":
        return f"{major + 1}.0.0"
    if key == "minor":
        return f"{major}.{minor + 1}.0"
    if key == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError("part must be major, minor, or patch")


def write_version(path: Path, version: str) -> str:
    parsed = parse_version(version)
    text = f"{parsed[0]}.{parsed[1]}.{parsed[2]}"
    if parsed[3]:
        text = f"{text}-{parsed[3]}"
    path.write_text(text + "\n", encoding="utf-8")
    return text


__version__ = read_version()
