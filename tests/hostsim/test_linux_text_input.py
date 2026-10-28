"""Linux text input with the firmware's exact keystrokes, no board needed.

The unit tier's bench records the HID reports the real typing code produces;
this replays them into a real X server, through a real input method (IBus, as
on GNOME), into a real GTK text box - and, separately, GTK's built-in input
(no IBus). It is the closest thing to a Linux desktop that CI can run on every
push. The board-to-kernel half is covered on real hardware by
tests/e2e/test_linux_evdev.py.

Needs Linux with: Xvfb, ibus, python3-gi + gir1.2-gtk-3.0, python3-xlib for the
SYSTEM python, x11-xkb-utils. (CI installs them; see .github/workflows/tests.yml.)
"""
import json
import os
import platform
import shutil
import subprocess
import time

import pytest

from support import corpus, linux_keys, paths

# timing: live typing into IBus misses keys when it shares the CPU with the
# parallel dashboard workers (it did on GitHub's 4-CPU runner, 23 cases); run.py
# runs it alone, after the parallel phase.
pytestmark = [pytest.mark.hostsim, pytest.mark.timing]

SYS_PY = "/usr/bin/python3"
MODES = ["linux_off", "linux_on", "linux_caps"]
LABELS = [k for k in corpus.CORPUS if k not in corpus.HOST_UNTYPEABLE]


def _missing():
    if platform.system() != "Linux":
        return "Linux only (replays into Xvfb + IBus + GTK) - runs in CI"
    for tool in ("Xvfb", "ibus-daemon", "setxkbmap", "dbus-launch"):
        if not shutil.which(tool):
            return f"{tool} not installed (apt install xvfb ibus ibus-gtk3 x11-xkb-utils dbus-x11)"
    r = subprocess.run([SYS_PY, "-c", "import gi, Xlib; gi.require_version('Gtk','3.0')"], capture_output=True)
    if r.returncode:
        return "system python lacks GTK/Xlib (apt install python3-gi gir1.2-gtk-3.0 python3-xlib)"
    return None


def _session(input_method, cases_file, out_file, table_file):
    env = dict(os.environ, DISPLAY=":97")
    procs = [subprocess.Popen(["Xvfb", ":97", "-screen", "0", "1024x768x24", "-nolisten", "tcp"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    try:
        time.sleep(1.5)
        subprocess.run(["setxkbmap", "us"], env=env, check=True)
        dbus = subprocess.run(["dbus-launch", "--sh-syntax"], env=env, capture_output=True, text=True).stdout
        for line in dbus.splitlines():
            if line.startswith("DBUS_SESSION_BUS_ADDRESS="):
                env["DBUS_SESSION_BUS_ADDRESS"] = line.split("=", 1)[1].rstrip(";").strip("'")
        if input_method == "ibus":
            env.update(GTK_IM_MODULE="ibus", XMODIFIERS="@im=ibus")
            subprocess.run(["ibus-daemon", "-drx"], env=env)
            time.sleep(3)
        else:
            env["GTK_IM_MODULE"] = "gtk-im-context-simple"
        r = subprocess.run([SYS_PY, str(paths.TESTS / "hostsim" / "x11" / "replay.py"), str(cases_file),
                            str(table_file), str(out_file)], env=env, capture_output=True, text=True, timeout=1800)
        if r.returncode:
            raise RuntimeError(r.stderr[-3000:])
        return json.loads(out_file.read_text(encoding="utf-8"))
    finally:
        subprocess.run(["pkill", "-f", "ibus-daemon"], capture_output=True)
        for p in procs:
            p.terminate()


@pytest.fixture(scope="module")
def replayed():
    why = _missing()
    if why:
        pytest.skip(why)
    paths.CACHE.mkdir(parents=True, exist_ok=True)
    cf, tf = paths.CACHE / "hostsim_cases.json", paths.CACHE / "hostsim_table.json"
    recorded = os.environ.get("HIDFI_HOSTSIM_CASES")
    if recorded:
        # Traces recorded on another machine (one with the ESP32 core installed):
        #   python tests/hostsim/record.py > cases.json
        cf.write_text(open(recorded).read(), encoding="utf-8")
    else:
        from unit.test_typing import build_bench, _dump
        exe, _ = build_bench()
        rows = [(label, corpus.CORPUS[label].encode(), mode) for mode in MODES for label in LABELS]
        traces = _dump(exe, rows)
        cases = [{"id": f"{d['mode']}/{d['label']}", "ev": d["ev"], "caps": d["mode"] == "linux_caps"} for d in traces]
        cf.write_text(json.dumps(cases), encoding="utf-8")
    tf.write_text(json.dumps(linux_keys.kernel_hid_keyboard()), encoding="utf-8")
    return {im: _session(im, cf, paths.CACHE / f"hostsim_{im}.json", tf) for im in ("ibus", "gtk-simple")}


def _expected(label, mode):
    off, _, on, _ = corpus.expect(corpus.CORPUS[label].encode())
    text = off if mode == "linux_off" else on
    if mode == "linux_caps":   # Caps Lock flips plain letters on any keyboard; Unicode entry is unaffected
        text = "".join(c.swapcase() if c.isascii() and c.isalpha() else c for c in text)
    return text


@pytest.mark.parametrize("im", ["ibus", "gtk-simple"])
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("label", LABELS)
def test_linux_desktop_receives_exactly(replayed, im, mode, label):
    got = replayed[im].get(f"{mode}/{label}", {})
    assert "error" not in got, got.get("error")
    assert got.get("text") == _expected(label, mode)
