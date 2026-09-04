#!/usr/bin/env python3
"""Install the OpenCode CLI and this repo's agents/skills (offline, stdlib).

Uses the same replace rules as opencode-configs/install.py: backup
~/.opencode, unhook other installs from PATH, write a clean home, then
copy the vendored binary (or the binary from the backup if vendor is
missing) and prepend ~/.opencode/bin.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import sys
from pathlib import Path

STOCK_CONFIG = """{
  "$schema": "https://opencode.ai/config.json",
  "autoupdate": false,
  "plugin": []
}
"""

CONFIGS_REL = Path("opencode-configs")
REVIEW_AGENT_REL = CONFIGS_REL / "agents" / "gitlab-reviewer.md"
REVIEW_SKILLS_REL = CONFIGS_REL / "skills"


def home() -> Path:
    # Windows: always %USERPROFILE% so we land in <user>\.opencode, not AppData.
    if os.name == "nt":
        profile = os.environ.get("USERPROFILE")
        if profile:
            return Path(profile)
    return Path.home()


def opencode_home(user_home: Path | None = None) -> Path:
    return (user_home or home()) / ".opencode"


def config_home(user_home: Path | None = None) -> Path:
    return (user_home or home()) / ".config" / "opencode"


def dest_dir(user_home: Path | None = None) -> Path:
    return opencode_home(user_home) / "bin"


def dest_binary(user_home: Path | None = None) -> Path:
    name = "opencode.exe" if os.name == "nt" else "opencode"
    return dest_dir(user_home) / name


def configs_root(root: Path) -> Path:
    return Path(root) / CONFIGS_REL


def review_agent_source(root: Path) -> Path:
    return Path(root) / REVIEW_AGENT_REL


def review_skills_source(root: Path) -> Path:
    return Path(root) / REVIEW_SKILLS_REL


def list_agent_files(root: Path) -> list[Path]:
    folder = configs_root(root) / "agents"
    if not folder.is_dir():
        raise FileNotFoundError(
            f"OpenCode configs missing: {folder} (git submodule update --init)"
        )
    files = sorted(path for path in folder.glob("*.md") if path.is_file())
    if not files:
        raise FileNotFoundError(f"No agent markdown under {folder}")
    return files


def list_skill_dirs(root: Path) -> list[Path]:
    folder = review_skills_source(root)
    if not folder.is_dir():
        raise FileNotFoundError(
            f"OpenCode configs missing: {folder} (git submodule update --init)"
        )
    dirs = sorted(
        path for path in folder.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()
    )
    if not dirs:
        raise FileNotFoundError(f"No skills with SKILL.md under {folder}")
    return dirs


def agent_dests(
    name: str,
    user_home: Path | None = None,
    *,
    include_opencode_home: bool = True,
) -> list[Path]:
    base = user_home or home()
    dests = [config_home(base) / "agents" / f"{name}.md"]
    if include_opencode_home:
        dests.append(opencode_home(base) / "agents" / f"{name}.md")
    return dests


def review_agent_dests(user_home: Path | None = None, *, include_opencode_home: bool = True) -> list[Path]:
    return agent_dests("gitlab-reviewer", user_home, include_opencode_home=include_opencode_home)


def vendor_binary(root: Path) -> Path | None:
    vendor_bin = Path(root) / "vendor" / "bin"
    if os.name == "nt":
        candidates = (vendor_bin / "windows" / "opencode.exe", vendor_bin / "opencode.exe")
    elif sys.platform == "darwin":
        machine = platform.machine().lower()
        tag = "darwin-arm64" if machine in {"arm64", "aarch64"} else "darwin-x64"
        candidates = (vendor_bin / tag / "opencode", vendor_bin / "opencode")
    else:
        candidates = (vendor_bin / "linux" / "opencode", vendor_bin / "opencode")
    for path in candidates:
        if path.is_file():
            return path
    return None


def existing_install(user_home: Path | None = None) -> list[Path]:
    found: list[Path] = []
    for path in (opencode_home(user_home), config_home(user_home)):
        try:
            if path.exists():
                found.append(path)
        except OSError:
            continue
    return found


def ensure_binary(src: Path | None, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file():
        print(f"[OK] Binary kept: {dest}")
        return "kept"
    if src is None:
        raise FileNotFoundError(f"No OpenCode binary to install at {dest}")
    shutil.copy2(src, dest)
    if os.name != "nt":
        dest.chmod(dest.stat().st_mode | 0o111)
    print(f"[OK] Binary installed: {dest}")
    return "installed"


def ensure_config(path: Path) -> str:
    if path.is_file():
        _keep_existing_config(path)
        print(f"[OK] Config kept: {path}")
        return "kept"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STOCK_CONFIG, encoding="utf-8")
    print(f"[OK] Config created: {path}")
    return "created"


def _keep_existing_config(path: Path) -> None:
    """Leave user plugins and keys in place. Invalid JSON is not rewritten."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return
    try:
        json.loads(raw)
    except json.JSONDecodeError:
        print(f"[OK] Config left untouched (not plain JSON): {path}")


def install_review_agent(
    root: Path,
    user_home: Path | None = None,
    *,
    include_opencode_home: bool = True,
) -> list[Path]:
    written: list[Path] = []
    for src in list_agent_files(root):
        text = src.read_text(encoding="utf-8")
        for dest in agent_dests(src.stem, user_home, include_opencode_home=include_opencode_home):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            written.append(dest)
            print(f"[OK] Agent {src.stem} written: {dest}")
    return written


def review_skill_dests(
    name: str,
    user_home: Path | None = None,
    *,
    include_opencode_home: bool = True,
) -> list[Path]:
    base = user_home or home()
    dests = [config_home(base) / "skills" / name / "SKILL.md"]
    if include_opencode_home:
        dests.append(opencode_home(base) / "skills" / name / "SKILL.md")
    return dests


def install_review_skills(
    root: Path,
    user_home: Path | None = None,
    *,
    include_opencode_home: bool = True,
) -> list[Path]:
    written: list[Path] = []
    for skill_dir in list_skill_dirs(root):
        src = skill_dir / "SKILL.md"
        text = src.read_text(encoding="utf-8")
        for dest in review_skill_dests(
            skill_dir.name, user_home, include_opencode_home=include_opencode_home
        ):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            written.append(dest)
            print(f"[OK] Skill {skill_dir.name} written: {dest}")
    return written


def load_pack_installer(root: Path):
    path = configs_root(root) / "install.py"
    if not path.is_file():
        raise FileNotFoundError(
            f"OpenCode configs installer missing: {path} (git submodule update --init)"
        )
    spec = importlib.util.spec_from_file_location("opencode_configs_install", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def latest_backup_binary(user_home: Path | None = None) -> Path | None:
    base = user_home or home()
    name = "opencode.exe" if os.name == "nt" else "opencode"
    backups = sorted(base.glob(".opencode_backup_*"), reverse=True)
    for backup in backups:
        candidate = backup / "bin" / name
        if candidate.is_file():
            return candidate
    return None


def prepend_user_path(directory: Path) -> None:
    """Windows-only leftover helper. New installs use opencode-configs PATH logic."""
    if os.name != "nt":
        return
    import winreg

    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE
    )
    try:
        try:
            raw, _typ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            raw = ""
        parts = [p for p in str(raw).split(";") if p]
        norm = str(directory).rstrip("\\")
        parts = [p for p in parts if p.rstrip("\\").lower() != norm.lower()]
        new = norm + (";" + ";".join(parts) if parts else "")
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new)
        print(f"[OK] Prepended to user PATH: {norm}")
    finally:
        key.Close()


def install(root: Path, *, user_home: Path | None = None) -> Path:
    root = Path(root).expanduser().resolve()
    base = user_home or home()
    pack = load_pack_installer(root)
    pack.install(configs_root(root), user_home=base)
    dest = dest_binary(base)
    src = vendor_binary(root) or latest_backup_binary(base)
    if src is None:
        raise FileNotFoundError(f"No OpenCode binary under {root / 'vendor' / 'bin'}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    if os.name != "nt":
        dest.chmod(dest.stat().st_mode | 0o111)
    print(f"[OK] Binary installed: {dest}")
    print(f"Install root: {opencode_home(base)}")
    return dest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Backup ~/.opencode, unhook other installs from PATH, then install the vendored CLI and agents/skills."
    )
    p.add_argument("--root", required=True, help="Repo / zip root that contains vendor/bin")
    args = p.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    try:
        target = install(root)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        print("Use a CI zip from packaging/build_dist.py, or git submodule update --init.", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    print(f"[OK] OpenCode ready: {target}")
    print("Jobs use OPENCODE_AGENT=gitlab-reviewer (see .env.example).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
