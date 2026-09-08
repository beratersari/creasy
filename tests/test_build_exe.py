"""Executable zip layout. Does not run PyInstaller."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    path = REPO / "packaging" / "build_exe.py"
    spec = importlib.util.spec_from_file_location("creasy_build_exe", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plant_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    agents = root / "opencoderman" / "agents"
    skills = root / "opencoderman" / "skills" / "secrets"
    scripts = root / "scripts"
    agents.mkdir(parents=True)
    skills.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (agents / "code-reviewer.md").write_text("agent\n", encoding="utf-8")
    (agents / "planner.md").write_text("planner\n", encoding="utf-8")
    (skills / "SKILL.md").write_text("skill\n", encoding="utf-8")
    (root / "opencoderman" / "README.md").write_text("pack\n", encoding="utf-8")
    (root / "opencoderman" / "install.py").write_text("print('no')\n", encoding="utf-8")
    (root / "opencoderman" / ".git").mkdir()
    (root / "opencoderman" / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    (root / "opencoderman" / "vendor" / "bin").mkdir(parents=True)
    (root / "opencoderman" / "vendor" / "bin" / "opencode.exe").write_bytes(b"no")
    (scripts / "install-review-agent.bat").write_text("bat\n", encoding="utf-8")
    (scripts / "install-review-agent.sh").write_text("sh\n", encoding="utf-8")
    return root


def test_zip_contains_exe_config_opencoderman_and_scripts(tmp_path: Path) -> None:
    exe = tmp_path / "creasy.exe"
    cfg = tmp_path / ".env.example"
    exe.write_bytes(b"exe")
    cfg.write_text("PORT=9001\n", encoding="utf-8")
    dest = tmp_path / "creasy-0.3.0-windows-x64.zip"
    mod = _load()
    mod.write_exe_zip(exe, cfg, dest, root=_plant_root(tmp_path))
    mod.assert_exe_zip(dest, expect_exe="creasy.exe")
    with zipfile.ZipFile(dest) as zf:
        names = set(zf.namelist())
    assert "creasy.exe" in names
    assert ".env.example" in names
    assert "install-review-agent.bat" in names
    assert "install-review-agent.sh" in names
    assert "opencoderman/agents/code-reviewer.md" in names
    assert "opencoderman/agents/planner.md" in names
    assert "opencoderman/skills/secrets/SKILL.md" in names
    assert "opencoderman/README.md" not in names
    assert "opencoderman/install.py" not in names
    assert not any(".git" in name.split("/") for name in names)
    assert not any(name.startswith("opencoderman/vendor/") for name in names)
    assert all(
        name in {
            "creasy.exe",
            ".env.example",
            "install-review-agent.bat",
            "install-review-agent.sh",
        }
        or name.startswith("opencoderman/agents/")
        or name.startswith("opencoderman/skills/")
        for name in names
    )


def test_zip_name() -> None:
    assert _load().zip_name("0.3.0", "linux-x64") == "creasy-0.3.0-linux-x64.zip"


def test_assert_rejects_extra_files(tmp_path: Path) -> None:
    dest = tmp_path / "bad.zip"
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("creasy", "x")
        zf.writestr(".env.example", "y")
        zf.writestr("install-review-agent.bat", "b")
        zf.writestr("install-review-agent.sh", "s")
        zf.writestr("opencoderman/agents/code-reviewer.md", "a")
        zf.writestr("opencoderman/skills/secrets/SKILL.md", "k")
        zf.writestr("README.txt", "no")
    try:
        _load().assert_exe_zip(dest, expect_exe="creasy")
    except SystemExit:
        return
    raise AssertionError("expected SystemExit")


def test_assert_requires_review_agent(tmp_path: Path) -> None:
    dest = tmp_path / "bad.zip"
    with zipfile.ZipFile(dest, "w") as zf:
        zf.writestr("creasy", "x")
        zf.writestr(".env.example", "y")
    try:
        _load().assert_exe_zip(dest, expect_exe="creasy")
    except SystemExit as exc:
        assert "code-reviewer.md" in str(exc)
        return
    raise AssertionError("expected SystemExit")


def test_repo_opencoderman_entries_skip_git() -> None:
    entries = _load().opencoderman_zip_entries(REPO / "opencoderman")
    names = [arc for _path, arc in entries]
    assert "opencoderman/agents/code-reviewer.md" in names
    assert any(name.startswith("opencoderman/skills/") and name.endswith("/SKILL.md") for name in names)
    assert "opencoderman/README.md" not in names
    assert not any(".git" in name.split("/") for name in names)
    assert all(name.startswith("opencoderman/agents/") or name.startswith("opencoderman/skills/") for name in names)
