"""Recover a HID-Fi board you hold: its WiFi password, and its access PIN.

    ./flash_esp.sh --recover            (macOS / Linux - runs this)
    .\\flash_esp.ps1 -Recover            (Windows - the same, in PowerShell)
    python3 tools/recover.py            (directly)

Recovery works only over USB, on purpose. Over WiFi nobody can read the WiFi
password or reset a PIN they do not know - not even the owner - because a
stranger on the same network looks exactly like the owner there. Holding the
board is what proves ownership: its COM cable, or - on a board with no COM port,
like the HID-Fi Dongle - recovery mode: BOOT held 8 seconds and let go, which
restarts the board with a serial link on its own USB for 5 minutes
(docs/RECOVERY_MODE.md). A board in recovery mode is used first: opening a COM
port resets the board, which would close it. When done, the tool closes recovery
mode again unless --keep-open is given.

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
ESP_NATIVE = (0x303A, 0x1001)   # a running HID-Fi's own USB - and the ROM's USB-Serial-JTAG: told apart by asking
RECOVERY_HELP = (
    "No COM cable? Open recovery mode on the board's own USB port instead:",
    "  1. Leave the board plugged into this computer.",
    "  2. Hold its BOOT button for 8 seconds (on the dongle: a pin in the pinhole), then let go.",
    "  3. It restarts with recovery open for 5 minutes. Run this again.",
)


def say(*lines):
    for l in lines:
        print("   " + l)


class BoardLink:
    """One serial session. On the COM cable DTR and RTS stay low: they drive the
    board's reset and BOOT. On the recovery link DTR is raised: the board's USB
    serial only answers a host that has raised it, and there it drives nothing."""

    def __init__(self, device, recovery=False):
        import serial
        self.recovery = recovery
        self.s = serial.Serial()
        self.s.port, self.s.baudrate, self.s.dtr, self.s.rts, self.s.timeout = device, 115200, recovery, False, 0.3
        self.s.open()
        # Opening the COM port can restart the board on some systems; let it finish.
        # The recovery link never restarts the board.
        time.sleep(float(os.environ.get("HIDFI_RECOVER_SETTLE", "0.2" if recovery else "2.5")))
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


def recovery_links(ports, timeout=1.5):
    """The boards in recovery mode: Espressif native ports that answer `status`
    with "recovery": true. Never guessed from a name or an ID: the ROM loader has the
    same VID:PID and answers no JSON, another ESP32 project answers something else."""
    found = []
    for p in F.one_per_device(ports):
        if (p.vid, p.pid) != ESP_NATIVE or "jtag" in (p.description or "").lower():
            continue
        try:
            link = BoardLink(p.device, recovery=True)
        except Exception:
            continue
        try:
            st = link.cmd({"cmd": "status"}, timeout=timeout)
            # A link a paired computer opened for its own factory reset is not ours.
            if st.get("recovery") is True and st.get("recovery_kind") != "reset":
                found.append(p)
        except (TimeoutError, ValueError):
            pass
        finally:
            link.close()
    return found


def pick_board(args):
    """(port, is_recovery_link, code)."""
    try:
        ports = F.list_ports()
    except ImportError:
        say("pyserial is not installed: pip install pyserial")
        return None, False, 12
    named_com = args.port and any(p.device == args.port and p.kind == "hidfi" for p in ports)
    rec = [] if named_com else recovery_links(ports)
    if args.port and not named_com:
        hit = [p for p in rec if p.device == args.port]
        if hit:
            return hit[0], True, 0
    elif not args.port:
        if args.serial:
            want = args.serial.replace(":", "").lower()
            rec = [p for p in rec if (p.serial or "").replace(":", "").lower().startswith(want)]
        if len(rec) == 1:
            say(f"A board in recovery mode: {rec[0].device} (MAC {rec[0].serial or '?'})")
            return rec[0], True, 0
        if len(rec) > 1:
            say(f"{len(rec)} boards are in recovery mode, so none was picked:")
            for i, p in enumerate(rec):
                say(f"  {i + 1}. {p.device}  MAC {p.serial or '?'}")
            if sys.stdin.isatty() and not os.environ.get("CI"):
                ans = input(f"   Which board? [1-{len(rec)}, Enter = none]: ").strip()
                if ans.isdigit() and 1 <= int(ans) <= len(rec):
                    return rec[int(ans) - 1], True, 0
                say("Nothing chosen, nothing changed.")
            else:
                say("Pick one with --port <port> or --serial <its MAC>.")
            return None, False, 10
    targets, lines, code = F.choose(ports, args.port, args.serial)
    for l in lines:
        say(l)
    if code == 10 and sys.stdin.isatty() and not os.environ.get("CI"):
        cands = targets
        ans = input(f"   Which board? [1-{len(cands)}, Enter = none]: ").strip()
        if ans.isdigit() and 1 <= int(ans) <= len(cands):
            return cands[int(ans) - 1], False, 0
        say("Nothing chosen, nothing changed.")
        return None, False, 10
    if code == 4:
        print()
        say(*RECOVERY_HELP)
    if code:
        return None, False, code
    return targets[0], False, 0


def close_recovery(link, keep_open):
    """A recovery link is closed when the tool is done: the board restarts normally."""
    if not link.recovery:
        return
    if keep_open:
        say("Recovery mode stays open (it closes by itself 5 minutes after the last command).")
        return
    try:
        r = link.cmd({"cmd": "recovery_exit"})
        if r.get("reply") == "recovery_exit":
            say("Recovery mode closed: the board restarts normally.")
    except TimeoutError:
        say("Recovery mode did not answer the close; it closes by itself within 5 minutes.")


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


def home_line(st):
    """Where the board is on the home network - the answer to "what was its address
    again?", which is the other thing people come to this tool for."""
    name = f"http://{st['mdns']}.local/" if st.get("mdns") else ""
    if st.get("wifi_connected") and st.get("wifi_ip"):
        return f"Home network:  {st.get('wifi_ssid') or 'joined'} at http://{st['wifi_ip']}/" + (f"  or  {name}" if name else "")
    if st.get("sta_last_ip"):
        return f"Home network:  away - it was at http://{st['sta_last_ip']}/" + (f"; {name} finds it when it is back" if name else "")
    return ""


def show_password(ap, st=None):
    if ap.get("open"):
        say(f"WiFi network:  {ap.get('ssid')}", "Password:      none - the network is open")
    else:
        say(f"WiFi network:  {ap.get('ssid')}", f"Password:      {ap.get('password')}")
        if ap.get("generated"):
            say("(This board made this password for itself. You can keep it or set your own.)")
    if st and home_line(st):
        say(home_line(st))
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


FACTORY_LOSES = ("the access PIN and every setting", "its home WiFi networks", "stored passwords (the vault)",
                 "macros and saved PCs", "paired computers")


def factory_reset(link, ap, yes, interactive):
    """docs/FACTORY_RESET.md: everything a person set up goes; the board's own WiFi
    name and password and its firmware stay. Asked first unless --yes."""
    say("FACTORY RESET - everything on this board will be erased:", *("  - " + x for x in FACTORY_LOSES),
        "It keeps its own WiFi name, the WiFi password it came with, and its firmware.")
    if not yes:
        if not interactive:
            say("Not erased: add --yes to factory reset without being asked.")
            return 1
        if input("   Type yes to erase everything on this board: ").strip().lower() != "yes":
            say("Nothing changed.")
            return 1
    r = link.cmd({"cmd": "factory_reset", "confirm": True})
    if r.get("reply") != "factory_reset":
        say(f"Not erased: {r.get('message') or r.get('reply') or 'the board refused'}. Nothing changed.")
        return 1
    link.recovery = False          # it restarts: there is no recovery mode left to close
    ssid = (ap or {}).get("ssid") or "its own WiFi"
    say(f"Erased. The board is restarting as new: join {ssid} again to set it up.")
    return 0


def menu(link, st, ap, vl, entries):
    say(f"Board: {st.get('firmware')}  WiFi: {ap.get('ssid')}  access PIN: {'set' if st.get('auth_set') else 'not set'}",
        f"Stored passwords: {vault_fate(vl, entries)}")
    if home_line(st):
        say(home_line(st))
    print()
    print("   1  Show this board's WiFi name and password")
    print("   2  Set a new WiFi password")
    print("   3  Reset the access PIN (or turn it off)")
    print("   4  Factory reset: erase everything on the board")
    print("   5  Quit")
    choice = input("   Choose 1-5: ").strip()
    if choice == "1":
        return show_password(ap, st)
    if choice == "2":
        a = input("   New WiFi password (8-63 characters): ")
        if input("   Type it again: ") != a:
            say("They did not match. Nothing changed.")
            return 1
        return set_password(link, ap, a)
    if choice == "3":
        p = input("   New 4 digit access PIN, or Enter to turn the PIN off: ").strip()
        return reset_pin(link, vl, entries, p, False, True)
    if choice == "4":
        return factory_reset(link, ap, False, True)
    say("Nothing changed.")
    return 0


def main(argv=None):
    ap_ = argparse.ArgumentParser(description="Recover a HID-Fi board over USB: its COM cable, or recovery mode.")
    ap_.add_argument("--port", default=os.environ.get("HIDFI_PORT") or None)
    ap_.add_argument("--serial", default=os.environ.get("HIDFI_SERIAL") or None)
    ap_.add_argument("--show-password", action="store_true")
    ap_.add_argument("--set-password")
    ap_.add_argument("--reset-pin")
    ap_.add_argument("--erase-vault", action="store_true", help="allow a PIN reset to erase an unguarded vault")
    ap_.add_argument("--keep-open", action="store_true", help="leave recovery mode open when done (it closes itself after 5 minutes)")
    ap_.add_argument("--factory-reset", action="store_true", help="erase everything a person set up (docs/FACTORY_RESET.md)")
    ap_.add_argument("--yes", action="store_true", help="with --factory-reset: do not ask")
    a = ap_.parse_args(argv)

    print("== HID-Fi recovery (USB)")
    board, rec, code = pick_board(a)
    if code:
        return code
    say(f"Board on {board.device} (serial {board.serial or '?'})" + (" - recovery mode" if rec else ""))
    try:
        link = BoardLink(board.device, recovery=rec)
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
            return show_password(ap, st)
        if a.set_password is not None:
            return set_password(link, ap, a.set_password)
        if a.reset_pin is not None:
            return reset_pin(link, vl, entries, a.reset_pin, a.erase_vault, False)
        if a.factory_reset:
            return factory_reset(link, ap, a.yes, sys.stdin.isatty())
        if not sys.stdin.isatty():
            say("No choice given. Use --show-password, --set-password, --reset-pin or --factory-reset, or run it in a terminal.")
            return 1
        return menu(link, st, ap, vl, entries)
    except TimeoutError as e:
        say(f"The board did not answer ({e})." + (" Recovery mode may have just closed: hold BOOT 8 s again."
                                                if link.recovery else " Is the COM cable in the board's COM port?"))
        return 1
    finally:
        try:
            close_recovery(link, a.keep_open)
        finally:
            link.close()


if __name__ == "__main__":
    sys.exit(main())
