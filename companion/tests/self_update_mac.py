"""The Companion updating itself on a Mac, end to end (docs/COMPANION_WINDOW.md):

  an "installed" HID-Fi Companion.app (this version) in a folder of its own,
  a release offering a newer one (a disk image, signed manifest - the TEST key),
  -> the app finds it, downloads it, checks it against the signed manifest,
     installs it in its own place and starts it; the new one takes over and the
     old one quits; what the update replaced is removed; the app's folder
     (pairings, settings) is untouched.

The buttons are pressed by the test build itself (HIDFI_TEST_AUTO_UPDATE; the
window tests prove the buttons send these commands). Everything else is the
real app: no USB board is seen (HIDFI_TEST_NO_USB), so no real board on the desk
is asked to pair. Builds the old and the new app (a few minutes the first time).

Run: python3 companion/tests/self_update_mac.py
"""
import http.server
import json
import os
import pathlib
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

COMP = pathlib.Path(__file__).resolve().parents[1]
REPO = COMP.parent
NEW_VERSION = "4.99.0"
sys.path.insert(0, str(REPO / "tools"))
import ota_release as R  # noqa: E402

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, detail, flush=True)


def version_of_workspace():
    for line in (COMP / "Cargo.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version"):
            return line.split('"')[1]


def bundle(binary, version, where):
    app = where / "HID-Fi Companion.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    shutil.copy(binary, app / "Contents" / "MacOS" / "hidfi-companion")
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleIdentifier": "in.hidfi.companion", "CFBundleExecutable": "hidfi-companion",
                       "CFBundleName": "HID-Fi Companion", "CFBundlePackageType": "APPL",
                       "CFBundleShortVersionString": version, "CFBundleVersion": version,
                       "LSUIElement": True, "NSHighResolutionCapable": True}, f)
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True)
    return app


def build(target_dir, src):
    env = dict(os.environ, CARGO_TARGET_DIR=str(target_dir))
    subprocess.run(["cargo", "build", "-q", "--release", "-p", "hidfi-companion", "--features", "test-ota-key"],
                   cwd=src, env=env, check=True)
    return target_dir / "release" / "hidfi-companion"


work = pathlib.Path(tempfile.mkdtemp(prefix="hidfi-selfupdate-"))
print("work:", work)
old_version = version_of_workspace()

# The installed copy: this version, in a folder of its own.
old_bin = build(COMP / "target" / "selfupdate-old", COMP)
installed = bundle(old_bin, old_version, work / "Applications")

# The new version: the same sources, numbered higher.
src = work / "src"
shutil.copytree(COMP, src, ignore=shutil.ignore_patterns("target", "__pycache__"))
(src / "Cargo.toml").write_text((src / "Cargo.toml").read_text(encoding="utf-8")
                                .replace(f'version = "{old_version}"', f'version = "{NEW_VERSION}"', 1), encoding="utf-8")
new_bin = build(COMP / "target" / "selfupdate-new", src)
stage = work / "dmg"
bundle(new_bin, NEW_VERSION, stage)
os.symlink("/Applications", stage / "Applications")
rel = work / "release"
rel.mkdir()
dmg = rel / f"HID-Fi-Companion-{NEW_VERSION}-macos.dmg"
subprocess.run(["hdiutil", "create", "-quiet", "-volname", "HID-Fi Companion", "-srcfolder", str(stage),
                "-ov", "-format", "UDZO", str(dmg)], check=True)
(rel / "hidfi-companion-windows-x86_64.exe").write_bytes(b"not used here")

# The release, signed with the TEST key the test build trusts.
vec = json.loads((REPO / "tests" / "vectors" / "ota_v1.json").read_text(encoding="utf-8"))
from cryptography.hazmat.primitives import serialization  # noqa: E402
key = serialization.load_pem_private_key(vec["test_private_key_pem"].encode(), password=None)
img = (REPO / "hid_fi" / "build" / "hid_fi.ino.bin").read_bytes()
table = (REPO / "hid_fi" / "build" / "hid_fi.ino.partitions.bin").read_bytes()
comp = {"version": NEW_VERSION, "assets": R.companion_assets(rel, NEW_VERSION)}
m = R.canonical(R.build_manifest("4.1", img, "4.0.4", "3.3.12", "5.5.5", "## v4.1 - 2026-10-29\n\nA test release.\n",
                                 companion=comp, table_bytes=table, image_name="hid_fi-4.1.bin",
                                 _tests_allow_test_build=True))
(rel / "hidfi-ota.json").write_bytes(m)
(rel / "hidfi-ota.sig").write_text(json.dumps(R.sign(m, key)), encoding="utf-8")
asked = []


class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(rel), **k)

    def log_message(self, *a):
        asked.append(self.path)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

home = work / "home"
home.mkdir()
(home / "keep.me").write_text("pairings and settings live here", encoding="utf-8")
env = dict(os.environ, HIDFI_HOME=str(home), HIDFI_RELEASES=f"http://127.0.0.1:{srv.server_port}/",
           HIDFI_NO_AUTOSTART="1", HIDFI_TEST_AUTO_UPDATE="1", HIDFI_TEST_NO_USB="1", RUST_LOG="info")
old = subprocess.Popen([str(installed / "Contents" / "MacOS" / "hidfi-companion")], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
inst, t0 = {}, time.time()
try:
    while time.time() - t0 < 120:
        try:
            inst = json.loads((home / "instance.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            inst = {}
        if inst.get("version") == NEW_VERSION and old.poll() is not None:
            break
        time.sleep(0.5)
    took = time.time() - t0
    check("the app found the release and fetched the new version", any(p.endswith(".dmg") for p in asked), asked)
    check("the new version runs", inst.get("version") == NEW_VERSION and inst.get("pid") != old.pid,
          f"{inst} after {took:.0f} s")
    check("the old one quit by itself", old.poll() is not None)
    with open(installed / "Contents" / "Info.plist", "rb") as f:
        v = plistlib.load(f)["CFBundleShortVersionString"]
    check("the app in its own place is the new version", v == NEW_VERSION, v)
    for _ in range(20):
        if not (work / "Applications" / "HID-Fi Companion.app.old").exists():
            break
        time.sleep(0.5)
    check("what it replaced is gone", not (work / "Applications" / "HID-Fi Companion.app.old").exists())
    check("nothing else was left beside it", sorted(p.name for p in (work / "Applications").iterdir()) == ["HID-Fi Companion.app"],
          sorted(p.name for p in (work / "Applications").iterdir()))
    check("the app's folder is untouched", (home / "keep.me").read_text(encoding="utf-8") == "pairings and settings live here")
    check("the new copy's signature holds", subprocess.run(["codesign", "--verify", "--deep", "--strict", str(installed)],
                                                           capture_output=True).returncode == 0)
    mounts = subprocess.run(["hdiutil", "info"], capture_output=True, text=True).stdout
    check("the disk image was put away", dmg.name not in mounts and "hidfi-update-" not in mounts)
finally:
    pid = inst.get("pid")
    if pid and pid != old.pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if old.poll() is None:
        old.terminate()
    srv.shutdown()
print("took", f"{time.time() - t0:.0f} s from start to the new version running")
sys.exit(0 if ok else 1)
