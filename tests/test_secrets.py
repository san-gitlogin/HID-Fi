"""No command may hand a stored secret back.

This is the regression test for the rule in SECURITY.md: saved passwords and the
access PIN are write-and-use only, and nothing lists, exports or echoes them.

It runs over **serial on purpose**. Serial is the ungated transport - no PIN, no
lockout - so if a secret is reachable anywhere, it is reachable here. A leak that
this test cannot see is a leak that needs a PIN to reach, which is the whole
design.

The one documented exception is `macro_get`, which returns a macro's steps
verbatim so the dashboard can edit them. That is why a macro is not a secret
store, and the test asserts the exception still behaves exactly as documented
rather than pretending it does not exist.

Everything this test creates is deleted afterwards, and the board's PIN is left
as it was found.
"""
import json
import sys
import time

import serial
import serial.tools.list_ports

# Distinctive enough that finding it in any reply is unambiguous.
MARKER = "zzMARKERsecret77"
PROBE_PIN = "9137"

# Every command that returns a list, an inventory or a status. None of them may
# contain the marker. Add new read-only commands here.
SURFACES = ["pc_list", "macro_list", "status", "storage_info", "wifi_status",
            "ping", "clients"]


def find_port():
    for p in serial.tools.list_ports.comports():
        if p.vid == 0x1A86 and p.pid in (0x55D3, 0x7523):
            return p.device
    return None


def send(ser, cmd, timeout=4.0):
    """Returns (parsed, raw). The raw text is what the leak check reads."""
    while ser.in_waiting:
        ser.readline()
    ser.write((json.dumps(cmd) + "\n").encode())
    ser.flush()
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        raw = ser.readline().decode("utf-8", "replace").strip()
        if not raw.startswith("{"):
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if parsed.get("reply") or parsed.get("status"):
            return parsed, raw
    return None, ""


class Run:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, label, ok, detail=""):
        print(f"  [{'OK ' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        return ok


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else find_port()
    if not port:
        print("No CH343 board found")
        return 1

    # Deferred open with both lines low, so the board boots the application
    # rather than risking the ROM bootloader.
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, 115200, 2
    ser.dtr = False
    ser.rts = False
    ser.open()
    time.sleep(4)          # opening the port resets the board

    r = Run()
    print("=" * 62)
    print(f"  Secret disclosure audit over serial on {port}")
    print("=" * 62)

    st, _ = send(ser, {"cmd": "status"})
    if not r.check("board answered", st is not None):
        ser.close()
        return 1
    had_pin = bool(st.get("auth_set"))

    # Saving a PC password is refused without a PIN, so set one if there is none
    # and take it away again at the end.
    if not had_pin:
        send(ser, {"cmd": "set_auth", "token": PROBE_PIN})

    pcs, _ = send(ser, {"cmd": "pc_list"})
    pc_used = {p["slot"] for p in (pcs or {}).get("profiles", [])}
    pc_slot = next((i for i in range(8) if i not in pc_used), None)
    macros, _ = send(ser, {"cmd": "macro_list"})
    mac_used = {m["slot"] for m in (macros or {}).get("macros", [])}
    mac_slot = next((i for i in range(8) if i not in mac_used), None)
    if pc_slot is None or mac_slot is None:
        print("  every slot is in use - not overwriting anything")
        ser.close()
        return 1

    send(ser, {"cmd": "pc_save", "slot": pc_slot, "name": "ZZ audit", "password": MARKER})
    send(ser, {"cmd": "macro_save", "slot": mac_slot, "name": "ZZ audit",
               "steps": ["type:" + MARKER]})
    print(f"\n  marker planted in PC slot {pc_slot} and macro slot {mac_slot}\n")

    print("-- nothing that lists or reports may contain it --")
    for cmd in SURFACES:
        _, raw = send(ser, {"cmd": cmd})
        r.check(f"{cmd:<13} returns no secret", raw != "" and MARKER not in raw,
                "MARKER PRESENT IN REPLY" if MARKER in raw else "")

    print("\n-- the PIN is reported as a boolean, never echoed --")
    _, raw = send(ser, {"cmd": "storage_info"})
    r.check("storage_info: pin_set only", '"pin_set"' in raw and PROBE_PIN not in raw)
    _, raw = send(ser, {"cmd": "status"})
    r.check("status: auth_set only", '"auth_set"' in raw and PROBE_PIN not in raw)

    print("\n-- the documented exception --")
    _, raw = send(ser, {"cmd": "macro_get", "slot": mac_slot})
    r.check("macro_get returns the body verbatim, as documented", MARKER in raw,
            "a macro is not a secret store - SECURITY.md")

    print("\n-- restoring the board --")
    send(ser, {"cmd": "pc_delete", "slot": pc_slot})
    send(ser, {"cmd": "macro_delete", "slot": mac_slot})
    pcs, _ = send(ser, {"cmd": "pc_list"})
    macros, _ = send(ser, {"cmd": "macro_list"})
    now_pc = {p["slot"] for p in (pcs or {}).get("profiles", [])}
    now_mac = {m["slot"] for m in (macros or {}).get("macros", [])}
    r.check("planted PC slot deleted", pc_slot not in now_pc)
    r.check("planted macro slot deleted", mac_slot not in now_mac)
    r.check("no other profile touched", now_pc == pc_used, f"{sorted(now_pc)}")
    r.check("no other macro touched", now_mac == mac_used, f"{sorted(now_mac)}")
    if not had_pin:
        send(ser, {"cmd": "set_auth", "token": ""})
        st, _ = send(ser, {"cmd": "status"})
        r.check("access PIN left unset, as found", not (st or {}).get("auth_set"))

    print("=" * 62)
    print(f"  {r.passed} passed, {r.failed} failed")
    print("=" * 62)
    ser.close()
    return 0 if r.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
