#!/usr/bin/env bash
# Download Python wheels into vendor/python-wheels (needs network).
# Copy vendor/ + the repo to an air-gapped machine, then run install.sh.
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
WHEELS="$ROOT/vendor/python-wheels"

PY=""
if [[ -x "$ROOT/vendor/python/linux/bin/python3" ]]; then
  PY="$ROOT/vendor/python/linux/bin/python3"
elif [[ -x "$ROOT/vendor/python/macos/bin/python3" ]]; then
  PY="$ROOT/vendor/python/macos/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
else
  echo "[ERROR] No Python on PATH. Install Python 3.10+ or place it under vendor/python/."
  exit 1
fi

echo "========================================"
echo "  Creasy - vendor wheels (online)"
echo "========================================"
echo "Project : $ROOT"
echo "Python  : $PY"
echo "Wheels  : $WHEELS"
echo

mkdir -p "$WHEELS"

echo "Downloading pip / setuptools / wheel..."
"$PY" -m pip download -d "$WHEELS" pip setuptools wheel

echo "Downloading Creasy runtime dependencies..."
"$PY" -m pip download -d "$WHEELS" \
  "fastapi>=0.115" \
  "uvicorn[standard]>=0.32" \
  "httpx>=0.27" \
  "pydantic>=2.0" \
  "python-dotenv>=1.0"

echo
echo "[OK] Wheels are in vendor/python-wheels"
echo "Copy this repo (including vendor/) to the offline host, then:"
echo "  scripts/install.sh"
echo "  scripts/start.sh"
echo
