"""Opening HID-Fi Companion while it already runs shows its window - the real
app, end to end (docs/COMPANION_WINDOW.md).

  - the program started a second time (a double-click on the .exe, the Start
    menu, a terminal): the second copy leaves, and the running one shows its
    window (handover.rs, show.request);
  - macOS: the app opened again from Finder, Spotlight or Launchpad - no second
    copy starts; macOS sends the running app a reopen event (desktop.rs).

Until v4.2 both did nothing at all, so a window that would not come forward
left a person with no way in but quitting from the menu bar.

The window's own states (minimized, hidden, behind) are window_life.py's; this
proves the two ways in reach that code. A test build (feature test-ota-key) sees
no USB board (HIDFI_TEST_NO_USB) and asks no release server, so no board on the
desk is touched; the app has a folder of its own and a made-up pairing, so it
starts in the tray without its window, as it does for a person after setup.

Run: python3 companion/tests/opened_again.py   (macOS, Windows)
     python3 companion/tests/opened_again.py path/to/test-build   (built with
     --features test-ota-key elsewhere; nothing is built or restored)
"""
import json
import os
import pathlib
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

COMP = pathlib.Path(__file__).resolve().parents[1]
WIN = os.name == "nt"
MAC = sys.platform == "darwin"
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, detail, flush=True)


def windows_of(pid):
    """(title, on screen and not minimized) for each top-level window of pid, as the OS sees it."""
    if MAC:
        js = ("ObjC.import('CoreGraphics');var l=ObjC.deepUnwrap(ObjC.castRefToObject("
              "$.CGWindowListCopyWindowInfo($.kCGWindowListOptionAll,0)));"
              f"JSON.stringify(l.filter(w=>w.kCGWindowOwnerPID=={pid}&&w.kCGWindowLayer==0)"
              ".map(w=>[w.kCGWindowName||'',!!w.kCGWindowIsOnscreen,w.kCGWindowBounds.Width]))")
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", js], capture_output=True, text=True)
        # Titles are shown only to a program with Screen Recording access (a CI
        # runner has none): then the window is the one as wide as the app's (920).
        return [(t if t else ("HID-Fi Companion" if w == 920 else ""), on) for t, on, w in json.loads(r.stdout or "[]")]
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(h, _):
        p = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid:
            buf = ctypes.create_unicode_buffer(256)
            u.GetWindowTextW(h, buf, 256)
            found.append((buf.value, bool(u.IsWindowVisible(h)) and not u.IsIconic(h)))
        return True

    u.EnumWindows(each, 0)
    return found


def shown(pid, wait):
    end = time.time() + wait
    while True:
        w = [t for t, on in windows_of(pid) if on and t == "HID-Fi Companion"]
        if w or time.time() > end:
            return bool(w)
        time.sleep(0.25)


def instance(home):
    try:
        return json.loads((home / "instance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def started(home, wait=20):
    end = time.time() + wait
    while time.time() < end:
        if instance(home).get("pid"):
            return instance(home)["pid"]
        time.sleep(0.25)
    return None


def quit_app(home, pid):
    # The way a newer copy asks it to go: the app quits as its own Quit does.
    (home / "quit.request").write_text("99.0.0", encoding="utf-8")
    end = time.time() + 10
    while time.time() < end and alive(pid):
        time.sleep(0.25)
    if alive(pid):  # only our own copy, by its pid
        os.kill(pid, 9)


def alive(pid):
    if WIN:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True)
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


exe = COMP / "target" / "release" / ("hidfi-companion.exe" if WIN else "hidfi-companion")
# A test build made elsewhere (the Windows VM has no Rust): run it, build nothing.
GIVEN = [a for a in sys.argv[1:] if not a.startswith("--")]
if GIVEN:
    exe = pathlib.Path(GIVEN[0])
    sys.argv.append("--no-build")
# tests/vectors/ota_v1.json's public TEST key: a build that trusts it is never shipped.
TEST_KEY = b"0446f73c2faa078cc0ed394029be39afcda7fd03afaa8506813679dc7a659057"
BUILD = ["cargo", "build", "-q", "--release", "-p", "hidfi-companion"]


def build_normal():
    """The test build replaced the app in target/release, where CI packages the
    installers from: build the real one back, and prove it does not trust the
    TEST key. (Only the one crate changes: a relink, seconds.)"""
    b = subprocess.run(BUILD, cwd=COMP)
    clean = b.returncode == 0 and TEST_KEY not in exe.read_bytes()
    check("target/release holds the normal build again (no TEST key)", clean)


if "--no-build" not in sys.argv:
    b = subprocess.run(BUILD + ["--features", "test-ota-key"], cwd=COMP)
    if b.returncode != 0:
        # Never run what an earlier build left behind (docs/TESTING.md).
        print("FAIL the test build did not build")
        build_normal()
        sys.exit(1)

work = pathlib.Path(tempfile.mkdtemp(prefix="hidfi-opened-again-"))


def fresh_home(name):
    home = work / name
    home.mkdir()
    # A made-up pairing: set up already, so the app starts without its window.
    (home / "pairing-0000000000A1.key").write_text("ab" * 32, encoding="utf-8")
    return home


def env_for(home):
    return dict(os.environ, HIDFI_HOME=str(home), HIDFI_NO_AUTOSTART="1", HIDFI_TEST_NO_USB="1",
                HIDFI_RELEASES="http://127.0.0.1:9/")


def scenarios():
    # 1. The program started again.
    home = fresh_home("again")
    first = subprocess.Popen([str(exe)], env=env_for(home), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pid = started(home)
    try:
        check("the app starts in the tray", pid == first.pid, instance(home))
        time.sleep(2)
        check("with no window (set up already)", not shown(first.pid, 0), windows_of(first.pid))
        second = subprocess.run([str(exe)], env=env_for(home), timeout=30, capture_output=True)
        check("a second start leaves", second.returncode == 0)
        check("and the running app shows its window", shown(first.pid, 5), windows_of(first.pid))
        check("the same copy still runs", alive(first.pid) and instance(home).get("pid") == first.pid)
        third = subprocess.run([str(exe)], env=env_for(home), timeout=30, capture_output=True)
        check("starting it once more leaves too, the window still shown",
              third.returncode == 0 and shown(first.pid, 2) and alive(first.pid))
    finally:
        quit_app(home, first.pid)

    # 2. macOS: opened again from Finder - a reopen event, no second copy.
    if MAC:
        app = work / "HID-Fi Opened Again Test.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        shutil.copy2(exe, app / "Contents" / "MacOS" / "hidfi-companion")
        # Its own bundle id: `open` must never reach the person's installed app.
        with open(app / "Contents" / "Info.plist", "wb") as f:
            plistlib.dump({"CFBundleName": "HID-Fi Opened Again Test",
                           "CFBundleIdentifier": f"in.hidfi.companion.test-opened-again-{os.getpid()}",
                           "CFBundleExecutable": "hidfi-companion", "CFBundlePackageType": "APPL",
                           "LSUIElement": True}, f)
        home = fresh_home("reopen")
        env_args = sum([["--env", f"{k}={v}"] for k, v in env_for(home).items() if k.startswith("HIDFI_")], [])
        subprocess.run(["open", "-g", *env_args, str(app)], check=True)
        pid = started(home)
        try:
            check("macOS: the app opened from Finder starts in the tray", pid is not None)
            time.sleep(2)
            check("macOS: with no window", pid and not shown(pid, 0), pid and windows_of(pid))
            subprocess.run(["open", str(app)], check=True)
            check("macOS: opening it again shows the window", pid and shown(pid, 5), pid and windows_of(pid))
            n = subprocess.run(["pgrep", "-f", str(app / "Contents" / "MacOS")], capture_output=True, text=True)
            check("macOS: and no second copy started", n.stdout.split() == [str(pid)], n.stdout.split())
        finally:
            if pid:
                quit_app(home, pid)


try:
    scenarios()
finally:
    shutil.rmtree(work, ignore_errors=True)
    if "--no-build" not in sys.argv:
        build_normal()
sys.exit(0 if ok else 1)
