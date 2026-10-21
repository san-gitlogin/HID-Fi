#!/bin/sh
# Inside the container (see run.sh). The repository is at /src, read-only.
set -eu
# A 4 GB VM: two jobs at a time, no debug info (it is what makes links big).
export CARGO_BUILD_JOBS=2 CARGO_PROFILE_DEV_DEBUG=0 CARGO_PROFILE_TEST_DEBUG=0
step() { printf '\n== %s\n' "$*"; }
# Copy the sources, never the Mac's own build output.
mkdir -p /work
tar -C /src --exclude=./companion/target --exclude=./tests/.build --exclude=./hid_fi/build \
    --exclude=./.git --exclude='*/node_modules' -cf - . | tar -C /work -xf -
cd /work/companion
step "build and unit tests, on Linux ($(uname -m))"
cargo test --workspace -q 2>&1 | grep -E "test result|panicked|^error|FAILED" || true
cargo test --workspace -q >/dev/null 2>&1 || { echo "FAIL unit tests"; cargo test --workspace -q 2>&1 | tail -40; exit 1; }
echo "ok   unit tests pass on Linux"
cargo clippy -q --workspace --all-targets -- -D warnings && echo "ok   clippy -D warnings"
cargo build --release -q -p hidfi-stream --example serve
cargo build --release -q -p hidfi-companion --bin hidfi-companion
echo "ok   release build: app binary $(du -h target/release/hidfi-companion | cut -f1)"

step "Cisco's OpenH264, as the installer fetches it"
sh package/fetch-openh264.sh "$HOME/.local/share/hidfi-companion" && ls "$HOME/.local/share/hidfi-companion"

step "a virtual desktop with two monitors"
Xvfb :99 -screen 0 3200x1200x24 +extension RANDR >/tmp/xvfb.log 2>&1 &
export DISPLAY=:99
for i in 1 2 3 4 5 6 7 8 9 10; do xdpyinfo >/dev/null 2>&1 && break; sleep 0.5; done
# Xvfb keeps no RandR monitors (xrandr --setmonitor is accepted and dropped),
# so the layout is given by hand, as for any X server that reports none.
export HIDFI_X11_MONITORS="LEFT=1920x1080+0+0,RIGHT=1280x1200+1920+0"
# Left red, right blue (an xlogo window over it, no window manager to move it).
xsetroot -solid '#c00000'
xlogo -geometry 1280x1200+1920+0 -bg '#0000c0' -fg '#0000c0' &
# Something moving: the root flickers between two reds 20 times a second.
( while :; do xsetroot -solid '#c00000'; sleep 0.05; xsetroot -solid '#c00010'; sleep 0.05; done ) &

step "the app itself, on a desktop with no tray (GNOME without its extension)"
H=$(mktemp -d)
HIDFI_HOME=$H HIDFI_NO_AUTOSTART=1 dbus-run-session -- ./target/release/hidfi-companion >/tmp/app.out 2>&1 &
APP=$!
sleep 8
if kill -0 $APP 2>/dev/null; then
  P=$(pgrep -f '[t]arget/release/hidfi-companion' | head -1)
  echo "ok   it starts and keeps running: $(ps -o rss= -p "$P" | awk '{print int($1/1024)}') MB, cpu $(ps -o %cpu= -p "$P" | tr -d ' ')%"
else
  echo "FAIL the app exited"; cat /tmp/app.out; exit 1
fi
grep -E "starting|board|tray|notif" "$H/companion.log" | sed 's/^[0-9]* /     /' | head -6
kill $APP

step "stream it: real X11 capture, real OpenH264, WebRTC"
HIDFI_REAL_SCREEN=1 ./target/release/examples/serve 1920 1080 30 > /tmp/serve.json 2>/tmp/serve.log &
for i in $(seq 1 40); do [ -s /tmp/serve.json ] && break; sleep 0.25; done
python3 tests/linux/view_e2e.py /tmp/serve.json; rc=$?
grep -E "INFO|WARN|ERROR" /tmp/serve.log | tail -6
exit $rc
