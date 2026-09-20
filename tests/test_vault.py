"""Password vault: storage, the PIN gate, and that nothing leaks.

This is the regression test for the vault rules in SECURITY.md. The gate is the
whole feature, so most of these checks are attempts to get a secret out without
presenting a PIN - over **serial**, the transport with no session and no
lockout, which is where a hole would be easiest to reach.

Covered:
  - a secret cannot be read back or typed without the PIN
  - storing, renaming and deleting do NOT ask for it, because none of them
    discloses anything
  - a PIN entry is refused unless it is all digits
  - the vault's own PIN, when set, replaces the access PIN as the gate
  - resetting a forgotten vault PIN with the access PIN erases the vault, which
    is what stops the access PIN being a quiet way to read everything
  - changing the access PIN without proving the old one erases the vault, which
    is what stops ungated serial set_auth from being a way in
  - proving the old PIN keeps it
  - vault_list and storage_info never carry a secret

`vault_type` is exercised with --type only, because it really does type into
whatever the host has focused.

**It refuses to run on a board that is holding anything.** The last section
deliberately resets the access PIN without the old one, which erases the vault -
so a board with entries already stored, a vault PIN set, or an access PIN this
test does not know is left completely alone. Use a board you are willing to wipe.
"""
import json
import sys
import time

import serial
import serial.tools.list_ports

MARKER = "zzVAULTsecret42!"
ACCESS_PIN = "9137"
VAULT_PIN = "4242"


def find_port():
    for p in serial.tools.list_ports.comports():
        if p.vid == 0x1A86 and p.pid in (0x55D3, 0x7523):
            return p.device
    return None


def send(ser, cmd, timeout=4.0):
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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_type = "--type" in sys.argv
    port = args[0] if args else find_port()
    if not port:
        print("No CH343 board found")
        return 1

    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, 115200, 2
    ser.dtr = False
    ser.rts = False
    ser.open()
    time.sleep(4)          # opening the port resets the board

    r = Run()
    print("=" * 64)
    print(f"  Password vault checks over serial on {port}")
    print("=" * 64)

    st, _ = send(ser, {"cmd": "status"})
    if not r.check("board answered", st is not None):
        ser.close()
        return 1
    had_pin = bool(st.get("auth_set"))

    lst, _ = send(ser, {"cmd": "vault_list"})
    if not r.check("vault_list answers", lst is not None and lst.get("reply") == "vault_list"):
        ser.close()
        return 1
    pre_existing = [e["slot"] for e in lst.get("entries", [])]

    # This test erases the vault on purpose at the end, so it will not go near a
    # board that is actually holding something. Refuse, say why, change nothing.
    stop = []
    if pre_existing:
        stop.append(f"the vault holds {len(pre_existing)} "
                    f"{'entry' if len(pre_existing) == 1 else 'entries'}")
    if lst.get("vault_pin_set"):
        stop.append("a vault PIN is set")
    if had_pin:
        stop.append("an access PIN is set, and this test does not know it")
    if stop:
        print("\n  Refusing to run. On this board: " + "; ".join(stop) + ".")
        print("  The last section of this test erases the vault by design, so it only")
        print("  runs on a board with no PIN set and an empty vault.")
        print("  Nothing has been changed.")
        ser.close()
        return 1

    free = 0

    print("\n-- nothing can be stored without an access PIN --")
    res, _ = send(ser, {"cmd": "vault_save", "label": "ZZ", "secret": MARKER})
    r.check("vault_save refused with no PIN set",
            res is not None and res.get("status") == "error", str(res.get("reply") if res else None))
    send(ser, {"cmd": "set_auth", "token": ACCESS_PIN})
    gate = ACCESS_PIN

    print("\n-- storing does not ask for a PIN, because it discloses nothing --")
    res, _ = send(ser, {"cmd": "vault_save", "slot": free, "label": "ZZ audit",
                        "type": "pin", "secret": MARKER})
    r.check("save with no PIN at all works", res is not None and res.get("reply") == "vault_saved",
            str(res.get("message") if res else None))
    r.check("reply reports the kind", res is not None and res.get("type") == "pin",
            str(res.get("type") if res else None))

    print("\n-- a PIN entry has to be digits --")
    res, _ = send(ser, {"cmd": "vault_save", "label": "ZZ bad pin", "type": "pin",
                        "secret": "12ab34"})
    r.check("a PIN with letters in it is refused",
            res is not None and res.get("status") == "error", str(res.get("message") if res else None))

    print("\n-- listing never carries the secret --")
    lst, raw = send(ser, {"cmd": "vault_list"})
    mine = next((e for e in (lst or {}).get("entries", []) if e["slot"] == free), None)
    r.check("entry is listed", mine is not None)
    r.check("label and kind only", mine is not None and mine.get("label") == "ZZ audit"
            and mine.get("type") == "pin")
    r.check("vault_list does not leak the secret", MARKER not in raw,
            "MARKER PRESENT" if MARKER in raw else "")
    r.check("no length is reported either", mine is not None and "len" not in mine)
    _, raw = send(ser, {"cmd": "storage_info"})
    r.check("storage_info does not leak it", MARKER not in raw)
    _, raw = send(ser, {"cmd": "status"})
    r.check("status does not leak it", MARKER not in raw)

    print("\n-- reading it back needs the PIN, every time --")
    res, raw = send(ser, {"cmd": "vault_get", "slot": free})
    r.check("vault_get without a PIN is denied", res is not None and res.get("reply") == "vault_denied")
    r.check("the denial carries no secret", MARKER not in raw)
    time.sleep(6)
    res, _ = send(ser, {"cmd": "vault_get", "slot": free, "pin": gate})
    r.check("vault_get with the PIN returns it", res is not None and res.get("secret") == MARKER)

    print("\n-- typing needs the PIN too --")
    res, _ = send(ser, {"cmd": "vault_type", "slot": free})
    r.check("vault_type without a PIN is denied", res is not None and res.get("reply") == "vault_denied")
    if do_type:
        time.sleep(6)
        res, _ = send(ser, {"cmd": "vault_type", "slot": free, "pin": gate})
        r.check("vault_type with the PIN types it", res is not None and res.get("reply") == "vault_typed")
    else:
        print("  [SKIP] real typing - pass --type to send keystrokes to this computer")
    time.sleep(6)

    print("\n-- editing keeps the secret without being given it --")
    res, _ = send(ser, {"cmd": "vault_save", "slot": free, "label": "ZZ renamed",
                        "type": "password"})
    r.check("edit with no secret accepted", res is not None and res.get("reply") == "vault_saved",
            str(res.get("message") if res else None))
    res, _ = send(ser, {"cmd": "vault_get", "slot": free, "pin": gate})
    r.check("the stored secret survived the edit", res is not None and res.get("secret") == MARKER)
    r.check("the label changed", res is not None and res.get("label") == "ZZ renamed")
    r.check("the kind changed", res is not None and res.get("type") == "password")

    print("\n-- a vault PIN of its own takes over the gate --")
    res, _ = send(ser, {"cmd": "vault_pin", "access": gate, "new_pin": VAULT_PIN})
    r.check("vault PIN set with the access PIN",
            res is not None and res.get("vault_pin_set") is True,
            str(res.get("message") if res else None))
    r.check("setting the first vault PIN keeps the entries",
            res is not None and res.get("vault_wiped") is False)
    res, _ = send(ser, {"cmd": "vault_get", "slot": free, "pin": gate})
    r.check("the access PIN no longer opens the vault",
            res is not None and res.get("reply") == "vault_denied")
    time.sleep(6)
    res, _ = send(ser, {"cmd": "vault_get", "slot": free, "pin": VAULT_PIN})
    r.check("the vault PIN does", res is not None and res.get("secret") == MARKER)

    print("\n-- with a vault PIN, the access PIN can change without a wipe --")
    res, _ = send(ser, {"cmd": "set_auth", "token": "5555"})
    r.check("set_auth reports no wipe", res is not None and res.get("vault_wiped") is False)
    res, _ = send(ser, {"cmd": "vault_get", "slot": free, "pin": VAULT_PIN})
    r.check("the entry survived", res is not None and res.get("secret") == MARKER)

    print("\n-- back to the access PIN guarding it --")
    res, _ = send(ser, {"cmd": "vault_pin", "pin": VAULT_PIN, "new_pin": ""})
    r.check("vault PIN cleared with the vault PIN",
            res is not None and res.get("vault_pin_set") is False)
    r.check("clearing it knowingly kept the entries",
            res is not None and res.get("vault_wiped") is False)
    res, _ = send(ser, {"cmd": "vault_get", "slot": free, "pin": "5555"})
    r.check("the access PIN opens it again", res is not None and res.get("secret") == MARKER)

    print("\n-- resetting a forgotten vault PIN erases what it guarded --")
    send(ser, {"cmd": "vault_pin", "access": "5555", "new_pin": VAULT_PIN})
    res, _ = send(ser, {"cmd": "vault_pin", "access": "5555", "new_pin": "1212"})
    r.check("reset with the access PIN is allowed",
            res is not None and res.get("reply") == "vault_pin_set",
            str(res.get("message") if res else None))
    r.check("and it reports the wipe", res is not None and res.get("vault_wiped") is True)
    lst, _ = send(ser, {"cmd": "vault_list"})
    r.check("the vault is empty after the reset",
            lst is not None and len(lst.get("entries", [])) == 0)
    send(ser, {"cmd": "vault_pin", "pin": "1212", "new_pin": ""})

    print("\n-- proving the old access PIN keeps the vault --")
    send(ser, {"cmd": "vault_save", "slot": free, "label": "ZZ again", "secret": MARKER})
    res, _ = send(ser, {"cmd": "set_auth", "token": ACCESS_PIN, "old": "5555"})
    r.check("set_auth with the old PIN reports no wipe",
            res is not None and res.get("vault_wiped") is False)
    res, _ = send(ser, {"cmd": "vault_get", "slot": free, "pin": ACCESS_PIN})
    r.check("the entry is still there", res is not None and res.get("secret") == MARKER)

    print("\n-- resetting the PIN blind erases what it guarded --")
    res, _ = send(ser, {"cmd": "set_auth", "token": "7777"})
    r.check("set_auth without the old PIN reports a wipe",
            res is not None and res.get("vault_wiped") is True)
    lst, raw = send(ser, {"cmd": "vault_list"})
    r.check("the vault is empty", lst is not None and len(lst.get("entries", [])) == 0,
            str(len((lst or {}).get("entries", []))))
    r.check("and the secret is gone from every reply", MARKER not in raw)
    res, _ = send(ser, {"cmd": "vault_get", "slot": free, "pin": "7777"})
    r.check("the old slot reads as empty", res is not None and res.get("status") == "error")

    print("\n-- restoring the board --")
    send(ser, {"cmd": "vault_wipe"})
    send(ser, {"cmd": "set_auth", "token": ""})
    st, _ = send(ser, {"cmd": "status"})
    r.check("access PIN left unset, as found", not (st or {}).get("auth_set"))
    lst, _ = send(ser, {"cmd": "vault_list"})
    r.check("vault left empty", lst is not None and len(lst.get("entries", [])) == 0)
    r.check("vault PIN left unset", lst is not None and lst.get("vault_pin_set") is False)

    print("=" * 64)
    print(f"  {r.passed} passed, {r.failed} failed")
    print("=" * 64)
    ser.close()
    return 0 if r.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
