"""Write the CHANGELOG section for one version to a file."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def extract(changelog: str, version: str) -> str:
    needle = f"## {version}"
    lines = changelog.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith(needle):
            start = index
            break
    if start is None:
        raise SystemExit(f"no {needle!r} section in CHANGELOG.md")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    body = "\n".join(lines[start:end]).strip()
    return body + "\n"


def downloads_body(version: str) -> str:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "packaging" / "exe_zips.py"
    spec = importlib.util.spec_from_file_location("creasy_exe_zips", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    zips_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(zips_mod)
    zips = [f"mireviewer-{version}-{suffix}.zip" for suffix in zips_mod.release_suffixes()]
    lines = [
        "Each zip is one MIReviewer executable, `.env.example`, `opencoderman/agents`, `opencoderman/skills`, and `install-review-agent` scripts.",
        "",
        "Linux: pick the zip that matches your Ubuntu. `linux-x64` is the Ubuntu 22.04 build.",
        "",
    ]
    lines.extend(f"- `{name}`" for name in zips)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--changelog", default=str(ROOT / "CHANGELOG.md"))
    args = parser.parse_args()
    Path(args.changelog).read_text(encoding="utf-8")
    Path(args.out).write_text(downloads_body(args.version), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
