#!/usr/bin/env bash
# Creasy — start the webhook + dashboard server.
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

export GIT_TERMINAL_PROMPT=0
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
echo "Project  : $ROOT"
echo "Server   : http://127.0.0.1:${DASH_PORT}/"
echo "Dashboard: http://127.0.0.1:${DASH_PORT}/jobs"
echo "Webhook  : POST http://127.0.0.1:${DASH_PORT}/webhook"
echo

if [[ ! -x "$VENV_PY" ]]; then
  echo "[ERROR] .venv is missing."
  echo "Run scripts/install.sh first (offline wheels in vendor/python-wheels)."
  exit 1
fi
echo "Python   : $VENV_PY"

if command -v git >/dev/null 2>&1; then
  echo "[OK] git found"
else
  echo "[WARNING] git is not on PATH. Reviews cannot clone."
fi

if command -v opencode >/dev/null 2>&1; then
  echo "[OK] opencode on PATH"
else
  echo "[WARNING] opencode is not on PATH. Jobs will fail until OpenCode is installed."
  echo "          Put the CLI on PATH or in vendor/bin."
fi

if [[ -f "$ROOT/.env.example" && ! -f "$ROOT/.env" ]]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  echo "[WARNING] Wrote .env from .env.example — set GITLAB_TOKEN and WEBHOOK_SECRET"
fi

mkdir -p "$ROOT/logs"
echo "Starting Creasy (Ctrl+C to stop)..."
set +e
"$VENV_PY" -m creasy
ec=$?
set -e
echo "Creasy exited. code=${ec}"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) exit=${ec}" >> "$ROOT/logs/wrapper-exit.log"
exit "$ec"
