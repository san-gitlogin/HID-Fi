"""Factory reset on the test board, every way (docs/FACTORY_RESET.md), with the board's
settings saved first and written back after each erase - it ends as it began.

  com        over the COM cable, with confirm (and refused without it)
  recovery   recovery mode opened by a real 8.6 s BOOT hold (the COM port's DTR), over the link
  request    a computer that is not paired asks; the owner approves with the PIN over WiFi
  paired     a computer pairs for real (the app's protocol, tests/support/companion_sim.py),
             asks with a signed request, and erases down the USB link it opened; that link
             refuses the WiFi password and every other command

Prints facts and yes/no only - never a password or a PIN. Needs the access PIN in HIDFI_PIN
and the NVS backup (read-flash 0x9000 0x5000 nvs.bin, 0xc90000 0x40000 nvs_ext.bin) in
HIDFI_NVS_BACKUP. Test board only, never the owner's.

Run: HIDFI_PIN=... HIDFI_NVS_BACKUP=dir python3 tests/board/factory_reset_hw.py COM-PORT [cases...]
"""
import glob, json, os, pathlib, subprocess, sys, time, urllib.request

import serial, serial.tools.list_ports
import websocket

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from support import companion_sim as cs  # noqa: E402

COM = sys.argv[1]
CASES = sys.argv[2:] or ["com", "recovery", "request", "paired"]
PIN = os.environ["HIDFI_PIN"]
BACKUP = pathlib.Path(os.environ["HIDFI_NVS_BACKUP"])
ESPTOOL = sorted(glob.glob(os.path.expanduser("~/Library/Arduino15/packages/esp32/tools/esptool_py/*/esptool")))[-1]
ok_all = True


def check(name, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, detail, flush=True)


class Lines:
    def __init__(self, s):
        self.s, self.buf = s, b""

    def __iter__(self):
        self.buf += self.s.read(8192)
        while b"\n" in self.buf:
            ln, self.buf = self.buf.split(b"\n", 1)
            yield ln.decode(errors="ignore").strip()


def until(lines, pred, secs):
    end = time.time() + secs
    while time.time() < end:
        for ln in lines:
            if ln.startswith("{"):
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                if pred(d):
                    return d
    return None


def com_open(settle=True):
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = COM, 115200, 0.05
    s.dtr = s.rts = False
    s.open()
    ln = Lines(s)
    if settle:
        until(ln, lambda d: d.get("event") == "sta_online", 14)      # opening resets it: boot, and join if it can
        until(ln, lambda d: False, 1.5)
    return s, ln


def ask(s, ln, body, want, secs=5):
    s.write((json.dumps(body) + "\n").encode())
    return until(ln, lambda d: d.get("reply") in (want,) or (d.get("status") == "error" and "event" not in d), secs)


def status_after_boot(s, ln):
    until(ln, lambda d: d.get("event") == "boot", 15)
    until(ln, lambda d: False, 4)
    return ask(s, ln, {"cmd": "status"}, "status")


def fresh_facts(s, ln, st):
    ap = ask(s, ln, {"cmd": "ap_password"}, "ap_password") or {}
    return {"fresh": st.get("fresh"), "pin": st.get("auth_set"), "home_wifi": st.get("wifi_connected"),
            "own_name": (st.get("ap_ssid") or "").startswith("ESP32-HID-"), "own_password": ap.get("generated")}


def expect_fresh(label, s, ln):
    st = status_after_boot(s, ln) or {}
    f = fresh_facts(s, ln, st)
    check(f"{label}: the board is as new, with its own name and WiFi password",
          f == {"fresh": True, "pin": False, "home_wifi": False, "own_name": True, "own_password": True}, f)


def restore():
    """Write the saved settings back and prove the board is as it was."""
    r = subprocess.run([ESPTOOL, "--chip", "esp32s3", "--port", COM, "--baud", "921600", "--before", "default-reset",
                        "--after", "hard-reset", "write-flash", "0x9000", str(BACKUP / "nvs.bin"),
                        "0xc90000", str(BACKUP / "nvs_ext.bin")], capture_output=True, text=True)
    s, ln = com_open()
    st = ask(s, ln, {"cmd": "status"}, "status") or {}
    s.close()
    back = r.returncode == 0 and st.get("auth_set") is True and st.get("fresh") is False
    check("   restored: the saved settings are back (PIN set, not fresh)", back,
          {"esptool": r.returncode, "pin": st.get("auth_set"), "home_wifi": st.get("wifi_connected")})
    if not back:
        sys.exit("STOP: the board could not be restored - not running more cases")
    return st


def board_ip():
    s, ln = com_open()
    st = ask(s, ln, {"cmd": "status"}, "status") or {}
    s.close()
    return st.get("wifi_ip"), (st.get("mac") or "").replace(":", "").upper()


def ws_signed_in(ip):
    c = websocket.create_connection(f"ws://{ip}:81", timeout=6)
    c.settimeout(1)
    try:
        c.recv()
    except Exception:
        pass
    c.settimeout(6)
    c.send(json.dumps({"cmd": "auth", "token": PIN}))
    end = time.time() + 6
    while time.time() < end:
        m = c.recv()
        if isinstance(m, str) and "auth_" in m:
            assert "auth_ok" in m, "the PIN was not accepted"
            return c
    raise SystemExit("no answer to auth")


def ws_cmd(c, body, want, secs=6):
    c.send(json.dumps(body))
    end = time.time() + secs
    while time.time() < end:
        m = c.recv()
        if isinstance(m, str) and m.startswith("{"):
            d = json.loads(m)
            if d.get("reply") == want or (d.get("status") == "error" and "event" not in d):
                return d
    return None


def native_link(mac, secs=20):
    end = time.time() + secs
    while time.time() < end:
        for p in serial.tools.list_ports.comports():
            if p.vid == 0x303A and (p.serial_number or "").replace(":", "").upper() == mac:
                return p.device
        time.sleep(0.2)
    return None


def open_link(dev):
    k = serial.Serial()
    k.port, k.baudrate, k.timeout = dev, 115200, 0.05
    k.dtr, k.rts = True, False                     # the link answers a host that raised DTR
    k.open()
    time.sleep(0.2)
    return k, Lines(k)


# ---- the cases ---------------------------------------------------------------------------
def case_com():
    s, ln = com_open()
    r = ask(s, ln, {"cmd": "factory_reset"}, "factory_reset")
    check("com: without confirm it is refused", r and r.get("reply") == "confirm_needed", r and r.get("reply"))
    r = ask(s, ln, {"cmd": "factory_reset", "confirm": True}, "factory_reset")
    check("com: with confirm it erases and restarts", r and r.get("restart") is True, r and r.get("reply"))
    expect_fresh("com", s, ln)
    s.close()
    restore()


def case_recovery():
    s, ln = com_open()
    s.dtr = True                                   # BOOT held: GPIO0 low, EN high
    try:
        ready = until(ln, lambda d: d.get("event") == "recovery", 9.5)
        time.sleep(max(0, 8.6 - 8.0))
    finally:
        s.dtr = False
    check("recovery: 'ready' while BOOT was held", ready and ready.get("state") == "ready", ready)
    dev = native_link(ip_mac[1])
    check("recovery: the link appeared", dev is not None)
    k, kl = open_link(dev)
    st = ask(k, kl, {"cmd": "status"}, "status") or {}
    check("recovery: it is the pinhole's kind", st.get("recovery_kind") == "button", st.get("recovery_kind"))
    r = ask(k, kl, {"cmd": "factory_reset", "confirm": True}, "factory_reset")
    check("recovery: erased over the link", r and r.get("restart") is True, r and r.get("reply"))
    k.close()
    expect_fresh("recovery", s, ln)
    s.close()
    restore()


def case_request():
    ip, mac = ip_mac
    s, ln = com_open()                      # once, first: opening the COM port resets the board
    laptop = cs.FakeApp(cs.RealBoardHttp(ip), name="HID-Fi HW test laptop")
    rid = laptop.reset_request().get("request")
    check("request: a computer that is not paired can ask", bool(rid))
    st = json.load(urllib.request.urlopen(f"http://{ip}/api/status", timeout=5))
    check("request: the board's status shows who asks", (st.get("reset_request") or {}).get("name") == laptop.name)
    check("request: and waits", laptop.reset_request_status(rid).get("state") == "pending")
    c = ws_signed_in(ip)
    d = ws_cmd(c, {"cmd": "factory_reset_decline", "request": rid}, "factory_reset_decline")
    check("request: the owner can decline", d and d.get("status") == "ok")
    check("request: declined is what the asker sees", laptop.reset_request_status(rid).get("state") == "declined")
    rid = laptop.reset_request().get("request")
    d = ws_cmd(c, {"cmd": "factory_reset", "confirm": True, "request": rid}, "factory_reset")
    check("request: approved with the PIN, it erases", d and d.get("restart") is True, d and d.get("reply"))
    time.sleep(1.0)
    seen = laptop.reset_request_status(rid).get("state")
    check("request: the asker sees 'approved' a second later (the 2.5 s hold)", seen == "approved", seen)
    expect_fresh("request", s, ln)
    s.close()
    restore()


def case_paired():
    ip, mac = ip_mac
    s, ln = com_open()                      # once, first: opening the COM port resets the board
    app = cs.FakeApp(cs.RealBoardHttp(ip), name="HID-Fi HW test PC")
    c = ws_signed_in(ip)
    assert app.hello()[1].get("state") == "pending"
    d = ws_cmd(c, {"cmd": "companion_pair", "id": app.id}, "companion_pair")
    check("paired: the dashboard approves the pairing", d and d.get("state") == "confirm", d and d.get("state"))
    app.advance()
    d = ws_cmd(c, {"cmd": "companion_pair", "id": app.id, "code": app.code}, "companion_pair")
    check("paired: paired with the six-digit code", d and d.get("state") == "paired", d and d.get("state"))
    bad = app._call(op="reset", id=app.id, chal="0" * 32, mac="0" * 64)[1]
    check("paired: a request with a wrong proof is refused", bad.get("reply") in ("stale_chal", "bad_mac"), bad.get("reply"))
    r = app.reset()
    tok = r.get("token")
    check("paired: a signed request gets a one-use token", bool(tok) and len(tok) == 32, r.get("reply"))
    dev = native_link(mac)
    check("paired: the board came back with its USB link", dev is not None)
    k, kl = open_link(dev)
    st = ask(k, kl, {"cmd": "status"}, "status") or {}
    check("paired: it is a reset link, 60 s", st.get("recovery_kind") == "reset" and 0 < (st.get("recovery_left_s") or 0) <= 60,
          {"kind": st.get("recovery_kind"), "left": st.get("recovery_left_s")})
    for cmd in ({"cmd": "ap_password"}, {"cmd": "set_auth", "token": "1111"}, {"cmd": "vault_list"}):
        r = ask(k, kl, cmd, cmd["cmd"])
        check(f"paired: the reset link refuses {cmd['cmd']}", r and r.get("reply") == "not_allowed", r and r.get("reply"))
    other = cs.FakeApp(cs.RealBoardHttp(ip), name="x")
    other.key, other.id = os.urandom(32), app.id
    r = ask(k, kl, {"cmd": "factory_reset", "proof": other.reset_proof(tok)}, "factory_reset")
    check("paired: a proof from another key is refused", r and r.get("reply") == "bad_proof", r and r.get("reply"))
    r = ask(k, kl, {"cmd": "factory_reset", "proof": app.reset_proof(tok)}, "factory_reset")
    check("paired: the token was one try - the right proof after a wrong one is refused too",
          r and r.get("reply") == "bad_proof", r and r.get("reply"))
    k.close()
    # The link closes itself 60 s after its last command; the board boots normally.
    end = time.time() + 75
    while time.time() < end and native_link(mac, secs=1):
        time.sleep(1)
    check("paired: the unused link closed by itself", native_link(mac, secs=1) is None)
    until(ln, lambda d: d.get("event") == "sta_online", 25)
    until(ln, lambda d: False, 2)
    r = app.reset()
    tok = r.get("token")
    dev = native_link(mac)
    k, kl = open_link(dev)
    r = ask(k, kl, {"cmd": "factory_reset", "proof": app.reset_proof(tok)}, "factory_reset")
    check("paired: the right proof down the cable erases", r and r.get("restart") is True, r and r.get("reply"))
    k.close()
    expect_fresh("paired", s, ln)
    s.close()
    restore()


ip_mac = board_ip()
print(f"board ...{ip_mac[1][-6:]} at {ip_mac[0]}; cases {CASES}")
for name in CASES:
    print(f"== {name}")
    globals()[f"case_{name}"]()
print("RESULT:", "PASS" if ok_all else "FAIL")
sys.exit(0 if ok_all else 1)
