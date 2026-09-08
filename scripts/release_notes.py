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
    zips = [
        f"creasy-{version}-windows-x64.zip",
        f"creasy-{version}-linux-x64.zip",
        f"creasy-{version}-darwin-arm64.zip",
    ]
    lines = [
        "Each zip is one Creasy executable, `.env.example`, `opencoderman/agents`, `opencoderman/skills`, and `install-review-agent` scripts.",
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
