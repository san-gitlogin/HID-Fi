"""Recovery mode, 50 times in a row on the test board (docs/RECOVERY_MODE.md, H9),
with what H3 asks of each recovery boot: WiFi joins again (within 20 s of letting
go; the time is recorded), HID ready, and a pointer move answered over the link.

Each cycle: BOOT held 8.6 s through the COM port's DTR (H0) -> "ready" while held
-> the recovery link (303a:1001, serial = MAC) -> status over the link -> a net-zero
mouse move over the link -> recovery_exit -> the link gone -> a normal boot again.
One COM open for the whole run (each open resets the board). Prints timings and
yes/no facts only - never a reply's contents.

Run (test board only, never the owner's): python3 tests/board/recovery_soak.py COM-PORT [cycles]
"""
import json, statistics, sys, time
import serial, serial.tools.list_ports

COM = sys.argv[1]
CYCLES = int(sys.argv[2]) if len(sys.argv) > 2 else 50


def links(mac):
    return [p.device for p in serial.tools.list_ports.comports()
            if p.vid == 0x303A and (p.serial_number or "").upper().replace(":", "") == mac]


class Lines:
    def __init__(self, s):
        self.s, self.buf = s, b""

    def __iter__(self):
        self.buf += self.s.read(8192)
        while b"\n" in self.buf:
            ln, self.buf = self.buf.split(b"\n", 1)
            yield ln.decode(errors="ignore").strip()


def events_until(lines, pred, secs):
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


def ask(link, cmd, want, secs=4):
    link.write((json.dumps(cmd) + "\n").encode())
    return events_until(Lines(link), lambda d: d.get("reply") == want or d.get("status") == "error", secs)


com = serial.Serial()
com.port, com.baudrate, com.timeout = COM, 115200, 0.02
com.dtr = com.rts = False
com.open()
lines = Lines(com)
events_until(lines, lambda d: False, 12)            # opening resets it: let it boot and join
com.write(b'{"cmd":"status"}\n')
st = events_until(lines, lambda d: d.get("reply") == "status", 5)
assert st, "the board does not answer on COM"
MAC = st["mac"].replace(":", "").upper()
assert not links(MAC), "a recovery link is already open"
print(f"board {MAC[-6:]}, firmware {st.get('firmware')}, {CYCLES} cycles")

times = {"ready": [], "link": [], "handshake": [], "wifi": [], "closed": []}
failures = []
for n in range(1, CYCLES + 1):
    why = None
    t0 = time.time()
    com.dtr = True                                    # BOOT held (GPIO0 low, EN high)
    ready = None
    try:
        ready = events_until(lines, lambda d: d.get("event") == "recovery", 9.5)
        while time.time() - t0 < 8.6:
            list(lines)
            time.sleep(0.02)
    finally:
        com.dtr = False                               # always let go
    t_rel = time.time()
    if not ready or ready.get("state") != "ready":
        why = f"no 'ready' while held ({ready and ready.get('state')})"
    else:
        times["ready"].append(round(ready and (t_rel - t0) or 0, 2))
        port = None
        end = time.time() + 15
        while time.time() < end and not port:
            port = (links(MAC) or [None])[0]
            time.sleep(0.1)
        if not port:
            why = "no recovery link within 15 s of letting go"
        else:
            times["link"].append(round(time.time() - t_rel, 2))
            link = serial.Serial()
            link.port, link.baudrate, link.timeout = port, 115200, 0.05
            link.dtr, link.rts = True, False          # the link answers with DTR raised
            ta = time.time()
            link.open()
            time.sleep(0.2)
            s = ask(link, {"cmd": "status"}, "status")
            if not s:
                why = "no status over the link"
            else:
                times["handshake"].append(round(time.time() - ta, 2))
                # WiFi joins a few seconds into any boot (about 8 s on a normal one):
                # wait for it, and record how long it took after letting go.
                end = time.time() + 20
                while not s.get("wifi_connected") and time.time() < end:
                    time.sleep(0.5)
                    s = ask(link, {"cmd": "status"}, "status") or s
                if s.get("wifi_connected"):
                    times["wifi"].append(round(time.time() - t_rel, 2))
                facts = {"recovery": s.get("recovery") is True, "wifi": bool(s.get("wifi_connected")),
                         "hid": bool(s.get("hid_ready", True)), "left": (s.get("recovery_left_s") or 0) > 0}
                m1 = ask(link, {"cmd": "mouse_move", "dx": 3, "dy": 0}, "mouse_moved")
                m2 = ask(link, {"cmd": "mouse_move", "dx": -3, "dy": 0}, "mouse_moved")
                facts["move"] = bool(m1 and m2 and m1.get("status") != "error" and m2.get("status") != "error")
                if not all(facts.values()):
                    why = f"in recovery: {facts}"
                tc = time.time()
                # The recovery boot's own events sit unread on COM: drop them, so the
                # boot waited for below is the one after closing. (Taking the stale one
                # started the next press while the board was still restarting - every
                # other cycle then saw no "ready" - and a press held through a restart
                # can leave the chip in its ROM loader.)
                list(lines)
                lines.buf = b""
                ask(link, {"cmd": "recovery_exit"}, "recovery_exit")
                try:
                    link.close()
                except serial.SerialException:
                    pass
                end = time.time() + 15
                while time.time() < end and links(MAC):
                    time.sleep(0.1)
                if links(MAC):
                    why = why or "the link stayed after recovery_exit"
                else:
                    times["closed"].append(round(time.time() - tc, 2))
    boot = events_until(lines, lambda d: d.get("event") == "boot", 15)
    events_until(lines, lambda d: d.get("event") == "sta_online", 15)
    events_until(lines, lambda d: False, 2)           # settled before the next press
    if not boot:
        why = why or "no normal boot after closing"
    print(f"  {n:2d}/{CYCLES} {'ok' if not why else 'FAIL: ' + why}  ({time.time() - t0:.1f} s)", flush=True)
    if why:
        failures.append((n, why))
        if len(failures) >= 3:
            break
com.close()
for k, v in times.items():
    if v:
        print(f"{k:>9}: median {statistics.median(v):.2f} s, worst {max(v):.2f} s  (n={len(v)})")
print("RESULT:", "PASS" if not failures else f"FAIL {failures}")
sys.exit(0 if not failures else 1)
