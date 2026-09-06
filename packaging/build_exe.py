#!/usr/bin/env python3
"""Build one-file Creasy executables and zip each with its config.

  python packaging/build_exe.py --zip
  python packaging/build_exe.py --suffix windows-x64 --zip

Each zip contains only the executable and `.env.example`.
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


def write_exe_zip(exe: Path, config: Path, dest_zip: Path) -> None:
    """Zip exactly one executable and one config file."""
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    if dest_zip.exists():
        dest_zip.unlink()
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(exe, arcname=exe.name)
        zf.write(config, arcname=config.name)


def assert_exe_zip(dest_zip: Path, *, expect_exe: str) -> None:
    with zipfile.ZipFile(dest_zip) as zf:
        names = sorted(zf.namelist())
    if names != sorted([expect_exe, ".env.example"]):
        raise SystemExit(f"{dest_zip} must contain only {expect_exe} and .env.example; got {names}")


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
        write_exe_zip(staged_exe, config, dest)
        assert_exe_zip(dest, expect_exe=exe.name)
        print(f"Wrote {dest}")
    else:
        shutil.copy2(config, out / ".env.example")
        print(f"Wrote {staged_exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
