"""Bump the product version in VERSION (the single source of truth)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from creasy.version import bump_version, parse_version, read_version, version_path, write_version


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump or set the Creasy VERSION file")
    parser.add_argument(
        "part",
        nargs="?",
        choices=("major", "minor", "patch"),
        help="which semver component to increment",
    )
    parser.add_argument("--set", dest="explicit", default="", help="set an exact X.Y.Z version")
    args = parser.parse_args()
    path = version_path() or (ROOT / "VERSION")
    current = read_version()
    if args.explicit:
        parse_version(args.explicit)
        nxt = write_version(path, args.explicit)
    elif args.part:
        nxt = write_version(path, bump_version(current, args.part))
    else:
        parser.error("pass major|minor|patch or --set X.Y.Z")
        return 2
    print(f"{current} -> {nxt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
