"""Start scripts follow OSM launcher rules."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_start_scripts_match_osm_backend_pattern() -> None:
    win = (ROOT / "scripts" / "start.bat").read_text(encoding="utf-8")
    runner = (ROOT / "scripts" / "run-server.bat").read_text(encoding="utf-8")
    sh = (ROOT / "scripts" / "start.sh").read_text(encoding="utf-8")
    assert "run-server.bat" in win
    assert "cmd /v:on /c" not in win
    assert "->" not in "\n".join(
        line for line in win.splitlines() if not line.strip().upper().startswith("REM")
    )
    assert "-m creasy" in runner
    assert "wrapper-exit.log" in runner
    assert "-m creasy" in sh
    assert "install-opencode" in win
    assert "install-opencode" in sh
    assert r"web\dist\index.html" in win
    assert "web/dist/index.html" in sh
    assert r"web\index.html" not in win
    assert "web/index.html" not in sh
    oc = (ROOT / "scripts" / "install_opencode.py").read_text(encoding="utf-8")
    assert "wipe_old" not in oc
    assert "load_pack_installer" in oc
    assert "latest_backup_binary" in oc
    assert "install_review_agent" in oc
    assert "vendor" in oc and "bin" in oc
