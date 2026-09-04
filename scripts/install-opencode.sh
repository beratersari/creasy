#!/usr/bin/env bash
# Creasy — install OpenCode CLI (offline).
# Keeps an existing user install. Adds the review agent. Copies vendor/bin only if missing.
# Does not install Python / the dashboard. Use install.sh for that.
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
# shellcheck source=creasy-lib.sh
. "$ROOT/scripts/creasy-lib.sh"
creasy_chmod_launchers "$ROOT"

echo "========================================"
echo "  Creasy"
echo "  OpenCode CLI install (offline, keeps existing home)"
echo "========================================"
echo
echo "Project : $ROOT"
echo "Target  : $HOME/.opencode"
echo

if [[ ! -f "$ROOT/opencode-configs/agents/review.md" ]]; then
  echo "[ERROR] opencode-configs/agents/review.md is missing."
  echo "Clone with: git clone --recurse-submodules"
  echo "Or run: git submodule update --init --recursive"
  exit 1
fi

if [[ ! -f "$ROOT/vendor/bin/$(creasy_os_tag)/opencode" && ! -f "$ROOT/vendor/bin/opencode" ]]; then
  echo "[ERROR] vendor/bin/$(creasy_os_tag)/opencode is missing."
  echo "Use a current CI zip (macOS needs darwin-arm64 or darwin-x64), or:"
  echo "  python3 packaging/build_dist.py --in-place"
  exit 1
fi

PY="$(creasy_require_bundled_python "$ROOT")" || exit 1

echo "Python  : $PY"
echo
"$PY" "$ROOT/scripts/install_opencode.py" --root "$ROOT"

echo
echo "New shells pick up \$HOME/.opencode/bin (start.sh also prepends it)."
echo "Then: scripts/start.sh"
echo
