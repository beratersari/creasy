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
    assert "does not run npm" in bat.lower()
    assert "does not run npm" in sh.lower()
    assert r"web\dist\index.html" in bat
    assert "web/dist/index.html" in sh
    assert r"web\index.html" not in bat
    assert "web/index.html" not in sh


def test_vendor_scripts_call_build_dist():
    bat = _read("scripts/vendor.bat")
    sh = _read("scripts/vendor.sh")
    for body in (bat, sh):
        assert "build_dist.py" in body
        assert "--in-place" in body


def test_start_scripts_launch_creasy():
    bat = _read("scripts/start.bat")
    sh = _read("scripts/start.sh")
    assert "run-server.bat" in bat
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


def test_bat_files_avoid_osm_cmd_bugs():
    """OSM: unescaped '->' is a redirect; cmd /v:on /c eats delayed expansion."""
    bats = list((ROOT / "scripts").glob("*.bat")) + list(ROOT.glob("*.bat"))
    assert bats
    for path in bats:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("REM"):
                continue
            assert "->" not in line, f"{path.name} has unescaped -> : {line}"
        assert "cmd /v:on /c" not in text
        if path.name in {"install.bat", "start.bat", "vendor.bat"} or path.parent.name == "scripts":
            if path.name.startswith("install") or path.name.startswith("start") or path.name.startswith("vendor"):
                if path.parent.name == "scripts":
                    assert "maybe_pause" in text
                    assert "CREASY_NONINTERACTIVE" in text
                    assert "—" not in text


def test_install_opencode_scripts_require_configs():
    bat = _read("scripts/install-opencode.bat")
    sh = _read("scripts/install-opencode.sh")
    assert r"opencode-configs\agents\review.md" in bat
    assert "opencode-configs/agents/review.md" in sh
    assert "submodule update --init" in bat
    assert "submodule update --init" in sh


def test_ci_runs_vendor_install_start():
    workflow = _read(".github/workflows/ci.yml")
    assert "packaging/build_dist.py" in workflow
    assert "scripts/install.sh" in workflow
    assert "scripts/start.sh" in workflow
    assert "scripts\\install.bat" in workflow
    assert "scripts\\start.bat" in workflow
    assert "install-opencode" in workflow
    assert "submodules: true" in workflow
    assert workflow.count("submodules: true") >= 3
    assert "/health" in workflow
    assert "upload-artifact" in workflow
    assert "dist/stage/creasy-" in workflow
    assert "dist/creasy-*.zip" not in workflow
    assert "creasy-offline-zips" not in workflow
    assert "run-server.bat" in _read("scripts/start.bat")
    assert "cmd /v:on /c" not in _read("scripts/start.bat")
