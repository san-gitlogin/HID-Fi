#!/usr/bin/env bash
# Run the tiers that need Linux, in Docker, on this Mac (or any machine with Docker).
#
#   tests/docker/run.sh                 hostsim: the board's typing replayed into a Linux desktop -
#                                       the one tier that needs Linux (minutes)
#   tests/docker/run.sh ui              the dashboard and its 517-screen layout matrix on Linux,
#                                       for Linux layout baselines - heavy (about an hour), only
#                                       after a layout change
#   tests/docker/run.sh static,unit,ui,hostsim   everything CI runs, minus the firmware build
#   HIDFI_K="<pytest -k>" tests/docker/run.sh ui   only the matching tests
#   HIDFI_RUN_ARGS=--update-golden ...           record Linux baselines (review them)
#   HIDFI_SHM=3g, HIDFI_VM_MEMORY_GB=8 (vm.sh up)  the 5K-8K layout screens crash
#                                       Chromium in a 4 GB VM with 1 GB of /dev/shm
#
# The repository is mounted read-only and copied inside, so nothing the run
# writes (reports, caches, new golden files) lands in your working tree as root.
# Results go to tests/.reports/linux/; golden files the run created for Linux
# (e.g. ui_responsive_known.linux.json) go there too, for review before copying
# them into tests/golden/.
#
# The build tier is left out: it needs the ESP32 core (~1 GB) and runs the same
# on macOS.
#
# Two parallel workers by default (HIDFI_WORKERS): four Chromiums in a 4 GB VM
# crash the browser ("Target crashed"), which looks like a bug and is not one.
#
# Which Docker: any that is running (Docker Desktop, Linux, colima). With
# HIDFI_VM_HOME set (see tests/docker/vm.sh) it uses that VM, and the results go
# to $HIDFI_VM_HOME/reports instead, so nothing is written to the internal disk.
set -euo pipefail
cd "$(dirname "$0")/../.."
TIERS="${1:-${TIERS:-hostsim}}"
IMAGE=hidfi-linux-tests
OUT="$PWD/tests/.reports/linux"
if [ -n "${HIDFI_VM_HOME:-}" ]; then
    export DOCKER_HOST="unix://$HIDFI_VM_HOME/colima/default/docker.sock" DOCKER_CONFIG="$HIDFI_VM_HOME/docker-config"
    OUT="$HIDFI_VM_HOME/reports"
fi

docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop, or: tests/docker/vm.sh up"; exit 2; }
docker build -q -t "$IMAGE" -f tests/docker/Dockerfile tests >/dev/null
rm -rf "$OUT" && mkdir -p "$OUT"
echo "== Linux tiers: $TIERS (ubuntu 24.04, $(docker version -f '{{.Server.Arch}}'))"
set +e
docker run --rm --init --shm-size="${HIDFI_SHM:-1g}" \
    -v "$PWD":/src:ro -v "$OUT":/out \
    -e TIERS="$TIERS" -e HIDFI_WORKERS="${HIDFI_WORKERS:-2}" -e HIDFI_K="${HIDFI_K:-}" -e HIDFI_RUN_ARGS="${HIDFI_RUN_ARGS:-}" \
    "$IMAGE" bash -c '
        # Never the Mac'"'"'s own build output: companion/target alone reached 26 GB
        # and filled the VM'"'"'s disk mid-copy (2026-10-30).
        rsync -a --exclude .git --exclude tests/.build --exclude tests/.reports --exclude "hid_fi/build" \
              --exclude companion/target /src/ /work/ && cd /work &&
        ls tests/golden > /tmp/before &&
        python3 tests/run.py --tier "$TIERS" ${HIDFI_K:+-k "$HIDFI_K"} $HIDFI_RUN_ARGS; code=$?
        cp -r tests/.reports/. /out/ 2>/dev/null
        for f in tests/golden/*; do grep -qx "$(basename "$f")" /tmp/before || cp "$f" /out/; done
        exit $code'
code=$?
set -e
new=$(cd "$OUT" && ls *.linux.json 2>/dev/null || true)
[ -n "$new" ] && echo "   New Linux golden files (review, then copy to tests/golden/): $new"
echo "   Reports: $OUT/"
exit $code
