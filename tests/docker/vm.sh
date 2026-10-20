#!/usr/bin/env bash
# A Linux VM for the Linux test tiers on a Mac without Docker Desktop - with every
# byte of it (tools, VM disk, images, caches) in one folder you choose, such as an
# external drive. Nothing is installed system-wide and nothing goes to ~/Library.
#
#   tests/docker/vm.sh image "/Volumes/My SSD"   once: a 200 GB growable APFS disk
#                                                image on that drive, mounted at
#                                                /Volumes/HIDFi-Linux (works on exFAT
#                                                drives, which cannot hold a VM)
#   export HIDFI_VM_HOME=/Volumes/HIDFi-Linux    where everything lives (any APFS folder)
#   tests/docker/vm.sh setup                     once: colima, lima, buildx into it, verified
#   tests/docker/vm.sh up                        start the VM (about 30 s; first time ~2 min)
#   tests/docker/run.sh                          the Linux tiers - uses the VM by itself
#   tests/docker/vm.sh down                      stop it; gives the memory back
#   tests/docker/vm.sh status | env | shell
#
# Linux and Docker Desktop users do not need this: tests/docker/run.sh uses any
# running Docker. Apple Silicon or Intel, macOS 13 or later (Apple's virtualization).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
COLIMA_V=v0.10.3
LIMA_V=2.2.1
BUILDX_V=v0.38.0
IMAGE_NAME=HIDFi-Linux

die() { echo "vm.sh: $*" >&2; exit 2; }

need_home() {
    [ -n "${HIDFI_VM_HOME:-}" ] || die "set HIDFI_VM_HOME to the folder that holds the VM (e.g. /Volumes/HIDFi-Linux)"
    # A disk image that is not mounted yet: mount it, rather than writing into an
    # empty mount point on the internal disk.
    if [ ! -d "$HIDFI_VM_HOME" ] && [ "$HIDFI_VM_HOME" = "/Volumes/$IMAGE_NAME" ]; then
        local b
        b=$(ls -d /Volumes/*/"$IMAGE_NAME".sparsebundle 2>/dev/null | head -1 || true)
        [ -n "$b" ] && hdiutil attach "$b" -nobrowse >/dev/null
    fi
    [ -d "$HIDFI_VM_HOME" ] || die "$HIDFI_VM_HOME does not exist (plugged in? mounted?)"
    local fs
    fs=$(diskutil info "$HIDFI_VM_HOME" 2>/dev/null | awk -F: '/File System Personality/{gsub(/^ +/,"",$2);print $2}')
    case "$fs" in *APFS*|*HFS*|"") ;; *) die "$HIDFI_VM_HOME is $fs, which cannot hold a VM - use: vm.sh image <drive>";; esac
}

# Everything the tools would write goes under HIDFI_VM_HOME. HOME is redirected
# for these processes only: colima and lima cache their downloads under HOME.
vm_env() {
    export HOME="$HIDFI_VM_HOME/home"
    export COLIMA_HOME="$HIDFI_VM_HOME/colima"
    export DOCKER_CONFIG="$HIDFI_VM_HOME/docker-config"
    export PATH="$HIDFI_VM_HOME/bin:$HIDFI_VM_HOME/lima/bin:$PATH"
    export DOCKER_HOST="unix://$HIDFI_VM_HOME/colima/default/docker.sock"
    mkdir -p "$HOME" "$COLIMA_HOME" "$DOCKER_CONFIG/cli-plugins"
}

fetch() {   # repo tag asset dest - verified against GitHub's own SHA-256 for that file
    local repo=$1 tag=$2 asset=$3 dest=$4 want got
    curl -fsSL -o "$dest" "https://github.com/$repo/releases/download/$tag/$asset"
    want=$(curl -fsSL "https://api.github.com/repos/$repo/releases/tags/$tag" |
           python3 -c "import json,sys;print(next(a['digest'] for a in json.load(sys.stdin)['assets'] if a['name']=='$asset'))")
    got="sha256:$(shasum -a 256 "$dest" | awk '{print $1}')"
    [ "$want" = "$got" ] || { rm -f "$dest"; die "$asset failed its checksum ($got, expected $want)"; }
    echo "   $asset verified"
}

case "${1:-}" in
image)
    drive="${2:-}"; [ -d "$drive" ] || die "usage: vm.sh image \"/Volumes/<drive>\""
    [ -e "$drive/$IMAGE_NAME.sparsebundle" ] || hdiutil create -size 200g -type SPARSEBUNDLE -fs APFS \
        -volname "$IMAGE_NAME" "$drive/$IMAGE_NAME.sparsebundle" >/dev/null
    [ -d "/Volumes/$IMAGE_NAME" ] || hdiutil attach "$drive/$IMAGE_NAME.sparsebundle" -nobrowse >/dev/null
    echo "Disk image on $drive, mounted at /Volumes/$IMAGE_NAME. Now:"
    echo "  export HIDFI_VM_HOME=/Volumes/$IMAGE_NAME && tests/docker/vm.sh setup"
    ;;
setup)
    need_home; vm_env
    [ "$(uname -s)" = Darwin ] || die "this sets up a VM for macOS; on Linux, install Docker and run tests/docker/run.sh"
    arch=$(uname -m); [ "$arch" = arm64 ] || arch=x86_64
    barch=$([ "$arch" = arm64 ] && echo arm64 || echo amd64)
    mkdir -p "$HIDFI_VM_HOME/bin" "$HIDFI_VM_HOME/lima" "$HIDFI_VM_HOME/tmp"
    tmp="$HIDFI_VM_HOME/tmp"
    fetch abiosoft/colima "$COLIMA_V" "colima-Darwin-$arch" "$tmp/colima"
    fetch lima-vm/lima "v$LIMA_V" "lima-$LIMA_V-Darwin-$arch.tar.gz" "$tmp/lima.tgz"
    fetch docker/buildx "$BUILDX_V" "buildx-$BUILDX_V.darwin-$barch" "$tmp/buildx"
    tar -xzf "$tmp/lima.tgz" -C "$HIDFI_VM_HOME/lima"
    install -m 755 "$tmp/colima" "$HIDFI_VM_HOME/bin/colima"
    install -m 755 "$tmp/buildx" "$DOCKER_CONFIG/cli-plugins/docker-buildx"
    rm -f "$tmp"/*
    command -v docker >/dev/null || echo "   Also needed: the docker command line (brew install docker - 30 MB, no daemon)"
    echo "Tools in $HIDFI_VM_HOME. Next: tests/docker/vm.sh up"
    ;;
up)
    need_home; vm_env
    command -v colima >/dev/null || die "run tests/docker/vm.sh setup first"
    # The repository and HIDFI_VM_HOME are shared into the VM, so docker -v works on them.
    colima start --vm-type vz --cpu "${HIDFI_VM_CPUS:-4}" --memory "${HIDFI_VM_MEMORY_GB:-4}" \
        --disk "${HIDFI_VM_DISK_GB:-60}" --mount "$REPO:w" --mount "$HIDFI_VM_HOME:w"
    ;;
down)    need_home; vm_env; colima stop ;;
status)  need_home; vm_env; colima status 2>&1; echo "disk used: $(du -sh "$HIDFI_VM_HOME" | cut -f1)" ;;
env)     need_home; vm_env; echo "export DOCKER_HOST=$DOCKER_HOST DOCKER_CONFIG=$DOCKER_CONFIG" ;;
shell)   need_home; vm_env; colima ssh ;;
*)       sed -n '2,19p' "$0"; exit 1 ;;
esac
