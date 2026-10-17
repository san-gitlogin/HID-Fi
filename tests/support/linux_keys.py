"""What each firmware key name must arrive as on Linux, derived - never typed by hand.

  firmware mapKeyName()     name  -> KEY_* constant        (hid_fi/hid_fi.ino)
  ESP32 core header         KEY_* -> value                 (USBHIDKeyboard.h)
  Arduino convention        value -> HID usage             (>= 0x88: usage + 0x88; 0x80-0x87: modifiers 0xE0-0xE7)
  Linux hid-input.c         usage -> evdev keycode         (hid_keyboard[], fetched from kernel.org and cached)

The kernel table is downloaded rather than copied into this repository because it
comes from a GPL-2.0 source file. Consumer and System Control mappings are the
cases of hidinput_configure_usage(), checked against the same file.
"""
import re
import urllib.request

from support import firmware, paths

KERNEL_URL = "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/plain/drivers/hid/hid-input.c?h=linux-6.1.y"

# evdev codes from include/uapi/linux/input-event-codes.h
CONSUMER = {  # HID consumer usage -> evdev, per hid-input.c (consumer page cases)
    0x030: 116, 0x032: 142, 0x06F: 225, 0x070: 224, 0x0B3: 208, 0x0B4: 168, 0x0B5: 163, 0x0B6: 165,
    0x0B7: 166, 0x0CD: 164, 0x0E2: 113, 0x0E9: 115, 0x0EA: 114, 0x192: 140, 0x221: 217, 0x223: 172,
    0x224: 158, 0x225: 159,
}
SYSTEM = {"power_off": 116, "sleep": 142, "standby": 142, "wake": 143}   # generic desktop 0x81/0x82/0x83
EV_NAMES = {113: "KEY_MUTE", 114: "KEY_VOLUMEDOWN", 115: "KEY_VOLUMEUP", 116: "KEY_POWER", 140: "KEY_CALC",
            142: "KEY_SLEEP", 143: "KEY_WAKEUP", 158: "KEY_BACK", 159: "KEY_FORWARD", 163: "KEY_NEXTSONG",
            164: "KEY_PLAYPAUSE", 165: "KEY_PREVIOUSSONG", 166: "KEY_STOPCD", 168: "KEY_REWIND", 172: "KEY_HOMEPAGE",
            208: "KEY_FASTFORWARD", 217: "KEY_SEARCH", 224: "KEY_BRIGHTNESSDOWN", 225: "KEY_BRIGHTNESSUP"}


def kernel_hid_keyboard():
    cache = paths.CACHE / "linux-hid-input.c"
    if not cache.exists():
        paths.CACHE.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(KERNEL_URL, headers={"User-Agent": "hidfi-tests/1.0 (+https://github.com/san-gitlogin/HID-Fi)"})
        with urllib.request.urlopen(req, timeout=30) as r:
            cache.write_bytes(r.read())
    src = cache.read_text(encoding="utf-8")
    body = src[src.index("hid_keyboard[256] = {") + len("hid_keyboard[256] = {"):]
    body = body[:body.index("};")]
    toks = [t.strip() for t in body.replace("\n", " ").split(",") if t.strip()]
    table = [0 if t == "unk" else int(t) for t in toks]
    assert len(table) == 256, "kernel hid_keyboard[] changed shape"
    return table


def core_key_values():
    usb_src, _ = firmware.core_usb_src()
    if not usb_src:
        return None
    h = (usb_src / "USBHIDKeyboard.h").read_text(encoding="utf-8")
    return {m.group(1): int(m.group(2), 16) for m in re.finditer(r"#define\s+(KEY_[A-Z0-9_]+)\s+0x([0-9A-Fa-f]+)", h)}


def firmware_key_names(text=None):
    """name -> KEY_* constant, from the body of mapKeyName()."""
    text = text or firmware.ino_text()
    body = text[text.index("uint8_t mapKeyName(const String& name) {"):]
    body = body[:body.index("\n}\n")]
    out = {}
    for stmt in re.finditer(r"if\s*\(([^;]*?)\)\s*return\s+(KEY_[A-Z0-9_]+)\s*;", body):
        for name in re.findall(r'name == "([^"]+)"', stmt.group(1)):
            out[name] = stmt.group(2)
    return out


def usage_of(value):
    if 0x80 <= value <= 0x87:
        return 0xE0 + (value - 0x80)
    if value >= 0x88:
        return value - 0x88
    return None


def evdev_for_names():
    """{firmware key name: evdev keycode} for every name mapKeyName() accepts."""
    table, values = kernel_hid_keyboard(), core_key_values()
    if values is None:
        raise RuntimeError("ESP32 core not installed")
    out = {}
    for name, const in firmware_key_names().items():
        v = values.get(const)
        u = usage_of(v) if v is not None else None
        out[name] = table[u] if u is not None else None
    return out


def media_names():
    """{media key name: consumer usage} from the firmware's mediaUsage()."""
    text = firmware.ino_text()
    body = text[text.index("uint16_t mediaUsage(const char* key) {"):]
    body = body[:body.index("\n}\n")]
    usb_src, _ = firmware.core_usb_src()
    cc = {}
    if usb_src:
        for m in re.finditer(r"#define\s+(CONSUMER_CONTROL_[A-Z0-9_]+)\s+0x([0-9A-Fa-f]+)",
                             (usb_src / "USBHIDConsumerControl.h").read_text(encoding="utf-8")):
            cc[m.group(1)] = int(m.group(2), 16)
    out = {}
    for stmt in re.finditer(r"if\s*\(([^;]*?)\)\s*return\s+(CONSUMER_CONTROL_[A-Z0-9_]+)\s*;", body):
        for name in re.findall(r'k == "([^"]+)"', stmt.group(1)):
            out[name] = cc.get(stmt.group(2))
    return out


def ascii_to_evdev():
    """{char: (evdev code, needs shift)} for every printable ASCII character, via the
    core's en_US table and the kernel's hid_keyboard[] - what `type` must produce."""
    usb_src, _ = firmware.core_usb_src()
    if not usb_src:
        raise RuntimeError("ESP32 core not installed")
    lay = (usb_src / "keyboardLayout" / "KeyboardLayout_en_US.cpp").read_text(encoding="utf-8")
    body = lay[lay.index("KeyboardLayout_en_US[128] PROGMEM = {"):]
    body = body[body.index("{") + 1:body.index("};")]
    vals = []
    for line in body.splitlines():
        line = line.split("//")[0].strip().rstrip(",")
        if not line:
            continue
        for tok in line.split(","):
            tok = tok.strip()
            if not tok:
                continue
            shift = "SHIFT" in tok
            num = re.search(r"0x([0-9A-Fa-f]+)", tok)
            vals.append(((int(num.group(1), 16) if num else 0), shift))
    assert len(vals) == 128, f"en_US table parse gave {len(vals)} entries"
    table = kernel_hid_keyboard()
    out = {}
    for c in range(0x20, 0x7F):
        usage, shift = vals[c]
        if usage:
            out[chr(c)] = (table[usage & 0x7F], shift)
    return out
