"""macOS: a grant kept for an older signature of the app is cleared once, so
Screen Recording is not asked for again and again (crates/hidfi-companion/src/
permissions.rs) - the real app, end to end, under a bundle id of its own.

Seen on the owner's Mac (2026-10-30): grants made to a "HID-Fi Local" build
stayed after the release (another certificate) replaced it. System Settings
showed HID-Fi Companion switched on, macOS answered "Failed to match existing
code requirement", and the app asked again on every start.

  1. a first start, Screen Recording not working: the app resets its own entries
     once (tccd logs the deletes for this bundle id - never in.hidfi.companion)
     and records its signature;
  2. started again, same signature: nothing is reset;
  3. signed anew (another signature, as an update to a new certificate is):
     reset once more.

A test build (feature test-ota-key) sees no USB board; the normal build is put
back in target/release afterwards and checked for the TEST key.

Run: python3 companion/tests/permissions_mac.py
"""
import os
import pathlib
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time

if sys.platform != "darwin":
    print("skip: macOS only")
    sys.exit(0)

COMP = pathlib.Path(__file__).resolve().parents[1]
exe = COMP / "target" / "release" / "hidfi-companion"
TEST_KEY = b"0446f73c2faa078cc0ed394029be39afcda7fd03afaa8506813679dc7a659057"
BUILD = ["cargo", "build", "-q", "--release", "-p", "hidfi-companion"]
ID = f"in.hidfi.companion.test-permissions-{os.getpid()}"
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, detail, flush=True)


def build_normal():
    b = subprocess.run(BUILD, cwd=COMP)
    check("target/release holds the normal build again (no TEST key)",
          b.returncode == 0 and TEST_KEY not in exe.read_bytes())


if subprocess.run(BUILD + ["--features", "test-ota-key"], cwd=COMP).returncode != 0:
    print("FAIL the test build did not build")
    build_normal()
    sys.exit(1)

work = pathlib.Path(tempfile.mkdtemp(prefix="hidfi-permissions-"))
app = work / "HID-Fi Permissions Test.app"
home = work / "home"
calls = work / "tccutil-calls.txt"
# Records each call, then runs the real tccutil: what is asked, and that it is done.
standin = work / "tccutil"
standin.write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\nexec /usr/bin/tccutil "$@"\n', encoding="utf-8")
standin.chmod(0o755)


def make_app(extra=None):
    (app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, app / "Contents" / "MacOS" / "hidfi-companion")
    info = {"CFBundleName": "HID-Fi Permissions Test", "CFBundleIdentifier": ID,
            "CFBundleExecutable": "hidfi-companion", "CFBundlePackageType": "APPL", "LSUIElement": True}
    info.update(extra or {})
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump(info, f, fmt=plistlib.FMT_XML)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)


def requirement():
    r = subprocess.run(["codesign", "-d", "-r", "-", str(app)], capture_output=True, text=True)
    return next((ln.split("designated => ", 1)[1].strip() for ln in (r.stdout + r.stderr).splitlines()
                 if "designated => " in ln), None)


def cleared_lines():
    try:
        return [ln for ln in (home / "companion.log").read_text(encoding="utf-8").splitlines()
                if "permissions: this app's signature changed" in ln]
    except OSError:
        return []


def run_once(label):
    """Start the app through macOS (a bundle, as a person starts it), let the
    check run, quit it the way an update asks it to."""
    for f in ("instance.json", "quit.request"):
        (home / f).unlink(missing_ok=True)
    env = ["--env", f"HIDFI_TEST_TCCUTIL={standin}", "--env", f"HIDFI_HOME={home}", "--env", "HIDFI_NO_AUTOSTART=1", "--env", "HIDFI_TEST_NO_USB=1",
           "--env", "HIDFI_RELEASES=http://127.0.0.1:9/"]
    subprocess.run(["open", "-n", "-g", *env, str(app)], check=True)
    pid, end = None, time.time() + 20
    while time.time() < end and pid is None:
        try:
            import json
            pid = json.loads((home / "instance.json").read_text(encoding="utf-8"))["pid"]
        except (OSError, ValueError, KeyError):
            time.sleep(0.25)
    check(f"{label}: the app starts", pid is not None)
    time.sleep(4)  # the check runs off the main thread: codesign, then tccutil
    (home / "quit.request").write_text("99.0.0", encoding="utf-8")
    end = time.time() + 10
    while pid and time.time() < end:
        try:
            os.kill(pid, 0)
            time.sleep(0.25)
        except OSError:
            break
    else:
        if pid:
            os.kill(pid, 9)


def resets():
    """What the app asked tccutil for, through the stand-in: [(service, bundle id)]."""
    try:
        return [tuple(ln.split()[1:3]) for ln in calls.read_text(encoding="utf-8").splitlines()
                if ln.startswith("reset ")]
    except OSError:
        return []


try:
    home.mkdir(parents=True)
    # Set up already (a made-up pairing): no first-run window.
    (home / "pairing-0000000000A1.key").write_text("ab" * 32, encoding="utf-8")
    make_app()
    run_once("first start")
    first = requirement()
    check("first start, not allowed: its own entries are reset once", len(cleared_lines()) == 1, cleared_lines())
    check("and the signature is recorded", (home / "signature.txt").read_text().strip() == first)
    run_once("started again")
    check("started again, same signature: nothing reset", len(cleared_lines()) == 1, cleared_lines())
    make_app({"HIDFiTestSignature": "another"})  # another code directory: another signature
    check("signed anew: the signature differs", requirement() != first)
    run_once("signed anew")
    check("signed anew: reset once more", len(cleared_lines()) == 2, cleared_lines())
    check("and the new signature is recorded", (home / "signature.txt").read_text().strip() == requirement())
    want = [(svc, ID) for svc in ("ScreenCapture", "AudioCapture", "Microphone")]
    check("each reset asked the real tccutil for this app's three services, twice in all", resets() == want * 2,
          resets())
    check("and never for the installed HID-Fi Companion", all(i == ID for _, i in resets()))
finally:
    shutil.rmtree(work, ignore_errors=True)
    build_normal()
sys.exit(0 if ok else 1)
