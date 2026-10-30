"""The Companion's window, asked for in every state a person can leave it in -
on this OS, through the app's own code (docs/COMPANION_WINDOW.md).

Runs the built app with `--window-life`: it opens the window, then for each way
(never opened, minimized, hidden, behind another window, the whole app hidden on
macOS, closed, minimized again) puts it there for real and asks for it the way
"Open HID-Fi Companion..." does. This judges the OS's answer: the window is on
screen, not minimized, and in front, within a few seconds.

v4.1 and v4.2 failed "minimized": Open did nothing until the app was quit.

Run: python3 companion/tests/window_life.py [path/to/hidfi-companion]
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
exe = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "target" / "release" / (
    "hidfi-companion.exe" if os.name == "nt" else "hidfi-companion"))
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, detail, flush=True)


out = pathlib.Path(tempfile.mkdtemp(prefix="hidfi-window-life-")) / "life.json"
env = dict(os.environ, HIDFI_HOME=str(out.parent / "home"), HIDFI_NO_AUTOSTART="1")
p = subprocess.Popen([exe, "--window-life", str(out)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True)
try:
    _, err = p.communicate(timeout=120)
except subprocess.TimeoutExpired:
    p.kill()
    _, err = p.communicate()
    # Each step is on stderr: the last one is where it stopped.
    lines = err.splitlines()
    beat = [ln for ln in lines if "watchdog" in ln]
    trace = [ln for ln in lines if "watchdog" not in ln]
    check("the window's life ran to its end within 120 s", False,
          "\n" + "\n".join(trace[-40:] + beat[-1:]))
    sys.exit(1)
ran = p.returncode == 0 and out.exists()
check("the app ran the window's life and reported", ran, "" if ran else err[-400:])
if not out.exists():
    sys.exit(1)
rep = json.loads(out.read_text(encoding="utf-8"))
want = ["NotOpen", "Minimized", "Hidden", "Behind"] + (["AppHidden"] if sys.platform == "darwin" else []) + [
    "Closed", "Minimized"]
check("every way was tried", [x["way"] for x in rep] == want, [x["way"] for x in rep])
for x in rep:
    if "error" in x:
        check(f"{x['way']}: set up", False, x["error"])
        continue
    # "front": in front and taking input, as the OS says (Windows: the foreground window).
    good = x["open"] and x["visible"] and not x["minimized"] and x["front"]
    check(f"{x['way']}: Open brings the window to the front", good,
          {k: x.get(k) for k in ("visible", "minimized", "front", "focused", "ms")})
sys.exit(0 if ok else 1)
