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
    return "mireviewer.exe" if sys.platform.startswith("win") else "mireviewer"


def zip_name(version: str, suffix: str) -> str:
    return f"mireviewer-{version}-{suffix}.zip"


REVIEW_AGENT_SCRIPTS = (
    "install-review-agent.bat",
    "install-review-agent.sh",
)


def opencoderman_zip_entries(opencoderman: Path) -> list[tuple[Path, str]]:
    """Only agents/*.md and skills/*/SKILL.md. Never .git or the rest."""
    src = Path(opencoderman)
    agent = src / "agents" / "code-reviewer.md"
    if not agent.is_file():
        raise SystemExit(
            "opencoderman/agents/code-reviewer.md missing. "
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
        "opencoderman/agents/code-reviewer.md",
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


def write_pyinstaller_spec(root: Path, spec_path: Path) -> None:
    """Onefile spec. Linux omits bundled libz and unpacks next to the binary.

    Bundled ``libz.so.1`` from ubuntu-latest is what produced
    ``failed to map segment from shared object`` on other boxes.
    The OS always has zlib. ``runtime_tmpdir='.'`` keeps remaining
    .so files off a noexec ``/tmp``.
    """
    root = Path(root).resolve()
    spec_path = Path(spec_path)
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    linux = sys.platform.startswith("linux")
    portable = linux and os.environ.get("CREASY_LINUX_PORTABLE", "").strip() in {"1", "true", "yes"}
    spec_path.write_text(
        "\n".join(
            [
                "# Generated by packaging/build_exe.py. Do not edit.",
                "# -*- mode: python ; coding: utf-8 -*-",
                "from pathlib import Path",
                "",
                f"ROOT = Path({str(root)!r})",
                "datas = [(str(ROOT / 'VERSION'), '.'), (str(ROOT / 'web' / 'dist'), 'web/dist')]",
                "binaries = []",
                "hidden = ['mireviewer', 'mireviewer.app']",
                "from PyInstaller.utils.hooks import collect_all",
                "for _pkg in ('uvicorn', 'fastapi', 'starlette'):",
                "    _d, _b, _h = collect_all(_pkg)",
                "    datas += _d",
                "    binaries += _b",
                "    hidden += _h",
                "",
                "a = Analysis(",
                "    [str(ROOT / 'src' / 'mireviewer' / '__main__.py')]",
                "    , pathex=[str(ROOT / 'src')]",
                "    , binaries=binaries",
                "    , datas=datas",
                "    , hiddenimports=hidden",
                "    , hookspath=[]",
                "    , hooksconfig={}",
                "    , runtime_hooks=[]",
                "    , excludes=[]",
                "    , noarchive=False",
                ")",
                "",
                f"LINUX = {linux!r}",
                f"PORTABLE = {portable!r}",
                "if LINUX:",
                "    _drop_pref = ('libz.so.', 'libssl.so.', 'libcrypto.so.', 'libffi.so.',",
                "                  'libbz2.so.', 'liblzma.so.', 'libtinfo.so.', 'libreadline.so.',",
                "                  'libncurses.so.', 'libncursesw.so.', 'libsqlite3.so.',",
                "                  'libnsl.so.', 'libuuid.so.', 'libexpat.so.')",
                "    def _keep(item):",
                "        name = str(item[0] if item else '')",
                "        base = name.replace('\\\\', '/').rsplit('/', 1)[-1]",
                "        if base == 'libz.so.1' or base.startswith('libz.so.'):",
                "            return False",
                "        if PORTABLE and any(base.startswith(p) for p in _drop_pref):",
                "            return False",
                "        return True",
                "    a.binaries = [item for item in a.binaries if _keep(item)]",
                "",
                "pyz = PYZ(a.pure)",
                "exe = EXE(",
                "    pyz,",
                "    a.scripts,",
                "    a.binaries,",
                "    a.datas,",
                "    [],",
                "    name='mireviewer',",
                "    debug=False,",
                "    bootloader_ignore_signals=False,",
                "    strip=False,",
                "    upx=False,",
                "    upx_exclude=[],",
                f"    runtime_tmpdir={'.' if linux else None!r},",
                "    console=True,",
                "    disable_windowed_traceback=False,",
                "    argv_emulation=False,",
                "    target_arch=None,",
                "    codesign_identity=None,",
                "    entitlements_file=None,",
                ")",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )


def run_pyinstaller(root: Path, work: Path) -> Path:
    dist = work / "dist"
    build = work / "build"
    spec = work / "creasy.spec"
    write_pyinstaller_spec(root, spec)
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist),
        "--workpath",
        str(build),
        str(spec),
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
