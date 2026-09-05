"""OpenCode installer backs up ~/.opencode and installs a clean home."""

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
    shutil.copy2(REPO / "opencode-configs" / "install.py", root / "opencode-configs" / "install.py")
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
    text = (REPO / "opencode-configs" / "agents" / "gitlab-reviewer.md").read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "mode: primary" in text
    assert "edit: deny" in text
    assert "task: deny" in text
    assert "never edit" in text.lower() or "You never edit" in text
    assert "merge-base" in text
    assert "agent/rules/CODE_REVIEW.md" in text
    assert ".creasy/CODE_REVIEW.md" not in text
    assert "Before you review, read project rules" in text
    assert "detect the C++ dialect" in text
    assert "CMAKE_CXX_STANDARD" in text
    assert "do not assume modern C++" in text
    assert 'skill({ name: "cpp98" })' in text
    assert 'skill({ name: "modern-cpp" })' in text
    assert 'skill({ name: "cpp-memory-safety" })' in text or "cpp-memory-safety" in text
    assert "typically 0–3" in text or "typically 0-3" in text
    assert "git-commits" in text
    assert "implementer-only" in text
    assert "GitLab MR comment" in text
    assert "Never start a line with `#`" in text
    assert "opencoderman-findings" in text
    assert '"findings"' in text
    assert "### Summary" in text
    assert "### Critical" in text
    assert "### Major" in text
    assert "### Minor" in text
    assert "### Improvement" in text
    assert "#### 1." in text
    assert "**Code**" in text
    assert "**Why it is an issue and where**" in text
    assert "**Suggested fix**" in text
    assert "Do **not** use these labels: Blocking" in text
    assert "only definition of review style" in text
    assert "Write each group header **once**" in text
    assert "Impact analysis (mandatory)" in text
    assert "git grep" in text
    assert "Negative space" in text
    assert "new contract" in text
    assert '"git grep*"' in text


def test_fresh_install_writes_binary_config_and_agent(tmp_path: Path) -> None:
    mod = _load()
    root = _plant_root(tmp_path, vendor=True)
    home = tmp_path / "home"
    dest = mod.install(root, user_home=home)
    assert dest.is_file()
    assert dest == home / ".opencode" / "bin" / ("opencode.exe" if os.name == "nt" else "opencode")
    cfg = json.loads((home / ".opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert cfg.get("plugin") == []
    agent = (home / ".opencode" / "agents" / "gitlab-reviewer.md").read_text(encoding="utf-8")
    assert "mode: primary" in agent
    assert not (home / ".config" / "opencode").exists()
    for name in (
        "cpp98",
        "modern-cpp",
        "cpp-memory-safety",
        "cmake-cpp",
        "secrets",
        "python",
        "web-security",
    ):
        skill = home / ".opencode" / "skills" / name / "SKILL.md"
        assert skill.is_file()
        body = skill.read_text(encoding="utf-8")
        assert f"name: {name}" in body


def test_existing_install_is_backed_up_and_replaced(tmp_path: Path) -> None:
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
    assert dest.read_bytes() == b"NEW"
    assert not extra.exists()
    backups = list(home.glob(".opencode_backup_*"))
    assert len(backups) == 1
    assert (backups[0] / "keep-me.txt").read_text(encoding="utf-8") == "stay"
    assert json.loads((backups[0] / "opencode.json").read_text(encoding="utf-8"))["plugin"] == [
        "opencode-supermemory",
        "mine",
    ]
    fresh = json.loads((oc / "opencode.json").read_text(encoding="utf-8"))
    assert fresh.get("plugin") == []
    assert (home / ".opencode" / "agents" / "gitlab-reviewer.md").is_file()
    assert (home / ".opencode" / "skills" / "cpp98" / "SKILL.md").is_file()
    assert not (home / ".config" / "opencode").exists()


def test_existing_home_without_vendor_copies_binary_from_backup(tmp_path: Path) -> None:
    mod = _load()
    root = _plant_root(tmp_path, vendor=False)
    home = tmp_path / "home"
    oc = home / ".opencode"
    (oc / "bin").mkdir(parents=True)
    old = oc / "bin" / ("opencode.exe" if os.name == "nt" else "opencode")
    old.write_bytes(b"OLD-BINARY")
    (oc / "opencode.json").write_text('{"plugin":["keep"]}', encoding="utf-8")
    dest = mod.install(root, user_home=home)
    assert dest.is_file()
    assert dest.read_bytes() == b"OLD-BINARY"
    assert (home / ".opencode" / "agents" / "gitlab-reviewer.md").is_file()
    assert not (home / ".config" / "opencode").exists()
    assert json.loads((oc / "opencode.json").read_text(encoding="utf-8")).get("plugin") == []


def test_pack_vendor_used_when_creasy_vendor_missing(tmp_path: Path) -> None:
    import platform
    import sys

    mod = _load()
    root = _plant_root(tmp_path, vendor=False)
    bin_name = "opencode.exe" if os.name == "nt" else "opencode"
    if os.name == "nt":
        tag = "windows"
    elif sys.platform == "darwin":
        machine = platform.machine().lower()
        tag = "darwin-arm64" if machine in {"arm64", "aarch64"} else "darwin-x64"
    else:
        tag = "linux"
    path = root / "opencode-configs" / "vendor" / "bin" / tag / bin_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PACK")
    dest = mod.install(root, user_home=tmp_path / "home")
    assert dest.is_file()
    assert dest.read_bytes() == b"PACK"


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


def test_config_only_home_is_backed_up_and_new_home_created(tmp_path: Path) -> None:
    mod = _load()
    root = _plant_root(tmp_path, vendor=True)
    home = tmp_path / "home"
    (home / ".config" / "opencode").mkdir(parents=True)
    (home / ".config" / "opencode" / "opencode.json").write_text(
        '{"plugin":["keep"]}', encoding="utf-8"
    )
    dest = mod.install(root, user_home=home)
    assert dest.is_file()
    assert dest == home / ".opencode" / "bin" / ("opencode.exe" if os.name == "nt" else "opencode")
    assert dest.read_bytes() == b"NEW"
    assert (home / ".opencode" / "agents" / "gitlab-reviewer.md").is_file()
    backups = list((home / ".config").glob("opencode_backup_*"))
    assert len(backups) == 1
    assert json.loads((backups[0] / "opencode.json").read_text(encoding="utf-8"))["plugin"] == ["keep"]
    assert not (home / ".config" / "opencode").exists()


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


def test_extra_skills_state_when_to_load() -> None:
    root = REPO / "opencode-configs" / "skills"
    names = sorted(p.name for p in root.iterdir() if (p / "SKILL.md").is_file())
    assert len(names) >= 40
    for name in names:
        body = (root / name / "SKILL.md").read_text(encoding="utf-8")
        assert body.startswith("---"), name
        assert f"name: {name}" in body, name
        assert "Load when" in body or "Load only" in body, name


def test_default_agent_is_gitlab_reviewer() -> None:
    from creasy.config import Config

    assert Config().opencode_agent == "gitlab-reviewer"


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
    assert (home / ".opencode" / "agents" / "planner.md").is_file()
    assert (home / ".opencode" / "skills" / "extra" / "SKILL.md").is_file()
    assert not (home / ".config" / "opencode").exists()


def test_install_unhooks_other_bin_and_prepends_new(tmp_path: Path) -> None:
    mod = _load()
    root = _plant_root(tmp_path, vendor=True)
    home = tmp_path / "home"
    custom = tmp_path / "apps" / "opencode" / "bin"
    custom.mkdir(parents=True)
    (custom / ("opencode.exe" if os.name == "nt" else "opencode")).write_text("old", encoding="utf-8")
    home.mkdir(parents=True)
    (home / ".opencode-path").write_text(
        str(custom) + (";" if os.name == "nt" else ":") + str(tmp_path / "keep"),
        encoding="utf-8",
    )
    dest = mod.install(root, user_home=home)
    assert dest.read_bytes() == b"NEW"
    assert (custom / ("opencode.exe" if os.name == "nt" else "opencode")).is_file()
    raw = (home / ".opencode-path").read_text(encoding="utf-8")
    assert str(home / ".opencode" / "bin") in raw
    assert str(custom) not in raw.split(";" if os.name == "nt" else ":")


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
