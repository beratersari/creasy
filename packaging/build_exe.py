#!/usr/bin/env python3
"""Build one-file Creasy executables and zip each with its config.

  python packaging/build_exe.py --zip
  python packaging/build_exe.py --suffix windows-x64 --zip

Each zip contains the executable, `.env.example`, install-review-agent
scripts, and only `opencoderman/agents` plus `opencoderman/skills`.
No `.git`, vendor, tests, README, or installers from that submodule.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def product_version(root: Path = ROOT) -> str:
    vf = root / "VERSION"
    if vf.is_file():
        return vf.read_text(encoding="utf-8").strip()
    return "0.0.0-dev"


def host_suffix() -> str:
    system = sys.platform
    machine = platform.machine().lower()
    if system.startswith("win"):
        os_name = "windows"
    elif system.startswith("linux"):
        os_name = "linux"
    elif system == "darwin":
        os_name = "darwin"
    else:
        raise SystemExit(f"unsupported platform: {system}")
    if machine in {"x86_64", "amd64"}:
        arch = "x64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise SystemExit(f"unsupported architecture: {machine}")
    return f"{os_name}-{arch}"


def exe_name() -> str:
    return "creasy.exe" if sys.platform.startswith("win") else "creasy"


def zip_name(version: str, suffix: str) -> str:
    return f"creasy-{version}-{suffix}.zip"


REVIEW_AGENT_SCRIPTS = (
    "install-review-agent.bat",
    "install-review-agent.sh",
)


def opencoderman_zip_entries(opencoderman: Path) -> list[tuple[Path, str]]:
    """Only agents/*.md and skills/*/SKILL.md. Never .git or the rest."""
    src = Path(opencoderman)
    agent = src / "agents" / "gitlab-reviewer.md"
    if not agent.is_file():
        raise SystemExit(
            "opencoderman/agents/gitlab-reviewer.md missing. "
            "git submodule update --init --recursive"
        )
    skills = src / "skills"
    if not skills.is_dir():
        raise SystemExit("opencoderman/skills missing")
    entries: list[tuple[Path, str]] = []
    for path in sorted(p for p in (src / "agents").glob("*.md") if p.is_file()):
        entries.append((path, f"opencoderman/agents/{path.name}"))
    skill_count = 0
    for skill_dir in sorted(p for p in skills.iterdir() if p.is_dir()):
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.is_file():
            continue
        entries.append((skill_md, f"opencoderman/skills/{skill_dir.name}/SKILL.md"))
        skill_count += 1
    if skill_count == 0:
        raise SystemExit("no skills with SKILL.md under opencoderman/skills")
    return entries


def review_agent_script_entries(scripts_dir: Path) -> list[tuple[Path, str]]:
    entries: list[tuple[Path, str]] = []
    for name in REVIEW_AGENT_SCRIPTS:
        path = Path(scripts_dir) / name
        if not path.is_file():
            raise SystemExit(f"missing {path}")
        entries.append((path, name))
    return entries


def _allowed_exe_zip_name(name: str, *, expect_exe: str) -> bool:
    if name in {expect_exe, ".env.example", *REVIEW_AGENT_SCRIPTS}:
        return True
    if name.startswith("opencoderman/agents/") and name.endswith(".md") and name.count("/") == 2:
        return True
    if name.startswith("opencoderman/skills/") and name.endswith("/SKILL.md") and name.count("/") == 3:
        return True
    return False


def write_exe_zip(exe: Path, config: Path, dest_zip: Path, *, root: Path) -> None:
    """Zip the executable, config, review-agent scripts, and opencoderman pack."""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    if dest_zip.exists():
        dest_zip.unlink()
    entries = [
        (exe, exe.name),
        (config, config.name),
        *review_agent_script_entries(Path(root) / "scripts"),
        *opencoderman_zip_entries(Path(root) / "opencoderman"),
    ]
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, arcname in entries:
            zf.write(path, arcname=arcname)


def assert_exe_zip(dest_zip: Path, *, expect_exe: str) -> None:
    with zipfile.ZipFile(dest_zip) as zf:
        names = zf.namelist()
    required = {
        expect_exe,
        ".env.example",
        *REVIEW_AGENT_SCRIPTS,
        "opencoderman/agents/gitlab-reviewer.md",
    }
    missing = sorted(required.difference(names))
    if missing:
        raise SystemExit(f"{dest_zip} missing {missing}; got {sorted(names)}")
    extras = sorted(name for name in names if not _allowed_exe_zip_name(name, expect_exe=expect_exe))
    if extras:
        raise SystemExit(f"{dest_zip} has unexpected entries {extras}")
    if not any(name.startswith("opencoderman/skills/") and name.endswith("/SKILL.md") for name in names):
        raise SystemExit(f"{dest_zip} has no opencoderman skills")
    if any(part == ".git" for name in names for part in name.replace("\\", "/").split("/")):
        raise SystemExit(f"{dest_zip} must not include opencoderman/.git")


def _sep() -> str:
    return ";" if os.name == "nt" else ":"


def build_web(root: Path) -> None:
    index = root / "web" / "dist" / "index.html"
    if index.is_file():
        return
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("web/dist is missing and npm is not on PATH")
    subprocess.run([npm, "--prefix", str(root / "web"), "ci"], cwd=root, check=True)
    subprocess.run([npm, "--prefix", str(root / "web"), "run", "build"], cwd=root, check=True)
    if not index.is_file():
        raise SystemExit("web/dist/index.html missing after npm run build")


def run_pyinstaller(root: Path, work: Path) -> Path:
    pyinstaller = shutil.which("pyinstaller")
    cmd = [sys.executable, "-m", "PyInstaller"]
    if pyinstaller:
        cmd = [pyinstaller]
    dist = work / "dist"
    build = work / "build"
    spec = work / "creasy.spec"
    sep = _sep()
    args = [
        *cmd,
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "creasy",
        "--console",
        "--distpath",
        str(dist),
        "--workpath",
        str(build),
        "--specpath",
        str(spec.parent),
        "--collect-all",
        "uvicorn",
        "--collect-all",
        "fastapi",
        "--collect-all",
        "starlette",
        "--hidden-import",
        "creasy",
        "--hidden-import",
        "creasy.app",
        f"--add-data={root / 'VERSION'}{sep}.",
        f"--add-data={root / 'web' / 'dist'}{sep}web/dist",
        str(root / "src" / "creasy" / "__main__.py"),
    ]
    print("  +", " ".join(args))
    subprocess.run(args, cwd=root, check=True)
    built = dist / exe_name()
    if not built.is_file():
        raise SystemExit(f"pyinstaller did not write {built}")
    return built


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--suffix", default="")
    parser.add_argument("--zip", action="store_true")
    parser.add_argument("--skip-web", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    suffix = args.suffix.strip() or host_suffix()
    version = product_version(root)
    out = (args.out_dir or (root / "dist" / "exe")).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not args.skip_web:
        build_web(root)
    work = out / f"work-{suffix}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    exe = run_pyinstaller(root, work)
    staged_exe = out / exe.name
    shutil.copy2(exe, staged_exe)
    config = root / ".env.example"
    if not config.is_file():
        raise SystemExit("missing .env.example")
    if args.zip:
        dest = out / zip_name(version, suffix)
        write_exe_zip(staged_exe, config, dest, root=root)
        assert_exe_zip(dest, expect_exe=exe.name)
        print(f"Wrote {dest}")
    else:
        shutil.copy2(config, out / ".env.example")
        print(f"Wrote {staged_exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
