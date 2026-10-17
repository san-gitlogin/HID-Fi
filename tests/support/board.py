"""One serial session to the board for a whole test run.

Opening the COM port reboots the board. Windows honours DTR/RTS being held low
before open(); the macOS CDC-ACM driver (the port shows as cu.usbmodem*) pulses
them anyway. Either way, a test run that opens the port per test reboots the
board per test, and every reboot drops the USB side for a few seconds - which
is how keystrokes get lost straight after an open. So the port is opened once,
the boot is waited out, and every test shares the session.

The legacy scripts in tests/ open the port themselves. Call release() before
running one, and the next acquire() re-opens (and re-boots) cleanly.
"""
import json
import time

import serial
import serial.tools.list_ports

# CH343 (this board) and CH340 bridges. Both appear under these IDs on every OS.
BRIDGE_IDS = {(0x1A86, 0x55D3), (0x1A86, 0x7523)}
NATIVE_USB_VID = 0x303A                     # Espressif: the board's own USB (HID side)
BOOT_SETTLE_S = 9.0                         # boot + host re-enumeration + 3 s OS detection


def find_ports():
    return [p.device for p in serial.tools.list_ports.comports() if (p.vid, p.pid) in BRIDGE_IDS]


class BoardError(Exception):
    pass


class Board:
    def __init__(self, port):
        self.port = port
        self.ser = None
        self.events = []            # unsolicited {"event": ...} lines seen while waiting
        self.boots = 0

    # ---- session ------------------------------------------------------------
    def open(self, settle=BOOT_SETTLE_S):
        s = serial.Serial()
        s.port, s.baudrate, s.timeout = self.port, 115200, 0.3
        s.dtr = False                       # set BEFORE open, as every test here does
        s.rts = False
        s.open()
        self.ser = s
        t0 = time.time()
        self.boot_s = None                  # seconds from open() to the boot event
        end = t0 + settle
        while time.time() < end:            # drain the boot banner, note the boot
            line = self._readline()
            if line and '"event":"boot"' in line.replace(" ", ""):
                self.boots += 1
                if self.boot_s is None:
                    self.boot_s = time.time() - t0
        self.ser.reset_input_buffer()
        return self

    def close(self):
        if self.ser:
            try:
                self.ser.close()
            finally:
                self.ser = None

    @property
    def is_open(self):
        return self.ser is not None

    def _readline(self):
        try:
            return self.ser.readline().decode("utf-8", "replace").strip()
        except serial.SerialException as e:
            raise BoardError(f"serial port went away: {e}") from e

    # ---- protocol -----------------------------------------------------------
    def cmd(self, obj, want=None, timeout=10.0):
        """Send one JSON command; return the first reply (or the first error).

        want: the expected "reply" value. Events are collected, not returned.
        """
        if not self.ser:
            raise BoardError("board session is not open")
        self.ser.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        self.ser.flush()
        end = time.time() + timeout
        while time.time() < end:
            line = self._readline()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "event" in d:
                self.events.append(d)
                continue
            if want is None or d.get("reply") == want or d.get("status") == "error":
                return d
        raise BoardError(f"no reply to {obj.get('cmd')!r} within {timeout}s")

    def status(self):
        return self.cmd({"cmd": "status"}, "status")

    def wait_host(self, timeout=15.0):
        """Block until a host has the USB side mounted and OS detection settled."""
        end = time.time() + timeout
        st = {}
        while time.time() < end:
            st = self.status()
            if st.get("usb_mounted") and st.get("detect_phase") == "settled":
                return st
            time.sleep(0.5)
        raise BoardError(f"no host mounted the board's USB side within {timeout}s "
                         f"(usb_mounted={st.get('usb_mounted')}, detect_phase={st.get('detect_phase')}) - "
                         "is the USB port (not COM) plugged into this computer?")

    # ---- leave the board as it was found -------------------------------------
    def snapshot(self):
        st = self.status()
        macros = self.cmd({"cmd": "macro_list"}).get("macros") or []
        return {
            "unicode_mac": bool(st.get("unicode_mac")),
            "unicode_linux": bool(st.get("unicode_linux")),
            "host_os_pin": st.get("host_os") if st.get("host_os_source") == "manual" else "auto",
            "power_mode": st.get("power_mode"),
            "macro_slots": sorted(m.get("slot") for m in macros if isinstance(m, dict)),
            "auth_set": bool(st.get("auth_set")),
        }

    def restore(self, snap):
        """Put back what tests may have changed. Returns a list of what differed."""
        drift = []
        now = self.snapshot()
        for os_ in ("mac", "linux"):
            if now[f"unicode_{os_}"] != snap[f"unicode_{os_}"]:
                self.cmd({"cmd": "set_unicode", "os": os_, "on": snap[f"unicode_{os_}"]}, "unicode_set")
                drift.append(f"unicode_{os_}")
        if now["host_os_pin"] != snap["host_os_pin"]:
            self.cmd({"cmd": "set_host_os", "os": snap["host_os_pin"]}, "host_os_set")
            drift.append("host_os_pin")
        if snap["power_mode"] and now["power_mode"] != snap["power_mode"]:
            self.cmd({"cmd": "power_mode", "mode": snap["power_mode"]})
            drift.append("power_mode")
        for slot in set(now["macro_slots"]) - set(snap["macro_slots"]):
            self.cmd({"cmd": "macro_delete", "slot": slot})
            drift.append(f"macro_slot_{slot}")
        self.cmd({"cmd": "key_release_all"})
        return drift

    def free_macro_slot(self):
        used = {m.get("slot") for m in (self.cmd({"cmd": "macro_list"}).get("macros") or []) if isinstance(m, dict)}
        for s in range(0, 8):               # firmware accepts slots 0-7
            if s not in used:
                return s
        raise BoardError("no free macro slot - refusing to overwrite a saved macro")
