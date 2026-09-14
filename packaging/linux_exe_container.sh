#!/usr/bin/env bash
# Runs inside ubuntu:18.04 / 20.04 / 22.04 / 24.04.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export PATH="/usr/local/bin:${HOME}/.local/bin:${PATH}"

if [ -f /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
fi
_point_apt_old_releases() {
  if [ -f /etc/apt/sources.list ]; then
    sed -i \
      -e 's|http://archive.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' \
      -e 's|http://security.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' \
      /etc/apt/sources.list
  fi
  if [ -d /etc/apt/sources.list.d ]; then
    for list in /etc/apt/sources.list.d/*.list; do
      [ -f "${list}" ] || continue
      sed -i \
        -e 's|http://archive.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' \
        -e 's|http://security.ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|g' \
        "${list}"
    done
  fi
}

APT_OK=0
if [ "${VERSION_CODENAME:-}" = "bionic" ] || [ "${VERSION_ID:-}" = "18.04" ]; then
  _point_apt_old_releases
fi
if apt-get update; then
  apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gcc \
    g++ \
    make \
    pkg-config \
    unzip \
    xz-utils \
    zlib1g \
    zlib1g-dev \
    libffi-dev \
    libssl-dev
  APT_OK=1
fi

if [ -d /src/.git ]; then
  git config --global --add safe.directory /src || true
fi

if ! command -v uv >/dev/null 2>&1; then
  if [ "${APT_OK}" != "1" ]; then
    echo "apt is unavailable and /usr/local/bin/uv is missing" >&2
    exit 1
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi
uv python install 3.11
VENV=/tmp/creasy-build-venv
uv venv --python 3.11 "${VENV}"
uv pip install --python "${VENV}" -e . pyinstaller
"${VENV}/bin/python" packaging/build_exe.py \
  --skip-web \
  --suffix "${CREASY_LINUX_SUFFIX:?}" \
  --zip \
  --out-dir /src/dist/exe
