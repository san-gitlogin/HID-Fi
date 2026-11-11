"""ESP32-S3 hardware rules the firmware must keep, so a board built around it still boots.

A pin that works on the dev kit can stop a custom board from starting, or stop working
when the module changes: the chip reads its strapping pins at every reset, the module's
flash and octal PSRAM take pins of their own, and ADC2 is the radio's while WiFi runs.
These cost a board spin when found late (checked against the S3 datasheet and the
WROOM-1 N16R8 module, 2026-11-11). HID-Fi uses two pins today: GPIO0, the BOOT button
(read at runtime on purpose: "HID OK", AP back, recovery mode), and GPIO48, the RGB LED.
"""
import re

import pytest

from support import paths

pytestmark = pytest.mark.static

FW = paths.TESTS.parent / "hid_fi"
SRC = "\n".join(p.read_text(encoding="utf-8") for p in sorted(FW.glob("*.ino")) + sorted(FW.glob("*.h"))
                if p.name != "web_ui.h")

STRAPPING = {3, 45, 46}                    # GPIO0 is one too, and is the BOOT button by design
DELIBERATE = {0: "BOOT button: pulled up, low only while pressed, so a reset still boots the firmware"}
FLASH = set(range(26, 33))                 # SPI flash, on every WROOM-1
OCTAL_PSRAM = set(range(33, 38))           # N16R8 (octal PSRAM): taken by the memory
USB = {19, 20}                             # native USB: HID-Fi's whole job
UART0 = {43, 44}                           # logs and the factory test pads
ADC1 = set(range(1, 11))                   # ADC2 belongs to the radio while WiFi runs

PIN_CALLS = r"\b(pinMode|digitalRead|digitalWrite|analogRead|analogReadMilliVolts|rgbLedWrite|neopixelWrite|ledcAttach|touchRead)\s*\(\s*([^,)\s]+)"


def pin_defines():
    return {name: int(num) for name, num in re.findall(r"^\s*#define\s+(\w+(?:_PIN|_BUTTON))\s+(\d+)\b", SRC, re.M)}


def pin_uses():
    """Every (call, pin number) the firmware touches; a literal or an unknown name is a failure."""
    defs = pin_defines()
    uses, unknown = [], []
    for call, arg in re.findall(PIN_CALLS, SRC):
        if arg.isdigit():
            unknown.append(f"{call}({arg}) - use a named #define ..._PIN so this test sees it")
        elif arg in defs:
            uses.append((call, defs[arg]))
        else:
            unknown.append(f"{call}({arg})")
    return uses, unknown


def test_every_pin_the_firmware_touches_is_a_named_define():
    uses, unknown = pin_uses()
    assert uses, "found no pin use at all - the pattern no longer matches the firmware"
    assert not unknown, f"pins this test cannot check: {unknown}"


def test_no_pin_is_a_strapping_flash_psram_usb_or_log_pin():
    uses, _ = pin_uses()
    bad = []
    for call, pin in uses:
        for group, name in ((STRAPPING, "a strapping pin (read at reset: decides how the chip boots)"),
                            (FLASH, "the module's SPI flash"), (OCTAL_PSRAM, "the N16R8's octal PSRAM"),
                            (USB, "native USB"), (UART0, "UART0 (logs, factory pads)")):
            if pin in group and pin not in DELIBERATE:
                bad.append(f"{call} on GPIO{pin}: {name}")
    assert not bad, bad


def test_analog_inputs_are_on_adc1():
    uses, _ = pin_uses()
    bad = [f"{call} on GPIO{pin}" for call, pin in uses if call.startswith("analogRead") and pin not in ADC1]
    assert not bad, f"ADC2 stops reading while WiFi is on - use GPIO1-10: {bad}"


def test_the_deliberate_strapping_pin_is_still_only_the_boot_button():
    defs = pin_defines()
    assert defs.get("BOOT_BUTTON") == 0
    assert re.search(r"pinMode\(\s*BOOT_BUTTON\s*,\s*INPUT_PULLUP\s*\)", SRC), \
        "GPIO0 must stay an input with its pull-up: driven low at a reset, the chip boots the ROM loader"
    assert not re.search(r"digitalWrite\(\s*BOOT_BUTTON|pinMode\(\s*BOOT_BUTTON\s*,\s*OUTPUT", SRC), "GPIO0 is never driven"


def test_a_brownout_restart_is_reported():
    """A supply that dips while the radio transmits restarts the chip; without the reason it looks random."""
    ino = (FW / "hid_fi.ino").read_text(encoding="utf-8")
    body = ino[ino.index("const char* resetReasonStr() {"):]
    body = body[:body.index("\n}\n")]
    for rst, name in (("ESP_RST_BROWNOUT", "brownout"), ("ESP_RST_POWERON", "power_on"), ("ESP_RST_PANIC", "panic"),
                      ("ESP_RST_TASK_WDT", "task_watchdog"), ("ESP_RST_SW", "software"), ("ESP_RST_PWR_GLITCH", "power_glitch")):
        assert re.search(rf'case {rst}:\s*return "{name}";', body), f"{rst} must read {name!r}"
    boot = ino[ino.index('doc["event"] = "boot";'):]
    assert 'doc["reset_reason"] = resetReasonStr();' in boot[:boot.index("sendJson(doc);")]
    status = ino[ino.index("void buildStatusDoc(JsonDocument& doc) {"):]
    assert 'doc["reset_reason"] = resetReasonStr();' in status[:status.index("\n}\n")]
    sim = (paths.TESTS / "support" / "board_sim.py").read_text(encoding="utf-8")
    assert '"reset_reason":' in sim, "the simulated board answers status like the real one (test_sim_contract)"
