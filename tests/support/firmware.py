"""Read facts out of the firmware source, and compile slices of it on the host.

The sketch is the single source of truth: tests ask it for its command table,
version and build settings instead of keeping copies that drift.
"""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from support.paths import CACHE, FLASH_SH, INO

TYPE_BEGIN = "// ==================== TYPE STRING ===================="
TYPE_END = "// ==================== LOCK ===================="


def ino_text():
    return INO.read_text(encoding="utf-8")


def section(begin, end, text=None):
    """The source between two banner comments. Fails loudly if a banner moved,
    because a silently empty slice would make every test built on it vacuous."""
    text = text or ino_text()
    if text.count(begin) != 1 or text.count(end) != 1:
        raise AssertionError(f"firmware banner missing or duplicated: {begin!r} / {end!r} - "
                             "a test slices the sketch at these comments; update tests/support/firmware.py if they were renamed")
    a = text.index(begin)
    b = text.index(end, a)
    return text[a:b]


def fw_version(text=None):
    m = re.search(r'#define\s+FW_VERSION\s+"([^"]+)"', text or ino_text())
    return m.group(1) if m else None


def dispatch_commands(text=None):
    """Every command name processCommand() dispatches on."""
    return sorted(set(re.findall(r'strcmp\(cmd,\s*"([a-z_0-9]+)"\)\s*==\s*0', text or ino_text())))


def fqbn():
    """The build settings, read from flash_esp.sh so they cannot drift from the flasher."""
    m = re.search(r"^FQBN='([^']+)'", FLASH_SH.read_text(), re.M)
    if not m:
        raise AssertionError("FQBN not found in flash_esp.sh")
    return m.group(1)


# ---- the installed ESP32 core ---------------------------------------------------
def _cli_json(*args):
    out = subprocess.run(["arduino-cli", *args, "--format", "json"], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    return json.loads(out.stdout or "{}")


def arduino_data_dir():
    out = subprocess.run(["arduino-cli", "config", "get", "directories.data"], capture_output=True, text=True, timeout=30)
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None


def installed_core_version():
    for p in _cli_json("core", "list").get("platforms", []):
        if p.get("id") == "esp32:esp32":
            return p.get("installed_version")
    return None


def core_usb_src():
    """Path to the installed core's USB HID sources (USBHIDKeyboard.cpp lives here)."""
    data, ver = arduino_data_dir(), installed_core_version()
    if not data or not ver:
        return None, None
    p = data / "packages" / "esp32" / "hardware" / "esp32" / ver / "libraries" / "USB" / "src"
    return (p if (p / "USBHIDKeyboard.cpp").exists() else None), ver


# ---- host C++ builds -------------------------------------------------------------
def host_cxx():
    for c in ("clang++", "g++"):
        if shutil.which(c):
            return c
    return None


def build_host(name, main_cpp, include_dirs=(), extra_inputs=(), flags=("-std=c++17", "-O2")):
    """Compile a host binary into tests/.cache, rebuilding only when an input changed."""
    cxx = host_cxx()
    if not cxx:
        raise RuntimeError("no C++ compiler (clang++ or g++) on PATH")
    CACHE.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    for f in [Path(main_cpp), *map(Path, extra_inputs)]:
        h.update(f.read_bytes())
    h.update(" ".join(map(str, include_dirs)).encode())
    h.update(" ".join(flags).encode())
    exe = CACHE / f"{name}-{h.hexdigest()[:12]}"
    if not exe.exists():
        cmd = [cxx, *flags, "-w", *[f"-I{d}" for d in include_dirs], "-o", str(exe), str(main_cpp)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"host build of {name} failed:\n{r.stderr[-4000:]}")
        for old in CACHE.glob(f"{name}-*"):          # keep the cache to one binary per target
            if old != exe and old.is_file():
                old.unlink(missing_ok=True)
    return exe
