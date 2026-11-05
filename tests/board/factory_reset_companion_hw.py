"""The Companion's own factory reset on the test board, end to end (docs/FACTORY_RESET.md):
the real service (examples/hw_app with HIDFI_HW_FACTORY=1: real USB listing, real network,
the real factory worker and serial ports) pairs through the board's real dashboard, then
resets the board the paired way - a signed request, the board back with its USB link, the
erase down that cable - and reports every step. Then the board is checked as new over COM
and its saved settings are written back.

Needs HIDFI_PIN, HIDFI_ONLY_BOARD (the test board's MAC), HIDFI_NVS_BACKUP (see
factory_reset_hw.py) and a release build of hw_app (HW_APP=path). Test board only.

Run: HIDFI_PIN=... HIDFI_ONLY_BOARD=<MAC> HIDFI_NVS_BACKUP=dir python3 tests/board/factory_reset_companion_hw.py COM-PORT
"""
import glob, json, os, pathlib, queue, re, subprocess, sys, tempfile, threading, time

import serial
from playwright.sync_api import sync_playwright

COM = sys.argv[1]
PIN = os.environ["HIDFI_PIN"]
ONLY = os.environ["HIDFI_ONLY_BOARD"].replace(":", "").upper()
BACKUP = pathlib.Path(os.environ["HIDFI_NVS_BACKUP"])
HW_APP = os.environ.get("HW_APP") or str(pathlib.Path(__file__).resolve().parents[2] / "companion/target/release/examples/hw_app")
ESPTOOL = sorted(glob.glob(os.path.expanduser("~/Library/Arduino15/packages/esp32/tools/esptool_py/*/esptool")))[-1]
ok_all = True


def check(name, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, detail, flush=True)


app = subprocess.Popen([HW_APP, tempfile.mkdtemp(prefix="hidfi-fr-")], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, text=True,
                       env=dict(os.environ, HIDFI_HW_FACTORY="1", HIDFI_ONLY_BOARD=ONLY))
latest, lines = {}, queue.Queue()


def pump():
    for ln in app.stdout:
        v = json.loads(ln)
        latest.update(v)
        lines.put(v)


threading.Thread(target=pump, daemon=True).start()


def wait_app(pred, secs, what):
    end = time.time() + secs
    while time.time() < end:
        if latest and pred(latest):
            return dict(latest)
        try:
            lines.get(timeout=0.2)
        except queue.Empty:
            pass
    raise SystemExit(f"FAIL waiting for {what}; last: { {k: latest.get(k) for k in ('board', 'session', 'factory')} }")


def sign_in(page):
    page.wait_for_selector("#sheet.on .pinbox", timeout=10000)
    page.locator("#sheet.on .pinbox").first.click()
    page.wait_for_timeout(150)
    for d in PIN:
        page.keyboard.type(d)
        page.wait_for_timeout(50)


def com_status():
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = COM, 115200, 0.3
    s.dtr = s.rts = False
    s.open()                                   # resets the board: let it boot
    end = time.time() + 14
    while time.time() < end:
        ln = s.readline().decode(errors="ignore").strip()
        if '"event":"sta_online"' in ln:
            break
    time.sleep(1.5)
    s.reset_input_buffer()
    out = {}
    for cmd, want in (("status", "status"), ("ap_password", "ap_password")):
        s.write((json.dumps({"cmd": cmd}) + "\n").encode())
        end = time.time() + 4
        while time.time() < end:
            ln = s.readline().decode(errors="ignore").strip()
            if ln.startswith("{") and f'"reply":"{want}"' in ln:
                out[cmd] = json.loads(ln)
                break
    s.close()
    return out


try:
    v = wait_app(lambda v: "Found" in v["board"] or "WaitingForApproval" in v["session"], 40, "the board")
    url = "http://" + re.search(r"(\d+\.\d+\.\d+\.\d+)", v["board"]).group(1)
    wait_app(lambda v: "WaitingForApproval" in v["session"], 30, "the app asking to pair")
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome")
        pg = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True).new_page()
        pg.goto(url + "/#screen")
        sign_in(pg)
        pg.wait_for_selector("[data-scrpair]", timeout=20000)
        pg.tap("[data-scrpair]")
        v = wait_app(lambda v: "ShowCode" in v["session"], 15, "the code on the computer")
        code = re.search(r'"(\d{6})"', v["session"]).group(1)
        pg.wait_for_selector("[data-scrcode]", timeout=10000)
        pg.tap("[data-scrcode]")
        pg.keyboard.type(code, delay=40)
        pg.tap("[data-scrok]")
        wait_app(lambda v: v["session"] == "Online", 15, "paired and online")
        check("the real Companion paired with the board through its dashboard", True)
        b.close()
    t0 = time.time()
    app.stdin.write(f"factory-reset {ONLY}\n")
    app.stdin.flush()
    v = wait_app(lambda v: any(f["mac"] == ONLY and f["result"] is not None for f in v.get("factory") or []),
                 90, "the factory reset's result")
    f = next(f for f in v["factory"] if f["mac"] == ONLY)
    check("the Companion chose the paired way (this computer paired, the board on its USB)", f["path"] == "paired", f["path"])
    check("every step done: asked, back with its USB link, erased down the cable, restarted as new",
          f["result"] is True and f["steps"] == ["done"] * 4, {"steps": f["steps"], "result": f["result"]})
    check("the note says what to do next", "ESP32-HID-" + ONLY[-6:] in f["note"], f["note"])
    print(f"   took {time.time() - t0:.1f} s from the click to the result")
    out = com_status()
    st, ap = out.get("status", {}), out.get("ap_password", {})
    facts = {"fresh": st.get("fresh"), "pin": st.get("auth_set"), "home_wifi": st.get("wifi_connected"),
             "own_password": ap.get("generated")}
    check("the board is as new, with its own WiFi password", facts == {"fresh": True, "pin": False, "home_wifi": False,
                                                                       "own_password": True}, facts)
finally:
    try:
        app.stdin.write("quit\n")
        app.stdin.flush()
        app.wait(5)
    except Exception:
        app.kill()
    r = subprocess.run([ESPTOOL, "--chip", "esp32s3", "--port", COM, "--baud", "921600", "--before", "default-reset",
                        "--after", "hard-reset", "write-flash", "0x9000", str(BACKUP / "nvs.bin"),
                        "0xc90000", str(BACKUP / "nvs_ext.bin")], capture_output=True, text=True)
    st = com_status().get("status", {})
    check("restored: the saved settings are back (PIN set, not fresh)",
          r.returncode == 0 and st.get("auth_set") is True and st.get("fresh") is False,
          {"esptool": r.returncode, "pin": st.get("auth_set"), "home_wifi": st.get("wifi_connected")})
print("RESULT:", "PASS" if ok_all else "FAIL")
sys.exit(0 if ok_all else 1)
