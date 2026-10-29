"""The Companion's window in this OS's own web view - WKWebView on macOS,
WebView2 on Windows - not a test browser (docs/COMPANION_WINDOW.md).

Runs the built app with `--window-check`: it draws every state its own model
produces at the window's size and at its narrowest, selects a card, opens its
details, copies its MAC, clicks Open dashboard, and draws fifty boards twenty
times. This judges the report: nothing scrolls sideways or is cut off, every
button at least 34 px, no script error, each click reached the app, and the
time to draw stays small.

Run: python3 companion/tests/window_real.py [path/to/hidfi-companion]
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


out = pathlib.Path(tempfile.mkdtemp(prefix="hidfi-window-")) / "check.json"
env = dict(os.environ, HIDFI_HOME=str(out.parent / "home"), HIDFI_NO_AUTOSTART="1")
r = subprocess.run([exe, "--window-check", str(out)], env=env, timeout=120, capture_output=True, text=True)
check("the app drew its window and reported", r.returncode == 0 and out.exists(), r.stderr[-400:])
if not out.exists():
    sys.exit(1)
rep = json.loads(out.read_text(encoding="utf-8"))
p1, p2 = rep.get("pass1", {}), rep.get("pass2", {})
print("engine:", p1.get("engine", "?"))
for name, p in (("at the window's size", p1), ("at its narrowest", p2)):
    fits = p.get("fits", [])
    bad = [f for f in fits if f["side"] > 0 or f["cut"] or f["small"]]
    check(f"every state fits {name} ({fits[0]['w'] if fits else '?'} px)", fits and not bad, bad[:3])
    check(f"no script error {name}", not p.get("errors"), p.get("errors"))
    # The page's very first draw also compiles its script and lays out its fonts
    # (a cold start, once per window); every draw after it is what a person
    # waits for on each change.
    if fits:
        first, rest = fits[0], fits[1:] if name == "at the window's size" else fits
        if name == "at the window's size":
            check("the first draw, cold, in under 150 ms", first["ms"] < 150, f"{first['name']}: {first['ms']} ms")
        slow = max(rest, key=lambda f: f["ms"]) if rest else first
        check(f"each state draws in under 40 ms {name}", slow["ms"] < 40, f"slowest {slow['name']}: {slow['ms']} ms")
check("a click selects the card", p1.get("selected") == 1, p1.get("selected"))
check("Details opens", p1.get("open") is True)
check("copying asks the app to copy (no focus rules apply)", str(p1.get("toast", "")).startswith("Copied DC:B4:D9:"),
      p1.get("toast"))
check("the copy and the click reached the app", rep.get("acts") == ["copy:DC:B4:D9:09:F7:C8", "act:open:DCB4D909F7C8"],
      rep.get("acts"))
check("fifty boards draw in under 40 ms", p1.get("fifty_first", 99) < 40, f"{p1.get('fifty_first')} ms")
check("and update in under 12 ms", p1.get("fifty_update_worst", 99) < 12, f"worst {p1.get('fifty_update_worst')} ms")
sys.exit(0 if ok else 1)
