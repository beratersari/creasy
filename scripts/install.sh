#!/usr/bin/env bash
# Creasy — install manager (offline).
# Python venv + wheels from bundled CPython. Does NOT install OpenCode.
# Use install-opencode.sh for the CLI.
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

if ! compgen -G "$WHEELS"/*.whl > /dev/null; then
  echo "[ERROR] vendor/python-wheels is missing or empty."
  echo "This installer is offline-only. Use the CI zip, or on a machine with network:"
  echo "  python3 packaging/build_dist.py --in-place"
  exit 1
fi
if [[ ! -f "$ROOT/web/dist/index.html" ]]; then
  echo "[ERROR] Missing $ROOT/web/dist/index.html"
  echo "The dashboard is the built OSM SPA. This installer is offline-only and does not run npm."
  echo "Use the CI zip, or on a machine with network:"
  echo "  python3 packaging/build_dist.py --in-place"
  exit 1
fi

# shellcheck source=creasy-lib.sh
. "$ROOT/scripts/creasy-lib.sh"
creasy_chmod_launchers "$ROOT"
BUNDLED_PY="$(creasy_require_bundled_python "$ROOT")" || exit 1
PYTHON_VERSION="$("$BUNDLED_PY" --version 2>&1)"
echo "[OK] Bundled $PYTHON_VERSION ($(creasy_os_tag))"
echo "     $BUNDLED_PY"

if command -v git >/dev/null 2>&1; then
  echo "[OK] git found"
else
  echo "[WARNING] git is not on PATH. Clone jobs will fail until Git is installed."
fi

echo
echo "Step 1: Python virtual environment from bundled python..."
if [[ -e "$VENV_DIR" ]]; then
  echo "Removing existing .venv so it matches the bundled interpreter..."
  rm -rf "$VENV_DIR"
fi
"$BUNDLED_PY" -m venv "$VENV_DIR"
echo "[OK] Created $VENV_DIR"
VENV_PY="$VENV_DIR/bin/python"

echo
echo "Step 2: Installing packages from vendor/python-wheels (no network)..."
"$VENV_PY" -m pip install --upgrade pip --no-index --find-links="$WHEELS"
if ! "$VENV_PY" -m pip install --no-index --find-links="$WHEELS" -e .; then
  echo "[ERROR] Offline package install failed."
  echo "Wheels must match the bundled interpreter. Need PyYAML + pydantic-core for this OS."
  echo "Present yaml / pydantic-core wheels:"
  ls -1 "$WHEELS"/*[Yy][Aa][Mm][Ll]* "$WHEELS"/*pydantic_core* 2>/dev/null || true
  exit 1
fi
echo "[OK] Creasy installed into .venv from local wheels"

if [[ -f "$ROOT/.env.example" && ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "[OK] Wrote .env from .env.example. Set GITLAB_TOKEN and WEBHOOK_SECRET."
fi

echo
echo "Step 3: Dashboard..."
if [[ -f "$ROOT/web/dist/index.html" ]]; then
  echo "[OK] Dashboard present: web/dist"
else
  echo "[WARNING] web/dist/index.html missing. /jobs will 404."
fi

echo
echo "========================================"
echo "  Install complete"
echo "========================================"
echo
echo "OpenCode is separate:"
echo "  scripts/install-opencode.sh"
echo "Then:"
echo "  scripts/start.sh"
echo "Dashboard: http://127.0.0.1:9001/jobs"
echo
