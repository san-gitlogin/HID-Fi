"""D-14 on the real board with the real Companion service: one home, two subnets.

Seen at the owner's home (2026-11-23): "ARVISANTU" is the main router (192.168.1.x),
"ARVISANTU C6" an extender with a subnet of its own (192.168.0.x) behind it. The Mac
and the board each move between them, and the
Companion said "not on this computer's WiFi - set it up from the board's own WiFi".
The story, against the real service (hw_app: real USB listing, network and mDNS):

  1. paired with the board on this computer's network: the card is the ordinary one
  2. the board moves to the home's other network (its old address goes stale, mDNS
     cannot follow): the Companion finds it again by itself and its card names both
     networks, with "Bring the board to this computer's network"
  3. that button: the board comes to this computer's network, and the card is the
     ordinary one again

Needs: the board on COM (pairing is confirmed over USB, moves are made over USB) and on
the native USB port, HIDFI_ONLY_BOARD=<its MAC>, a second saved network this computer
can route to. The test pairing is removed and the board's preferred network put back.
Run: cargo build --release -p hidfi-companion --example hw_app
     HIDFI_ONLY_BOARD=<MAC> python3 companion/tests/two_networks.py
"""
import json
import os
import pathlib
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time

import serial
import serial.tools.list_ports

ROOT = pathlib.Path(__file__).resolve().parents[1]
HW_APP = os.environ.get("HW_APP") or str(ROOT / "target/release/examples/hw_app")
MAC = os.environ.get("HIDFI_ONLY_BOARD", "").replace(":", "").upper() or sys.exit("set HIDFI_ONLY_BOARD to the board's MAC")
# It moves the board between the owner's real routers: never automatic (DECISIONS.md D-17).
if not os.environ.get("HIDFI_REAL_MOVES"):
    sys.exit("SKIP real moves between the owner's routers are opt-in: HIDFI_REAL_MOVES=1 (D-17)")
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(f"{'ok  ' if cond else 'FAIL'} {name} {detail}", flush=True)


# ---- the board over USB (COM) ----------------------------------------------------------------
port = next((p.device for p in serial.tools.list_ports.comports() if p.vid == 0x1A86), None) or sys.exit("no board on COM")
ser = serial.Serial()
ser.port, ser.baudrate, ser.timeout = port, 115200, 0.2
ser.dtr = ser.rts = False                      # before open: they drive the board's reset
ser.open()
replies, lock = {}, threading.Lock()


def _read():
    while True:
        try:
            line = ser.readline().decode(errors="ignore").strip()
        except Exception:
            return
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "reply" in d:
                with lock:
                    replies[d["reply"]] = d


threading.Thread(target=_read, daemon=True).start()


def cmd(o, want, wait=8):
    with lock:
        replies.pop(want, None)
    ser.write((json.dumps(o) + "\n").encode())
    end = time.time() + wait
    while time.time() < end:
        with lock:
            if want in replies:
                return replies[want]
        time.sleep(0.05)
    return {}


def board_on(pred, what, timeout=90):
    end = time.time() + timeout
    st = {}
    while time.time() < end:
        st = cmd({"cmd": "status"}, "status", 3)
        if pred(st):
            return st
        time.sleep(2)
    sys.exit(f"the board is not {what}: {st.get('wifi_ssid')} {st.get('wifi_ip')}")


# ---- the real service --------------------------------------------------------------------------
store = tempfile.mkdtemp(prefix="hidfi-two-nets-")
app = subprocess.Popen([HW_APP, store], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       text=True, env={**os.environ, "HIDFI_ONLY_BOARD": MAC})
views = queue.Queue()
last = {}
threading.Thread(target=lambda: [views.put(json.loads(l)) for l in app.stdout if l.startswith("{")], daemon=True).start()


def card(pred, what, timeout=150):
    global last
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = views.get(timeout=1)
        except queue.Empty:
            continue
        b = next((x for x in v.get("boards", []) if x["mac"] == MAC), None)
        if b:
            last = b
            if pred(b):
                return b
    sys.exit(f"FAIL {what} - not within {timeout}s; last card: {json.dumps(last)[:600]}")


start_pref = None
try:
    st = board_on(lambda s: s.get("wifi_connected"), "on a home network", 60)
    start_pref = st.get("wifi_preferred") or st.get("wifi_ssid")
    home, home_ip = st["wifi_ssid"], st["wifi_ip"]
    # pair: the service asks, the board is told yes over USB, then the code it shows
    card(lambda b: "WaitingForApproval" in b["session"], "asking to pair")
    pend = [p for p in cmd({"cmd": "companion_list"}, "companion_list").get("pending", []) if p.get("name") == "HID-Fi hardware test"]
    assert pend, "the service's pairing request is not on the board"
    cmd({"cmd": "companion_pair", "id": pend[0]["id"]}, "companion_pair")
    b = card(lambda b: "ShowCode" in b["session"], "showing the code")
    code = re.search(r'"(\d{6})"', b["session"]).group(1)
    r = cmd({"cmd": "companion_pair", "id": pend[0]["id"], "code": code}, "companion_pair")
    check("paired over USB with the code the Companion showed", r.get("state") == "paired", r.get("state"))

    b = card(lambda b: b["session"] == "Online" and (b.get("net") or {}).get("here"), "online on one network")
    check("1. together: the card is the ordinary one", not any("two networks" in l for l in b["lines"]), b["lines"])

    nets = cmd({"cmd": "wifi_networks"}, "wifi_networks").get("networks", [])
    others = [n["ssid"] for n in nets if n.get("proven") and n["ssid"] != home]
    moved = None
    for ssid in others:
        cmd({"cmd": "wifi_prefer", "ssid": ssid, "now": True, "force": True}, "wifi_prefer")
        try:
            s2 = board_on(lambda s: s.get("wifi_ssid") == ssid and s.get("wifi_connected"), f"on {ssid}", 45)
        except SystemExit:
            continue
        if s2["wifi_ip"].rsplit(".", 1)[0] != home_ip.rsplit(".", 1)[0]:
            moved = (ssid, s2)
            break
    if not moved:
        sys.exit("SKIP no second saved network on another subnet in range")
    ssid, s2 = moved
    t0 = time.time()
    b = card(lambda b: (b.get("net") or {}).get("board") == ssid and not b["net"]["here"]
             or b["board"] == "NotOnNetwork", f"found again on {ssid}, or said to be out of reach")
    if b["board"] == "NotOnNetwork":
        # This computer cannot route into the board's new network (an extender's own
        # subnet behind the main router is reachable only from inside it): the card must
        # say the board left this computer's network - never "Connecting..." for ever,
        # never "join this computer to" the network it is on (2026-11-23).
        want = f"Not reachable from this computer. The board was on {home} with this computer, and has left it"
        check(f"2. out of reach on {ssid}: the card says the board left this computer's network ({time.time() - t0:.0f} s)",
              b["lines"] and b["lines"][0] == want, b["lines"])
        check("   and how to bring it back, not to move this computer", any(f"choose {home}" in l for l in b["lines"])
              and not any("join this computer to" in l for l in b["lines"]), b["lines"])
        cmd({"cmd": "wifi_prefer", "ssid": home, "now": True, "force": True}, "wifi_prefer")
        board_on(lambda s: s.get("wifi_ssid") == home and s.get("wifi_connected"), f"back on {home}")
        t1 = time.time()
        b = card(lambda b: b["session"] == "Online" and (b.get("net") or {}).get("here"), "together again", 180)
        check(f"3. moved back over USB: the Companion finds it by itself, the ordinary card ({time.time() - t1:.0f} s)",
              not any("two networks" in l or "Not reachable" in l for l in b["lines"]), b["lines"])
        print("SKIP Bring from the Companion: this computer cannot reach the board on", ssid)
        raise SystemExit(0 if ok else 1)
    check(f"2. the Companion found the board again on the other network by itself ({time.time() - t0:.0f} s)", True,
          b["board"])
    want = f"This computer is on {home}, the board is on {ssid}: two networks"
    check("   its card names both networks", want in b["lines"], b["lines"])

    app.stdin.write(f"bring {MAC}\n")
    app.stdin.flush()
    b = card(lambda b: b.get("bring") is not None, "bring answered", 60)
    check("3. Bring the board here: the board answers", "Moving" in json.dumps(b["bring"]) or "Here" in json.dumps(b["bring"]),
          b["bring"])
    s3 = board_on(lambda s: s.get("wifi_ssid") == home and s.get("wifi_connected"), f"back on {home}")
    b = card(lambda b: b["session"] == "Online" and (b.get("net") or {}).get("here"), "together again")
    check("   both on one network again, the ordinary card", (b["net"]["board"], b["net"]["computer"]) == (home, home)
          and not any("two networks" in l for l in b["lines"]), b["lines"])
finally:
    try:
        app.stdin.write("quit\n")
        app.stdin.flush()
        app.wait(5)
    except Exception:
        app.kill()
    for p in cmd({"cmd": "companion_list"}, "companion_list").get("paired", []):
        if p.get("name") == "HID-Fi hardware test":
            cmd({"cmd": "companion_unpair", "id": p["id"]}, "companion_unpair")
    if start_pref:
        cmd({"cmd": "wifi_prefer", "ssid": start_pref, "now": True, "force": True}, "wifi_prefer")
    print("cleaned up: the test pairing removed, the board's preferred network put back")

sys.exit(0 if ok else 1)
