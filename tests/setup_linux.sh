#!/usr/bin/env bash
# Everything the test suite needs on Ubuntu or Debian - the one list, used by
# every Linux route: a Linux PC, Windows' WSL, ChromeOS's Linux, a cloud agent's
# sandbox (Codex, Copilot, Jules...), the Docker image (tests/docker/), and
# Copilot's setup (.github/workflows/copilot-setup-steps.yml). CI installs the
# same packages; tests/static checks the two lists agree.
#
#   tests/setup_linux.sh               install (asks sudo for the system packages unless root)
#   tests/setup_linux.sh --no-firmware skip the ESP32 toolchain (~2 GB): hostsim and some
#                                      unit tests compile the board's own typing code, so
#                                      they are then skipped
#   tests/setup_linux.sh --firmware-only  just the ESP32 toolchain (the Docker image's second layer)
#   tests/setup_linux.sh --print       show what it would install, change nothing
#   python3 tests/run.py            then the whole no-board suite, hostsim included
#
# Ubuntu 22.04+ / Debian 12+. On Fedora or Arch, install the same things by
# their names there (Xvfb, ibus with GTK3 support, PyGObject, GTK 3, python-xlib,
# xkbcomp, dbus-x11, a C++ compiler, Node 18+), or use Docker: tests/docker/run.sh.
set -euo pipefail

APT_PACKAGES=(
    # the language runtimes and a compiler (unit tier)
    python3 python3-pip python3-venv python3-dev gcc g++ nodejs rsync ca-certificates curl
    # the Linux input stack the hostsim tier types into - same as CI
    xvfb ibus ibus-gtk3 python3-gi gir1.2-gtk-3.0 python3-xlib x11-xkb-utils dbus-x11
)
HERE="$(cd "$(dirname "$0")" && pwd)"
REQ="$HERE/requirements.txt"
DEPS="$HERE/deps.json"              # the exact firmware pins, the same CI installs
FIRMWARE=1 SYSTEM=1
[ "${1:-}" = "--no-firmware" ] && FIRMWARE=0
[ "${1:-}" = "--firmware-only" ] && SYSTEM=0
# Large downloads from GitHub do time out now and then; one hiccup should not
# fail the whole setup.
retry() { local n; for n in 1 2 3; do "$@" && return 0; echo "   retrying ($n/3): $*"; sleep $((n * 5)); done; return 1; }
pin() { python3 -c "import json,sys;print(json.load(open('$DEPS'))['firmware'][sys.argv[1]])" "$1"; }

if [ "${1:-}" = "--print" ]; then
    echo "apt:  ${APT_PACKAGES[*]}"
    echo "pip:  -r $REQ (into the system python3 - hostsim needs its python3-gi)"
    echo "then: python3 -m playwright install --with-deps chromium webkit"
    echo "and:  arduino-cli, then the pinned firmware deps from $DEPS (unless --no-firmware)"
    exit 0
fi
command -v apt-get >/dev/null || { echo "No apt-get here. See the comment at the top of $0 for other systems."; exit 2; }

SUDO=""
[ "$(id -u)" = 0 ] || SUDO="sudo"
export DEBIAN_FRONTEND=noninteractive
if [ "$SYSTEM" = 1 ]; then
$SUDO apt-get update -qq
$SUDO apt-get install -y -qq --no-install-recommends "${APT_PACKAGES[@]}"

# The system python on purpose: python3-gi (GTK, IBus) only exists for it.
# --break-system-packages is what Debian requires for that; --user keeps it out
# of /usr when not root.
PIP=(python3 -m pip install -q --break-system-packages -r "$REQ")
[ -n "$SUDO" ] && PIP+=(--user)
"${PIP[@]}"

# Chromium for the dashboard tests, and the libraries it needs. Always as this
# user: Playwright calls sudo itself for the system libraries. (Running it under
# sudo instead fails when Playwright was installed with --user, as above: root's
# python cannot see this user's packages - which is what a WSL user gets.)
python3 -m playwright install --with-deps chromium webkit
fi
# The ESP32 toolchain, at the exact versions in tests/deps.json - what CI's
# "Pinned firmware dependencies" step installs.
if [ "$FIRMWARE" = 1 ]; then
    if ! command -v arduino-cli >/dev/null; then
        BINDIR=/usr/local/bin
        [ -n "$SUDO" ] && BINDIR="$HOME/.local/bin" && mkdir -p "$BINDIR"
        case ":$PATH:" in *":$BINDIR:"*) ;; *) echo "   Note: add $BINDIR to your PATH (e.g. in ~/.bashrc)";; esac
        # The release archive for this machine, checked against GitHub's own
        # SHA-256 for it - never a script piped into the shell.
        case "$(uname -m)" in aarch64|arm64) A=Linux_ARM64 ;; x86_64) A=Linux_64bit ;; *) echo "unsupported CPU $(uname -m)"; exit 2 ;; esac
        read -r URL DIGEST < <(curl -fsSL https://api.github.com/repos/arduino/arduino-cli/releases/latest | python3 -c "
import json,sys
a=[x for x in json.load(sys.stdin)['assets'] if x['name'].endswith('_$A.tar.gz')][0]
print(a['browser_download_url'], a['digest'])")
        T="$(mktemp -d)"
        curl -fsSL -o "$T/cli.tgz" "$URL"
        [ "sha256:$(sha256sum "$T/cli.tgz" | cut -d' ' -f1)" = "$DIGEST" ] || { echo "arduino-cli failed its checksum"; exit 2; }
        tar -xzf "$T/cli.tgz" -C "$T" arduino-cli
        install -m 755 "$T/arduino-cli" "$BINDIR/arduino-cli"
        rm -rf "$T"
        export PATH="$BINDIR:$PATH"
    fi
    retry arduino-cli core update-index >/dev/null
    retry arduino-cli core install "esp32:esp32@$(pin esp32:esp32)" >/dev/null
    retry arduino-cli lib install "ArduinoJson@$(pin ArduinoJson)" "WebSockets@$(pin WebSockets)" >/dev/null
    echo "   ESP32 core $(pin esp32:esp32), ArduinoJson $(pin ArduinoJson), WebSockets $(pin WebSockets)"
fi
echo "Ready. Run: python3 tests/run.py"
