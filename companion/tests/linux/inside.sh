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

step "sound: a real PulseAudio server, a tone to its output, the app's own capture"
pulseaudio --start --exit-idle-time=-1 >/tmp/pulse.log 2>&1 || true
for i in 1 2 3 4 5 6 7 8 9 10; do pactl info >/dev/null 2>&1 && break; sleep 0.5; done
pactl load-module module-null-sink sink_name=speakers >/dev/null
pactl load-module module-null-sink sink_name=headphones >/dev/null
pactl set-default-sink speakers
tone() { python3 -c "import sys,math,struct;f=float(sys.argv[1]);n=int(48000*float(sys.argv[2]));sys.stdout.buffer.write(b''.join(struct.pack('<ff',0.3*math.sin(2*math.pi*f*i/48000),0.3*math.sin(2*math.pi*f*i/48000)) for i in range(n)))" "$1" "$2"; }
( sleep 1; tone 1000 3 | pacat --device=speakers --format=float32le --rate=48000 --channels=2;
  pactl set-default-sink headphones; sleep 0.5;
  tone 600 3 | pacat --device=headphones --format=float32le --rate=48000 --channels=2 ) &
./target/release/hidfi-companion --check-sound system 8 /tmp/snd.jsonl
python3 - <<'PY'
import json
rows=[json.loads(l) for l in open('/tmp/snd.jsonl')]
if 'error' in rows[0]: print('FAIL capture:', rows[0]['error']); raise SystemExit(1)
r=[x for x in rows if 'rms' in x]
loud=[x for x in r if x['rms']>0.1]
k=[x for x in loud if abs(x['freq']-1000)<30]
print(('ok  ' if len(k)>=2 else 'FAIL'), 'the computer\'s sound: a 1 kHz tone captured', [(x['freq'], round(x['rms'],2), x['rate'], x['channels']) for x in r])
follow=[x for x in loud if abs(x['freq']-600)<30]
print(('ok  ' if follow else 'NOTE'), 'the default output changed mid-watch: the capture', 'follows it' if follow else 'stays on the first one until reopened')
print(('ok  ' if all(x['age_ms_max']<60 for x in r if x['chunks']) else 'FAIL'), 'chunks arrive fresh: oldest', max(x['age_ms_max'] for x in r), 'ms')
raise SystemExit(0 if len(k)>=2 else 1)
PY
pactl load-module module-sine-source source_name=mic frequency=440 >/dev/null
pactl set-default-source mic
./target/release/hidfi-companion --check-sound mic 3 /tmp/mic.jsonl
python3 - <<'PY'
import json
r=[json.loads(l) for l in open('/tmp/mic.jsonl')]
r=[x for x in r if 'rms' in x]
k=[x for x in r if abs(x['freq']-440)<20 and x['rms']>0.05]
print(('ok  ' if k else 'FAIL'), 'the microphone: the default input captured', [(x['freq'], round(x['rms'],2)) for x in r])
raise SystemExit(0 if k else 1)
PY

step "stream it: real X11 capture, real OpenH264, WebRTC"
HIDFI_REAL_SCREEN=1 ./target/release/examples/serve 1920 1080 30 > /tmp/serve.json 2>/tmp/serve.log &
for i in $(seq 1 40); do [ -s /tmp/serve.json ] && break; sleep 0.25; done
python3 tests/linux/view_e2e.py /tmp/serve.json; rc=$?
grep -E "INFO|WARN|ERROR" /tmp/serve.log | tail -6
exit $rc
