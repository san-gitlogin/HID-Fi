"""The network your devices use (docs/DECISIONS.md D-02), on a real board in a real
home with several saved networks on different routers. Test board only.

The story, from a board nobody has used over any of its networks:
  1. it starts on the strongest saved network (D-01);
  2. nobody reaches it there - this computer is on another router - so after 5
     minutes it tries another saved network by itself ("looking");
  3. this computer finds it there and signs in over the home network: the board
     marks that network as where it is used;
  4. restarted, it goes straight to that network ("preferred"), though another
     is stronger.

The COM port is opened once (opening it restarts the board) and read throughout.
Prints network names, choices and timings - never a password or the PIN.

"Where this computer is" is its subnet, not a name: a router and its extender are
one network (on 2026-11-06 the board looked its way from ARVISANTU C6 to
ARVISANTU, the same network as this computer's ARVISANTU EXT - right, and a name
check called it wrong).

Needs: HIDFI_PIN (the board's access PIN), HIDFI_ONLY_BOARD (the test board's MAC).
A board that already knows where it is used shows only step 4; steps 1-2 need one
with no history. Since D-09 a board seeds that history from the network it was last
on at its first start, so steps 1-2 show only on a board whose saved networks were
all added since it last started.
Run: python3 tests/board/net_preference_hw.py /dev/cu.usbmodemXXXX
"""
import json
import os
import socket
import sys
import time

import serial
from websockets.sync.client import connect

COM = sys.argv[1]
PIN = os.environ["HIDFI_PIN"]

def my_subnet():
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    u.connect(("10.255.255.255", 1))
    return u.getsockname()[0].rsplit(".", 1)[0]


HOME = my_subnet()                         # e.g. 192.168.1


def here(st):
    """The board is on a network this computer reaches."""
    return (st.get("wifi_ip") or "").rsplit(".", 1)[0] == HOME
ONLY = os.environ["HIDFI_ONLY_BOARD"].replace(":", "").upper()
ok_all = True


def check(name, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, detail, flush=True)


s = serial.Serial()
s.port, s.baudrate, s.timeout = COM, 115200, 0.3
s.dtr = s.rts = False
s.open()                                   # restarts the board: the story starts here
t0 = time.time()


def lines(until, stop=None):
    """Board output until a time, or until stop(line) is true; returns the stopping line."""
    while time.time() < until:
        ln = s.readline().decode(errors="ignore").strip()
        if not ln:
            continue
        if ln.startswith("[NET]") or ln.startswith("[WIFI]") or '"event":"sta_online"' in ln or '"wifi_moving"' in ln:
            print(f"   {time.time() - t0:6.0f}s  {ln[:150]}", flush=True)
        if stop and stop(ln):
            return ln
    return None


def status():
    s.reset_input_buffer()
    s.write(b'{"cmd":"status"}\n')
    end = time.time() + 4
    while time.time() < end:
        ln = s.readline().decode(errors="ignore").strip()
        if ln.startswith("{") and '"reply":"status"' in ln:
            return json.loads(ln)
    return {}


def online(ln):
    return '"event":"sta_online"' in ln


# 1. the first choice, nobody having used it
ln = lines(time.time() + 40, online)
st = status()
check("the board's MAC is the test board's", st.get("mac", "").replace(":", "") == ONLY, st.get("mac"))
first = st.get("wifi_ssid")
fresh = not st.get("wifi_preferred")
if not fresh:
    print(f"   it already knows where it is used ({st.get('wifi_preferred')}): steps 1-2 need a board with no history")
else:
    check("1. a board nobody has used starts on the strongest", st.get("wifi_choice") == "strongest",
          {"on": first, "choice": st.get("wifi_choice")})
if not fresh:
    pass
elif here(st):
    print("   it chose this computer's network already - the looking step cannot be shown here; move the board"
          " or use a network that is stronger where it sits")
else:
    # 2. nobody comes; after 5 minutes (+ up to 2 for the next look) it tries another
    moved = lines(time.time() + 9 * 60, lambda l: "Nobody came" in l)
    check("2. nobody reached it, so it went looking", bool(moved), f"after {time.time() - t0:.0f}s")
    ln = lines(time.time() + 60, online)
    st = status()
    check("   and landed on a network this computer reaches", here(st) and st.get("wifi_choice") == "strongest",
          {"on": st.get("wifi_ssid"), "choice": st.get("wifi_choice"), "subnet": HOME})

# 3. this computer signs in over the home network
st = status()
ip = st.get("wifi_ip") or st.get("sta_last_ip")
with connect(f"ws://{ip}:81/", open_timeout=8) as ws:
    ws.recv(timeout=3)                     # auth_required
    ws.send(json.dumps({"cmd": "auth", "token": PIN}))
    end, authed = time.time() + 5, False
    while time.time() < end and not authed:
        m = ws.recv(timeout=5)
        authed = isinstance(m, str) and '"auth_ok"' in m
check("3. signed in from this computer over the home network", authed, ip)
lines(time.time() + 2)
st = status()
used_here = st.get("wifi_ssid")
check("   the board now prefers the network it was reached over", st.get("wifi_preferred") == used_here, st.get("wifi_preferred"))

# 4. restarted: straight to the preferred network
s.rts = True
time.sleep(0.2)
s.rts = False                              # EN pulse: a restart
ln = lines(time.time() + 40, online)
st = status()
check("4. restarted, it goes straight to where it is used", here(st) and st.get("wifi_choice") == "preferred",
      {"on": st.get("wifi_ssid"), "choice": st.get("wifi_choice"), "preferred": st.get("wifi_preferred")})
s.close()
print("RESULT:", "PASS" if ok_all else "FAIL")
sys.exit(0 if ok_all else 1)
