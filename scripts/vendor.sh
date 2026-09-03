#!/usr/bin/env bash
# Online machine: fetch bundled CPython, OpenCode CLI, and wheels.
# Same as: python3 packaging/build_dist.py --in-place
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$HERE/pyproject.toml" ]]; then
  ROOT="$HERE"
elif [[ -f "$HERE/../pyproject.toml" ]]; then
  ROOT="$(cd "$HERE/.." && pwd)"
else
  echo "[ERROR] Cannot find repo root (pyproject.toml)."
  exit 1
fi
cd "$ROOT"

if command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
else
  echo "[ERROR] Need a network Python to run packaging/build_dist.py --in-place."
  exit 1
fi

echo "========================================"
echo "  Creasy - build_dist --in-place"
echo "========================================"
"$PY" "$ROOT/packaging/build_dist.py" --in-place

echo
echo "Then on this machine or after copying vendor/:"
echo "  scripts/install.sh"
echo "  scripts/install-opencode.sh"
echo "  scripts/start.sh"
echo
