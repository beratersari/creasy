"""OpenCode installer keeps an existing home and adds the review agent."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    path = REPO / "scripts" / "install_opencode.py"
    spec = importlib.util.spec_from_file_location("creasy_install_opencode", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plant_root(tmp_path: Path, *, vendor: bool) -> Path:
    root = tmp_path / "pkg"
    shutil.copytree(REPO / "opencode-configs" / "agents", root / "opencode-configs" / "agents")
    shutil.copytree(REPO / "opencode-configs" / "skills", root / "opencode-configs" / "skills")
    if vendor:
        bin_name = "opencode.exe" if os.name == "nt" else "opencode"
        if os.name == "nt":
            path = root / "vendor" / "bin" / "windows" / bin_name
        else:
            path = root / "vendor" / "bin" / bin_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"NEW")
    return root


def test_review_agent_source_is_primary_readonly() -> None:
    text = (REPO / "opencode-configs" / "agents" / "review.md").read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "mode: primary" in text
    assert "edit: deny" in text
    assert "task: deny" in text
    assert "never edit" in text.lower() or "You never edit" in text
    assert "merge-base" in text
    assert "agent/rules/CODE_REVIEW.md" in text
    assert "Before you review, read project rules" in text
    assert "detect the C++ dialect" in text
    assert "CMAKE_CXX_STANDARD" in text
    assert "do not assume modern C++" in text
    assert 'skill({ name: "cpp98" })' in text
    assert 'skill({ name: "modern-cpp" })' in text


def test_fresh_install_writes_binary_config_and_agent(tmp_path: Path) -> None:
    mod = _load()
    root = _plant_root(tmp_path, vendor=True)
    home = tmp_path / "home"
    dest = mod.install(root, user_home=home)
    assert dest.is_file()
    cfg = json.loads((home / ".opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert cfg.get("plugin") == []
    agent = (home / ".config" / "opencode" / "agents" / "review.md").read_text(encoding="utf-8")
    assert "mode: primary" in agent
    copy = (home / ".opencode" / "agents" / "review.md").read_text(encoding="utf-8")
    assert copy == agent
    for name in ("cpp98", "modern-cpp"):
        skill = home / ".config" / "opencode" / "skills" / name / "SKILL.md"
        assert skill.is_file()
        body = skill.read_text(encoding="utf-8")
        assert f"name: {name}" in body
        assert (home / ".opencode" / "skills" / name / "SKILL.md").read_text(
            encoding="utf-8"
        ) == body


def test_existing_install_keeps_binary_and_plugins(tmp_path: Path) -> None:
    mod = _load()
    root = _plant_root(tmp_path, vendor=True)
    home = tmp_path / "home"
    oc = home / ".opencode"
    (oc / "bin").mkdir(parents=True)
    existing = oc / "bin" / ("opencode.exe" if os.name == "nt" else "opencode")
    existing.write_bytes(b"OLD-BINARY")
    user_cfg = {"plugin": ["opencode-supermemory", "mine"], "autoupdate": True}
    (oc / "opencode.json").write_text(json.dumps(user_cfg), encoding="utf-8")
    extra = oc / "keep-me.txt"
    extra.write_text("stay", encoding="utf-8")

    dest = mod.install(root, user_home=home)
    assert dest.read_bytes() == b"OLD-BINARY"
    assert extra.read_text(encoding="utf-8") == "stay"
    kept = json.loads((oc / "opencode.json").read_text(encoding="utf-8"))
    assert kept["plugin"] == ["opencode-supermemory", "mine"]
    assert kept["autoupdate"] is True
    assert (home / ".config" / "opencode" / "agents" / "review.md").is_file()
    assert (home / ".config" / "opencode" / "skills" / "cpp98" / "SKILL.md").is_file()
    assert (home / ".config" / "opencode" / "skills" / "modern-cpp" / "SKILL.md").is_file()


def test_existing_home_without_vendor_still_adds_agent(tmp_path: Path) -> None:
    mod = _load()
    root = _plant_root(tmp_path, vendor=False)
    home = tmp_path / "home"
    (home / ".opencode").mkdir(parents=True)
    (home / ".opencode" / "opencode.json").write_text('{"plugin":["keep"]}', encoding="utf-8")
    dest = mod.install(root, user_home=home)
    assert dest.is_file()
    assert "mode: primary" in dest.read_text(encoding="utf-8")
    assert json.loads((home / ".opencode" / "opencode.json").read_text(encoding="utf-8"))["plugin"] == ["keep"]


def test_missing_vendor_and_missing_home_fails(tmp_path: Path) -> None:
    mod = _load()
    root = _plant_root(tmp_path, vendor=False)
    home = tmp_path / "home"
    try:
        mod.install(root, user_home=home)
    except FileNotFoundError as exc:
        assert "vendor" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


def test_config_only_home_does_not_shadow_binary(tmp_path: Path) -> None:
    mod = _load()
    root = _plant_root(tmp_path, vendor=True)
    home = tmp_path / "home"
    (home / ".config" / "opencode").mkdir(parents=True)
    (home / ".config" / "opencode" / "opencode.json").write_text(
        '{"plugin":["keep"]}', encoding="utf-8"
    )
    dest = mod.install(root, user_home=home)
    assert dest.is_file()
    assert dest.name == "review.md"
    assert not (home / ".opencode").exists()
    assert (home / ".config" / "opencode" / "skills" / "cpp98" / "SKILL.md").is_file()
    assert not (home / ".opencode" / "skills").exists()
    kept = json.loads((home / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert kept["plugin"] == ["keep"]


def test_cpp_skills_state_when_to_load() -> None:
    cpp98 = (REPO / "opencode-configs" / "skills" / "cpp98" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    modern = (
        REPO / "opencode-configs" / "skills" / "modern-cpp" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert cpp98.startswith("---")
    assert modern.startswith("---")
    assert "name: cpp98" in cpp98
    assert "name: modern-cpp" in modern
    assert "Do not load for C++11" in cpp98
    assert "Do not load for C++98" in modern
    assert "Do not recommend" in cpp98
    assert "boost::scoped_ptr" in cpp98
    assert "add Boost as a new dependency" in cpp98
    assert "enable_shared_from_this" in cpp98
    assert "two-phase lookup" in cpp98
    assert "hard ceiling" in modern
    assert "string_view" in modern
    assert "std::jthread" in modern
    assert "enable_shared_from_this" in modern


def test_default_agent_is_review() -> None:
    from creasy.config import Config

    assert Config().opencode_agent == "review"


def test_install_copies_every_agent_and_skill(tmp_path: Path) -> None:
    mod = _load()
    root = _plant_root(tmp_path, vendor=True)
    extra = root / "opencode-configs" / "agents" / "planner.md"
    extra.write_text("---\nmode: primary\n---\nplanner\n", encoding="utf-8")
    (root / "opencode-configs" / "skills" / "extra").mkdir()
    (root / "opencode-configs" / "skills" / "extra" / "SKILL.md").write_text(
        "---\nname: extra\n---\n", encoding="utf-8"
    )
    home = tmp_path / "home"
    mod.install(root, user_home=home)
    assert (home / ".config" / "opencode" / "agents" / "planner.md").is_file()
    assert (home / ".config" / "opencode" / "skills" / "extra" / "SKILL.md").is_file()


def test_missing_configs_submodule_fails(tmp_path: Path) -> None:
    mod = _load()
    root = tmp_path / "pkg"
    root.mkdir()
    try:
        mod.list_agent_files(root)
    except FileNotFoundError as exc:
        assert "opencode-configs" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")
