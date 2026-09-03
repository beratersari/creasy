"""Build a zip an air-gapped host can unpack and install with no network."""

from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INCLUDE_ROOT_FILES = (
    "pyproject.toml",
    "README.md",
    "AGENTS.md",
    "plan.md",
    ".env.example",
    ".gitignore",
    "install.bat",
    "install.sh",
    "start.bat",
    "start.sh",
    "vendor.bat",
    "vendor.sh",
)

INCLUDE_DIRS = ("src/creasy", "scripts", "web", "tests")
SKIP_DIR_NAMES = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "data", "logs", ".tmp-refs"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def _add_tree(zf: zipfile.ZipFile, src: Path, arc_prefix: Path) -> int:
    count = 0
    for path in src.rglob("*"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.is_dir():
            continue
        if path.suffix in SKIP_SUFFIXES:
            continue
        rel = path.relative_to(src)
        zf.write(path, arc_prefix / rel)
        count += 1
    return count


def pack(*, tag: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    wheels = ROOT / "vendor" / "python-wheels"
    if not wheels.is_dir() or not any(wheels.glob("*.whl")):
        raise SystemExit("vendor/python-wheels is empty. Run scripts/vendor.sh first.")

    prefix = Path(f"creasy-offline-{tag}")
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in INCLUDE_ROOT_FILES:
            path = ROOT / name
            if path.is_file():
                zf.write(path, prefix / name)
        for rel in INCLUDE_DIRS:
            src = ROOT / rel
            if src.is_dir():
                _add_tree(zf, src, prefix / rel)
        n = _add_tree(zf, wheels, prefix / "vendor" / "python-wheels")
        if n == 0:
            raise SystemExit("no wheels packed")
        readme = (
            f"Creasy offline bundle ({tag})\n\n"
            "1. Unzip this archive on the target host.\n"
            "2. Run install.bat (Windows) or ./install.sh (Linux).\n"
            "3. Edit .env (copied from .env.example).\n"
            "4. Run start.bat or ./start.sh.\n"
            "OpenCode is not included. Put the CLI on PATH or in vendor/bin.\n"
        )
        zf.writestr(str(prefix / "OFFLINE.txt"), readme)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default=os.getenv("CREASY_OFFLINE_TAG", "local"))
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    out = Path(args.out) if args.out else ROOT / "dist" / f"creasy-offline-{args.tag}.zip"
    packed = pack(tag=args.tag, dest=out)
    print(f"[OK] wrote {packed} ({packed.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
