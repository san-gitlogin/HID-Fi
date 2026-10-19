"""Recover a HID-Fi board you hold: its WiFi password, and its access PIN.

    ./flash_esp.sh --recover            (macOS / Linux - runs this)
    .\\flash_esp.ps1 -Recover            (Windows - the same, in PowerShell)
    python3 tools/recover.py            (directly)

Recovery works only over the board's USB COM cable, on purpose. Over WiFi
nobody can read the WiFi password or reset a PIN they do not know - not even the
owner - because a stranger on the same network looks exactly like the owner
there. Holding the board is what proves ownership.

What it can do, from a menu (or one flag each, for scripts):
  show the board's WiFi name and password     --show-password
  set a new WiFi password                      --set-password NEW
  reset the access PIN (or turn it off)        --reset-pin 1234 | --reset-pin off

Stored passwords (the vault) are kept when the vault has its own vault PIN. If
it does not, resetting the access PIN erases them, because the access PIN was
what guarded them; the tool says so first and asks you to type ERASE (or pass
--erase-vault for a script).

Exit codes: 0 done | 1 the board refused or did not answer | 4 no board |
10 several boards, pick one | 11 not a HID-Fi board | 12 pyserial missing.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flash_ports as F  # noqa: E402

VAULT_SAFE = "kept - the vault has its own vault PIN"


def say(*lines):
    for l in lines:
        print("   " + l)


class BoardLink:
    """One serial session. DTR and RTS stay low: they drive the board's reset."""

    def __init__(self, device):
        import serial
        self.s = serial.Serial()
        self.s.port, self.s.baudrate, self.s.dtr, self.s.rts, self.s.timeout = device, 115200, False, False, 0.3
        self.s.open()
        # Opening the port can restart the board on some systems; let it finish.
        time.sleep(float(os.environ.get("HIDFI_RECOVER_SETTLE", "2.5")))
        self.s.reset_input_buffer()

    def cmd(self, body, timeout=6.0):
        self.s.write((json.dumps(body) + "\n").encode())
        end = time.time() + timeout
        while time.time() < end:
            line = self.s.readline().decode(errors="ignore").strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "status" in d and not d.get("event"):
                return d
        raise TimeoutError(f"no answer to {body.get('cmd')}")

    def close(self):
        self.s.close()


def pick_board(args):
    try:
        ports = F.list_ports()
    except ImportError:
        say("pyserial is not installed: pip install pyserial")
        return None, 12
    targets, lines, code = F.choose(ports, args.port, args.serial)
    for l in lines:
        say(l)
    if code == 10 and sys.stdin.isatty() and not os.environ.get("CI"):
        cands = targets
        ans = input(f"   Which board? [1-{len(cands)}, Enter = none]: ").strip()
        if ans.isdigit() and 1 <= int(ans) <= len(cands):
            return cands[int(ans) - 1], 0
        say("Nothing chosen, nothing changed.")
        return None, 10
    if code:
        return None, code
    return targets[0], 0


def summary(link):
    st = link.cmd({"cmd": "status"})
    ap = link.cmd({"cmd": "ap_password"})
    vl = link.cmd({"cmd": "vault_list"})
    entries = len(vl.get("entries") or [])
    return st, ap, vl, entries


def vault_fate(vl, entries):
    if not entries:
        return "nothing stored, nothing to lose"
    if vl.get("vault_pin_set"):
        return f"{entries} stored password(s) {VAULT_SAFE}"
    return f"{entries} stored password(s) WILL BE ERASED - they were guarded by the access PIN"


def show_password(ap):
    if ap.get("open"):
        say(f"WiFi network:  {ap.get('ssid')}", "Password:      none - the network is open")
    else:
        say(f"WiFi network:  {ap.get('ssid')}", f"Password:      {ap.get('password')}")
        if ap.get("generated"):
            say("(This board made this password for itself. You can keep it or set your own.)")
    return 0


def set_password(link, ap, new):
    if not (8 <= len(new.encode()) <= 63):
        say("A WiFi password needs 8 to 63 characters. Nothing changed.")
        return 1
    # ap_configure restarts the access point and does not answer, by design.
    link.s.write((json.dumps({"cmd": "ap_configure", "ssid": ap.get("ssid"), "password": new, "open": False}) + "\n").encode())
    time.sleep(1.5)
    again = link.cmd({"cmd": "ap_password"})
    if again.get("password") != new:
        say("The board did not take the new password. Nothing changed.")
        return 1
    say(f"Done. Join '{again.get('ssid')}' with the new password. Phones on it now will drop off.")
    return 0


def reset_pin(link, vl, entries, new, erase_ok, interactive):
    turning_off = new in ("", "off")
    pin = "" if turning_off else new
    if pin and (not pin.isdigit() or len(pin) != 4):
        say("A PIN is four digits. Nothing changed.")
        return 1
    fate = vault_fate(vl, entries)
    say(f"Stored passwords: {fate}.")
    erases = entries and not vl.get("vault_pin_set")
    if erases and not erase_ok:
        if not interactive:
            say("Refusing: this would erase the vault. Pass --erase-vault if that is what you want.")
            return 1
        if input("   Type ERASE to reset the PIN and erase them, anything else to stop: ").strip() != "ERASE":
            say("Nothing changed.")
            return 1
    r = link.cmd({"cmd": "set_auth", "token": pin})
    if r.get("status") != "ok":
        say(f"The board refused: {r.get('message')}")
        return 1
    st = link.cmd({"cmd": "status"})
    if bool(st.get("auth_set")) != bool(pin):
        say("The board did not take it. Nothing changed.")
        return 1
    say("Access PIN turned off." if turning_off else "Access PIN set. Phones will ask for it on their next connection.")
    if r.get("vault_wiped"):
        say("The stored passwords were erased, as warned.")
    return 0


def menu(link, st, ap, vl, entries):
    say(f"Board: {st.get('firmware')}  WiFi: {ap.get('ssid')}  access PIN: {'set' if st.get('auth_set') else 'not set'}",
        f"Stored passwords: {vault_fate(vl, entries)}")
    print()
    print("   1  Show this board's WiFi name and password")
    print("   2  Set a new WiFi password")
    print("   3  Reset the access PIN (or turn it off)")
    print("   4  Quit")
    choice = input("   Choose 1-4: ").strip()
    if choice == "1":
        return show_password(ap)
    if choice == "2":
        a = input("   New WiFi password (8-63 characters): ")
        if input("   Type it again: ") != a:
            say("They did not match. Nothing changed.")
            return 1
        return set_password(link, ap, a)
    if choice == "3":
        p = input("   New 4 digit access PIN, or Enter to turn the PIN off: ").strip()
        return reset_pin(link, vl, entries, p, False, True)
    say("Nothing changed.")
    return 0


def main(argv=None):
    ap_ = argparse.ArgumentParser(description="Recover a HID-Fi board over its USB COM cable.")
    ap_.add_argument("--port", default=os.environ.get("HIDFI_PORT") or None)
    ap_.add_argument("--serial", default=os.environ.get("HIDFI_SERIAL") or None)
    ap_.add_argument("--show-password", action="store_true")
    ap_.add_argument("--set-password")
    ap_.add_argument("--reset-pin")
    ap_.add_argument("--erase-vault", action="store_true", help="allow a PIN reset to erase an unguarded vault")
    a = ap_.parse_args(argv)

    print("== HID-Fi recovery (USB COM cable)")
    board, code = pick_board(a)
    if code:
        return code
    say(f"Board on {board.device} (serial {board.serial or '?'})")
    try:
        link = BoardLink(board.device)
    except Exception as e:
        say(f"Could not open {board.device}: {e}",
            "Close anything else using it (Arduino Serial Monitor, another terminal) and try again.")
        return 1
    try:
        st, ap, vl, entries = summary(link)
        if ap.get("status") != "ok":
            say("This board's firmware is older than v3.10 and cannot show its WiFi password.",
                "Flash the current firmware first: it keeps every setting and stored password.")
            return 1
        if a.show_password:
            return show_password(ap)
        if a.set_password is not None:
            return set_password(link, ap, a.set_password)
        if a.reset_pin is not None:
            return reset_pin(link, vl, entries, a.reset_pin, a.erase_vault, False)
        if not sys.stdin.isatty():
            say("No choice given. Use --show-password, --set-password or --reset-pin, or run it in a terminal.")
            return 1
        return menu(link, st, ap, vl, entries)
    except TimeoutError as e:
        say(f"The board did not answer ({e}). Is the COM cable in the board's COM port?")
        return 1
    finally:
        link.close()


if __name__ == "__main__":
    sys.exit(main())
