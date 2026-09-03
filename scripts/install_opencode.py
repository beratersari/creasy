#!/usr/bin/env python3
"""Install or extend the user OpenCode home (offline, stdlib).

Never deletes an existing OpenCode install. A first run copies the vendored
CLI and a stock config. A later run only adds the Creasy review agent,
C++ review skills, and a missing binary or config.
"""

from __future__ import annotations

import argparse
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

REVIEW_AGENT_REL = Path("scripts") / "opencode" / "review.md"
REVIEW_SKILLS_REL = Path("scripts") / "opencode" / "skills"
REVIEW_SKILL_NAMES = ("cpp98", "modern-cpp")


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


def review_agent_source(root: Path) -> Path:
    return Path(root) / REVIEW_AGENT_REL


def review_skills_source(root: Path) -> Path:
    return Path(root) / REVIEW_SKILLS_REL


def review_agent_dests(user_home: Path | None = None, *, include_opencode_home: bool = True) -> list[Path]:
    base = user_home or home()
    dests = [config_home(base) / "agents" / "review.md"]
    if include_opencode_home:
        dests.append(opencode_home(base) / "agents" / "review.md")
    return dests


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
    src = review_agent_source(root)
    if not src.is_file():
        raise FileNotFoundError(f"Review agent missing: {src}")
    text = src.read_text(encoding="utf-8")
    written: list[Path] = []
    for dest in review_agent_dests(user_home, include_opencode_home=include_opencode_home):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        written.append(dest)
        print(f"[OK] Review agent written: {dest}")
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
    src_root = review_skills_source(root)
    written: list[Path] = []
    for name in REVIEW_SKILL_NAMES:
        src = src_root / name / "SKILL.md"
        if not src.is_file():
            raise FileNotFoundError(f"Review skill missing: {src}")
        text = src.read_text(encoding="utf-8")
        for dest in review_skill_dests(
            name, user_home, include_opencode_home=include_opencode_home
        ):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            written.append(dest)
            print(f"[OK] Review skill written: {dest}")
    return written


def prepend_user_path(directory: Path) -> None:
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
    oc_home = opencode_home(base)
    cfg_home = config_home(base)
    dest = dest_binary(base)
    src = vendor_binary(root)
    found = existing_install(base)

    if found:
        print("Existing OpenCode install kept:")
        for path in found:
            print(f"  {path}")
    else:
        print("No previous OpenCode home found; installing a new one.")

    oc_exists = oc_home.exists()
    own_home = dest.is_file() or (src is not None and (oc_exists or not found))
    if own_home:
        ensure_binary(src, dest)
        if user_home is None:
            prepend_user_path(dest.parent)
        ensure_config(oc_home / "opencode.json")
    elif not found:
        raise FileNotFoundError(f"No OpenCode binary under {root / 'vendor' / 'bin'}")
    else:
        print("[OK] Existing OpenCode config kept; adding the review agent and skills only")

    if (cfg_home / "opencode.json").is_file():
        ensure_config(cfg_home / "opencode.json")
    include_home = own_home or oc_exists
    install_review_agent(root, base, include_opencode_home=include_home)
    install_review_skills(root, base, include_opencode_home=include_home)
    print(f"Install root: {oc_home if own_home or oc_exists else cfg_home}")
    return dest if dest.is_file() else review_agent_dests(base, include_opencode_home=False)[0]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Install the vendored OpenCode CLI if needed; always add the Creasy review agent and C++ skills. Never deletes an existing install."
    )
    p.add_argument("--root", required=True, help="Repo / zip root that contains vendor/bin")
    args = p.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    try:
        target = install(root)
    except FileNotFoundError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        print("Use a CI zip from packaging/build_dist.py, or keep an existing OpenCode home.", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    print(f"[OK] OpenCode ready: {target}")
    print("Jobs use OPENCODE_AGENT=review (see .env.example).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
