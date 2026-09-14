#!/usr/bin/env bash
# Build one Linux onefile zip inside ubuntu:<version>.
# Usage: packaging/linux_exe_in_docker.sh 22.04
set -euo pipefail
UBUNTU="${1:?ubuntu version, e.g. 22.04}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUFFIX="linux-ubuntu-${UBUNTU}-x64"
if [ ! -f "${ROOT}/web/dist/index.html" ]; then
  echo "web/dist/index.html missing; build the dashboard first" >&2
  exit 1
fi
mkdir -p "${ROOT}/dist/exe"
docker run --rm \
  -e DEBIAN_FRONTEND=noninteractive \
  -e CREASY_LINUX_SUFFIX="${SUFFIX}" \
  -v "${ROOT}:/src" \
  -w /src \
  "ubuntu:${UBUNTU}" \
  bash /src/packaging/linux_exe_container.sh
