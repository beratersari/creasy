#!/usr/bin/env bash
# Shared helpers for install/start scripts. Sourced, not executed.

creasy_os_tag() {
  case "$(uname -s 2>/dev/null)" in
    Darwin)
      case "$(uname -m 2>/dev/null)" in
        arm64|aarch64) echo darwin-arm64 ;;
        *) echo darwin-x64 ;;
      esac
      ;;
    Linux) echo linux ;;
    *) echo unknown ;;
  esac
}

creasy_bundled_python() {
  local root="$1"
  local tag
  tag="$(creasy_os_tag)"
  case "$tag" in
    linux) echo "$root/vendor/python/linux/bin/python3" ;;
    darwin-arm64) echo "$root/vendor/python/darwin-arm64/bin/python3" ;;
    darwin-x64) echo "$root/vendor/python/darwin-x64/bin/python3" ;;
    *) echo "" ;;
  esac
}

creasy_require_bundled_python() {
  local root="$1"
  local path
  path="$(creasy_bundled_python "$root")"
  if [[ -z "$path" || ! -f "$path" ]]; then
    echo "[ERROR] No bundled Python for $(uname -s) $(uname -m)."
    echo "Looked for: ${path:-<unknown>}"
    echo "This zip must include vendor/python/linux, darwin-arm64, or darwin-x64."
    echo "Download a current Offline Distribution artifact, or rebuild:"
    echo "  python3 packaging/build_dist.py --in-place"
    return 1
  fi
  if [[ ! -x "$path" ]]; then
    chmod +x "$path" 2>/dev/null || true
  fi
  if ! "$path" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 11) else 1)" >/dev/null 2>&1; then
    echo "[ERROR] Bundled Python cannot run on this machine (wrong OS/arch or exec format):"
    echo "  $path"
    echo "  $(uname -s) $(uname -m)"
    return 1
  fi
  echo "$path"
}

# Zip extractors (python -m zipfile, some GUIs) drop Unix +x. Restore launchers.
creasy_chmod_launchers() {
  local root="$1"
  local f
  for f in \
    install.sh install-opencode.sh start.sh vendor.sh \
    scripts/install.sh scripts/install-opencode.sh scripts/start.sh \
    scripts/vendor.sh scripts/creasy-lib.sh
  do
    if [[ -f "$root/$f" ]]; then
      chmod +x "$root/$f" 2>/dev/null || true
    fi
  done
}
