#!/usr/bin/env bash
# Creasy — start the webhook + dashboard server (OSM start-backend.sh pattern).
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

DASH_PORT="${PORT:-8000}"
VENV_PY="$ROOT/.venv/bin/python"
CREASY_PY=""
if [[ -x "$VENV_PY" ]]; then
  CREASY_PY="$VENV_PY"
fi

export GIT_TERMINAL_PROMPT=0
export GIT_SSL_NO_VERIFY=1
export PYTHONUNBUFFERED=1
if [[ -d "$HOME/.opencode/bin" ]]; then
  export PATH="$HOME/.opencode/bin:$PATH"
fi
if [[ -d "$ROOT/vendor/bin" ]]; then
  export PATH="$ROOT/vendor/bin:$PATH"
fi

echo "========================================"
echo "  Creasy"
echo "========================================"
echo "Project : $ROOT"
echo "Server  : http://0.0.0.0:${DASH_PORT}/  (open http://127.0.0.1:${DASH_PORT}/jobs )"
echo

if [[ -z "$CREASY_PY" ]]; then
  echo "[ERROR] .venv is missing."
  echo "Run scripts/install.sh first. It creates .venv from the bundled Python for this OS."
  exit 1
fi
echo "Python  : $CREASY_PY"

if command -v git >/dev/null 2>&1; then
  echo "[OK] git found"
else
  echo "[WARNING] git is not on PATH. Reviews cannot clone."
fi

if command -v opencode >/dev/null 2>&1; then
  echo "[OK] opencode on PATH"
else
  echo "[WARNING] opencode is not on PATH. Jobs will fail until OpenCode is installed."
  echo "          Run scripts/install-opencode.sh (keeps existing home, copies opencoderman)."
fi

if [[ -f "$ROOT/scripts/creasy-lib.sh" ]]; then
  # shellcheck disable=SC1091
  . "$ROOT/scripts/creasy-lib.sh"
  creasy_chmod_launchers "$ROOT"
fi

if [[ ! -f "$ROOT/web/dist/index.html" ]]; then
  echo "[WARNING] web/dist/index.html missing. /jobs will 404."
  echo "          Use the CI zip or run python3 packaging/build_dist.py --in-place."
fi

if [[ -f "$ROOT/.env.example" && ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "[WARNING] Wrote .env from .env.example. Set GITLAB_TOKEN and WEBHOOK_SECRET."
fi

echo "Starting Creasy (Ctrl+C to stop)..."
mkdir -p "$ROOT/logs"
set +e
"$CREASY_PY" -m creasy
ec=$?
set -e
echo "Creasy exited. code=${ec}"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) exit=${ec}" >> "$ROOT/logs/wrapper-exit.log"
exit "$ec"
