#!/usr/bin/env bash
# The companion app on Linux, end to end, in Docker (tests/docker/vm.sh on a Mac).
#   companion/tests/linux/run.sh
# Cargo's caches live in a Docker volume (on the VM's disk), so a rerun builds
# only what changed.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
if [ -n "${HIDFI_VM_HOME:-}" ] && [ -z "${DOCKER_HOST:-}" ]; then
  eval "$("$repo/tests/docker/vm.sh" env)"
fi
docker build -q -t hidfi-companion-linux "$here" >/dev/null
docker run --rm -v "$repo:/src:ro" \
  -v hidfi-companion-cargo:/root/.cargo/registry -v hidfi-companion-target:/work/companion/target \
  hidfi-companion-linux sh /src/companion/tests/linux/inside.sh
