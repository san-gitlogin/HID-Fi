"""The firmware builds, with the pinned dependencies, inside its size budget.

Size is ratcheted against golden/build_size.json: growth beyond the tolerance
fails until someone decides it is worth it (--update-golden). Compiler warnings
in HID-Fi's own files are ratcheted the same way.
"""
import json
import re
import subprocess

import pytest

from support import deps, firmware, paths

pytestmark = pytest.mark.build

FLASH_TOLERANCE = 0.03       # +3% program size before a deliberate update is required
RAM_TOLERANCE_BYTES = 4096   # static RAM
APP_PARTITION_LIMIT = 0.80   # never use more than 80% of an app slot (hid_fi/partitions.csv) -
                             # arduino-cli's own "Maximum" is the whole flash with PartitionScheme=custom


@pytest.fixture(scope="module")
def build():
    out = paths.CACHE / "firmware"
    bp = paths.CACHE / "firmware-buildpath"
    out.mkdir(parents=True, exist_ok=True)
    # Recompile the sketch every time (its warnings are only printed when it is
    # compiled) but keep the core's objects - those take minutes and never change.
    import hashlib, shutil
    shutil.rmtree(bp / "sketch", ignore_errors=True)
    # build_opt.h's flags reach the libraries too: a change means their cached
    # objects are stale (and would disagree with the sketch), so start over.
    opt = hashlib.sha256((paths.SKETCH / "build_opt.h").read_bytes()).hexdigest()
    stamp = bp / ".build_opt.sha256"
    if not stamp.exists() or stamp.read_text().strip() != opt:
        shutil.rmtree(bp, ignore_errors=True)
        bp.mkdir(parents=True)
        stamp.write_text(opt)
    r = subprocess.run(["arduino-cli", "compile", "--fqbn", firmware.fqbn(), "--warnings", "default",
                        "--build-path", str(bp), "--output-dir", str(out), str(paths.SKETCH)],
                       capture_output=True, text=True, timeout=1800)
    return r


def test_installed_versions_match_pins(request):
    rows = [r for r in deps.check() if r[0] in ("esp32:esp32", "ArduinoJson", "WebSockets") and r[3] != "ok"]
    if rows:
        msg = "; ".join(f"{n} installed {have}, pinned {want}" for n, want, have, _ in rows)
        if request.config.getoption("--strict-deps"):
            pytest.fail(f"firmware dependencies differ from tests/deps.json: {msg}", pytrace=False)
        pytest.skip(f"building with UNPINNED versions ({msg}) - results describe that build, see docs/TESTING.md#upgrading-a-dependency")


def test_firmware_compiles(build):
    assert build.returncode == 0, (build.stdout + build.stderr)[-5000:]
    assert (paths.CACHE / "firmware" / "hid_fi.ino.bin").exists()


def _sizes(stdout):
    f = re.search(r"Sketch uses (\d+) bytes \((\d+)%\) of program storage space\. Maximum is (\d+)", stdout)
    r = re.search(r"Global variables use (\d+) bytes", stdout)
    assert f and r, "could not read the size report from arduino-cli"
    return int(f.group(1)), int(f.group(3)), int(r.group(1))


def test_size_budget(build, golden, request):
    assert build.returncode == 0, "build failed"
    flash, _, ram = _sizes(build.stdout)
    slot = firmware.app_slot_size()
    assert flash <= APP_PARTITION_LIMIT * slot, \
        f"firmware uses {flash}/{slot} bytes - over {APP_PARTITION_LIMIT:.0%} of an app slot; an update must fit the other slot"
    f = paths.GOLDEN / "build_size.json"
    if request.config.getoption("--update-golden") or not f.exists():
        golden("build_size", {"flash_bytes": flash, "ram_bytes": ram, "core": firmware.installed_core_version()})
        return
    base = json.loads(f.read_text())
    grew = []
    if flash > base["flash_bytes"] * (1 + FLASH_TOLERANCE):
        grew.append(f"program {base['flash_bytes']} -> {flash} bytes (+{(flash / base['flash_bytes'] - 1):.1%})")
    if ram > base["ram_bytes"] + RAM_TOLERANCE_BYTES:
        grew.append(f"static RAM {base['ram_bytes']} -> {ram} bytes")
    assert not grew, "firmware grew past its budget: " + "; ".join(grew) + \
        " - if that is intended, rerun with --update-golden and say why in the PR"


def test_no_new_compiler_warnings_in_hidfi_code(build, golden):
    text = build.stdout + build.stderr
    def norm(l):
        l = re.sub(r"^.*?(hid_fi/(?:hid_fi\.ino|web_ui\.h))", r"\1", l.strip())   # path relative to the repo
        l = re.sub(r":\d+:\d+:", ":", l)                                          # line numbers move
        return re.sub(r"ArduinoJson::V\w+::", "ArduinoJson::", l)                 # version-stamped namespace
    assert "hid_fi.ino" in text or "Compiling sketch" in text or build.returncode == 0
    ours = sorted({norm(l) for l in text.splitlines()
                   if "warning:" in l and ("hid_fi.ino" in l or "web_ui.h" in l)})
    assert ours or "warning" not in text, "warnings were printed but none were attributed to HID-Fi files - check norm()"
    golden("build_warnings", ours)
