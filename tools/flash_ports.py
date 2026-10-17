"""Which serial port is the HID-Fi board? Used by flash_esp.sh (and testable).

A developer's machine can have several boards plugged in: two HID-Fi boards, an
Arduino, an ESP32 dev kit on a different USB bridge, a HID-Fi board stuck in its
ROM bootloader. This module never guesses between them:

  - It reads each port's USB identity (vendor, product, serial number), so a
    board is recognised by what it is, not by what its /dev name looks like.
  - The HID-Fi board's COM cable is a WCH CH343 bridge (1a86:55d3). Only those
    are flashed without being named.
  - One device can appear under two names on macOS (cu.wchusbserial* with WCH's
    driver and cu.usbserial*/cu.usbmodem* with Apple's); they are one board.
  - Exactly one HID-Fi board: it is used. None: it says what it did find. More
    than one: it lists them and asks (or, without a terminal, stops with exit 10)
    - pick by port, by USB serial number (stable across replugging, unlike port
    names), or flash them all.
  - A port named by hand that is not a HID-Fi COM bridge is refused unless forced,
    because flashing it would overwrite some other project's firmware.

  python3 tools/flash_ports.py [--port P] [--serial S] [--all] [--force-port] [--identify]

Output, one fact per line, for the shell to read:
  TARGET <device> <serial>     a board to flash (zero or more)
  SAY <text>                   something to show the user
  CODE <n>                     the exit code the flasher should use (last line)

Exit codes match the flashers: 0 ok | 4 no board | 10 several, pick one |
11 named port is not a HID-Fi COM bridge (add --force-port) | 12 cannot tell (no pyserial).

For tests, HIDFI_FAKE_PORTS may name a JSON file of ports in place of the real
ones: [{"device", "vid", "pid", "serial", "location", "description"}].
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass

HIDFI_COM = (0x1A86, 0x55D3)                 # WCH CH343, the board's "COM" connector
ESP_ROM = (0x303A, 0x1001)                   # ESP32-S3 native USB in ROM download / JTAG mode
OTHER_BRIDGES = {                            # what other ESP32 boards and Arduinos use
    (0x1A86, 0x7523): "CH340", (0x1A86, 0x55D4): "CH9102", (0x10C4, 0xEA60): "CP210x",
    (0x0403, 0x6001): "FTDI", (0x0403, 0x6010): "FTDI", (0x0403, 0x6014): "FTDI", (0x0403, 0x6015): "FTDI",
    (0x2341, 0x0043): "Arduino Uno", (0x2341, 0x0001): "Arduino Uno", (0x2341, 0x0042): "Arduino Mega",
}
# Names a port gets on macOS, best first: WCH's own driver, then Apple's.
MAC_PREFERENCE = ("/dev/cu.wchusbserial", "/dev/cu.usbmodem", "/dev/cu.usbserial")


@dataclass
class Port:
    device: str
    vid: int = 0
    pid: int = 0
    serial: str = ""
    location: str = ""
    description: str = ""

    @property
    def kind(self):
        if (self.vid, self.pid) == HIDFI_COM:
            return "hidfi"
        if (self.vid, self.pid) == ESP_ROM or self.vid == 0x303A:
            return "esp-native"
        if (self.vid, self.pid) in OTHER_BRIDGES:
            return "other-bridge"
        return "other" if self.vid else "not-usb"

    @property
    def what(self):
        if self.kind == "hidfi":
            return "HID-Fi COM (CH343)"
        if self.kind == "esp-native":
            return "ESP32 native USB - a board in its ROM bootloader, or a different firmware"
        if self.kind == "other-bridge":
            return f"{OTHER_BRIDGES[(self.vid, self.pid)]} serial - not a HID-Fi board"
        if not self.vid:
            return "not a USB device"
        return (self.description or "USB serial device") + f" ({self.vid:04x}:{self.pid:04x})"

    def line(self):
        return f"{self.device}  serial {self.serial or '?'}  {self.what}"


def list_ports():
    fake = os.environ.get("HIDFI_FAKE_PORTS")
    if fake:
        return [Port(p["device"], int(str(p.get("vid", 0)), 0), int(str(p.get("pid", 0)), 0), p.get("serial") or "",
                     p.get("location") or "", p.get("description") or "") for p in json.load(open(fake))]
    from serial.tools import list_ports as lp
    return [Port(p.device, p.vid or 0, p.pid or 0, p.serial_number or "", p.location or "", p.description or "")
            for p in lp.comports()]


def one_per_device(ports):
    """Fold the names one USB device gets on macOS into one entry."""
    groups, order = {}, []
    for p in ports:
        if not p.vid:
            continue                                   # Bluetooth, debug consoles: never a board
        key = (p.vid, p.pid, p.serial or p.location or p.device)
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(p)

    def rank(p):
        for i, pre in enumerate(MAC_PREFERENCE):
            if p.device.startswith(pre):
                return i
        return len(MAC_PREFERENCE)
    return [sorted(groups[k], key=rank)[0] for k in order]


def choose(ports, port=None, serial=None, all_=False, force=False):
    """(targets, lines, code). Pure: everything it needs is in its arguments."""
    devs = one_per_device(ports)
    boards = [p for p in devs if p.kind == "hidfi"]
    say = []

    if port:
        # Every name the device has counts, so a cu.usbserial path still matches.
        hit = next((p for p in ports if p.device == port), None)
        if not hit:
            say.append(f"{port} is not connected.")
            say += ["Connected:"] + ["  " + p.line() for p in devs] if devs else ["Nothing that looks like a board is connected."]
            return [], say, 4
        if hit.kind == "hidfi" or hit.kind == "esp-native" or force:
            if hit.kind == "esp-native":
                say.append(f"{port} is an ESP32's native USB port - if this is a HID-Fi board stuck in its ROM bootloader, "
                           "flashing it here brings it back.")
            elif hit.kind != "hidfi":
                say.append(f"{port} is not a HID-Fi COM bridge ({hit.what}); flashing it anyway because you forced it.")
            return [hit], say, 0
        say.append(f"{port} is not a HID-Fi board's COM port: {hit.what}.")
        say.append("Flashing it would overwrite whatever that board runs. If you are sure, add --force-port.")
        return [], say, 11

    if serial:
        hits = [p for p in devs if p.serial and p.serial.lower().startswith(serial.lower())]
        if len(hits) == 1:
            if hits[0].kind != "hidfi" and not force:
                return [], [f"Serial {serial} is {hits[0].line()} - not a HID-Fi COM bridge. Add --force-port if you are sure."], 11
            return hits, [], 0
        if not hits:
            return [], [f"No board with serial {serial} is connected."] + ["  " + p.line() for p in devs], 4
        return [], [f"Serial {serial} matches {len(hits)} boards - give more of it:"] + ["  " + p.line() for p in hits], 10

    if all_:
        if not boards:
            return [], _nothing(devs), 4
        return boards, [f"Flashing all {len(boards)} HID-Fi boards."], 0

    if len(boards) == 1:
        others = [p for p in devs if p is not boards[0]]
        if others:
            say.append(f"Ignoring {len(others)} other serial device(s), not HID-Fi boards:")
            say += ["  " + p.line() for p in others]
        return boards, say, 0
    if not boards:
        return [], _nothing(devs), 4
    say.append(f"{len(boards)} HID-Fi boards are connected, so none was picked:")
    say += [f"  {i + 1}. {p.line()}" for i, p in enumerate(boards)]
    say.append("Pick one with -p <port> or --serial <serial>, or flash them all with --all.")
    return boards, say, 10


def _nothing(devs):
    say = ["No HID-Fi board found. Its COM connector is a CH343 bridge (USB 1a86:55d3)."]
    native = [p for p in devs if p.kind == "esp-native"]
    if native:
        say.append("An ESP32 is connected by its native USB port - a HID-Fi board stuck in its ROM bootloader looks "
                   "like this. To flash it there: -p " + native[0].device)
    rest = [p for p in devs if p.kind != "esp-native"]
    if rest:
        say.append("Other serial devices (never flashed unless named with -p and --force-port):")
        say += ["  " + p.line() for p in rest]
    say.append("Plug the cable into the board's COM connector, use a data cable, and on macOS install the CH343 "
               "driver (wch-ic.com) if no serial device appears.")
    return say


def identify(device):
    """Ask a board what it runs. Opening the port can restart it - only on request."""
    try:
        import time
        import serial
        s = serial.Serial()
        s.port, s.baudrate, s.dtr, s.rts, s.timeout = device, 115200, False, False, 1
        s.open()
        time.sleep(2.5)
        s.reset_input_buffer()
        s.write(b'{"cmd":"status"}\n')
        end = time.time() + 4
        while time.time() < end:
            line = s.readline().decode(errors="ignore").strip()
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("firmware"):
                s.close()
                return f"{d['firmware']}, access point '{d.get('ap_ssid', '?')}'"
        s.close()
        return "no reply - another firmware, or still booting"
    except Exception as e:
        return f"could not open: {e}"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=os.environ.get("HIDFI_PORT") or None)
    ap.add_argument("--serial", default=os.environ.get("HIDFI_SERIAL") or None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force-port", dest="force", action="store_true")
    ap.add_argument("--identify", action="store_true")
    a = ap.parse_args(argv)
    try:
        ports = list_ports()
    except ImportError:
        print("SAY pyserial is not installed, so ports cannot be told apart (pip install pyserial).")
        print("CODE 12")
        return 0
    targets, lines, code = choose(ports, a.port, a.serial, a.all, a.force)
    if a.identify:
        cands = targets or [p for p in one_per_device(ports) if p.kind in ("hidfi", "esp-native")]
        lines = ["Asking each board what it runs (this can restart it):"]
        lines += [f"  {p.device}  serial {p.serial or '?'}  {identify(p.device)}" for p in cands]
        targets, code = [], 0
    for l in lines:
        print("SAY " + l)
    if code == 0:
        for t in targets:
            print(f"TARGET {t.device} {t.serial or '-'}")
    else:
        for t in targets:                       # the candidates, for an interactive choice
            print(f"CANDIDATE {t.device} {t.serial or '-'}")
    print(f"CODE {code}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
