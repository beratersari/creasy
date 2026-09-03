from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_install_scripts_are_offline_only():
    bat = _read("scripts/install.bat")
    sh = _read("scripts/install.sh")
    assert "--no-index" in bat
    assert "--no-index" in sh
    assert "find-links" in bat
    assert "--find-links" in sh
    assert "vendor\\python-wheels" in bat or "vendor/python-wheels" in bat
    assert "vendor/python-wheels" in sh
    assert "npm" not in bat.lower()
    assert "npm" not in sh.lower()


def test_vendor_scripts_download_runtime_deps():
    bat = _read("scripts/vendor.bat")
    sh = _read("scripts/vendor.sh")
    for body in (bat, sh):
        assert "pip download" in body
        assert "fastapi" in body
        assert "uvicorn" in body


def test_start_scripts_launch_creasy():
    bat = _read("scripts/start.bat")
    sh = _read("scripts/start.sh")
    assert "-m creasy" in bat
    assert "-m creasy" in sh
    assert "GIT_TERMINAL_PROMPT" in bat
    assert "GIT_TERMINAL_PROMPT" in sh
    assert "opencode" in bat.lower()
    assert "opencode" in sh


def test_root_wrappers_call_scripts():
    assert "scripts\\install.bat" in _read("install.bat")
    assert "scripts\\start.bat" in _read("start.bat")
    assert "scripts/install.sh" in _read("install.sh")
    assert "scripts/start.sh" in _read("start.sh")


def test_ci_runs_vendor_install_start():
    workflow = _read(".github/workflows/ci.yml")
    assert "scripts/vendor.sh" in workflow
    assert "scripts/install.sh" in workflow
    assert "scripts/start.sh" in workflow
    assert "scripts\\vendor.bat" in workflow
    assert "scripts\\install.bat" in workflow
    assert "scripts\\start.bat" in workflow
    assert "/health" in workflow
