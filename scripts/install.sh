#!/usr/bin/env bash
# Creasy — offline install. Creates .venv from vendor/python-wheels.
# Does NOT install OpenCode. Put the CLI on PATH or in vendor/bin.
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
VENV_DIR="$ROOT/.venv"
WHEELS="$ROOT/vendor/python-wheels"
cd "$ROOT"

echo "========================================"
echo "  Creasy"
echo "  Install (offline)"
echo "========================================"
echo
echo "Project : $ROOT"
echo

if [[ ! -d "$WHEELS" ]]; then
  echo "[ERROR] vendor/python-wheels is missing."
  echo "This installer is offline-only. On a machine with network run:"
  echo "  scripts/vendor.sh"
  echo "then copy vendor/python-wheels with the repo."
  exit 1
fi

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
  echo "[ERROR] No Python interpreter."
  echo "Place one under vendor/python/ or install Python 3.10+."
  exit 1
fi
echo "[OK] $($PY --version 2>&1)"
echo "     $PY"

if command -v git >/dev/null 2>&1; then
  echo "[OK] git found"
else
  echo "[WARNING] git is not on PATH. Clone jobs will fail until Git is installed."
fi

echo
echo "Step 1: Python virtual environment..."
if [[ -e "$VENV_DIR" ]]; then
  echo "Removing existing .venv so it matches this interpreter..."
  rm -rf "$VENV_DIR"
fi
"$PY" -m venv "$VENV_DIR"
echo "[OK] Created $VENV_DIR"
VENV_PY="$VENV_DIR/bin/python"

echo
echo "Step 2: Installing packages from vendor/python-wheels (no network)..."
"$VENV_PY" -m pip install --upgrade pip --no-index --find-links="$WHEELS"
if ! "$VENV_PY" -m pip install --no-index --find-links="$WHEELS" -e .; then
  echo "[ERROR] Offline package install failed."
  echo "Wheels must match this interpreter. Re-run scripts/vendor.sh on the same OS."
  ls -1 "$WHEELS"/*.whl 2>/dev/null || true
  exit 1
fi
echo "[OK] Creasy installed into .venv from local wheels"

if [[ -f "$ROOT/.env.example" && ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "[OK] Wrote .env from .env.example — set GITLAB_TOKEN and WEBHOOK_SECRET"
fi

if [[ -f "$ROOT/web/index.html" ]]; then
  echo "[OK] Dashboard present: web/index.html"
else
  echo "[WARNING] web/index.html missing — /jobs will 404"
fi

echo
echo "========================================"
echo "  Install complete"
echo "========================================"
echo
echo "Edit .env then:"
echo "  scripts/start.sh"
echo "Dashboard: http://127.0.0.1:8000/jobs"
echo
