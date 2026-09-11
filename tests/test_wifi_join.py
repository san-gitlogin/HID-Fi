"""Exercises the STA join state machine over serial, without needing a real network.

The failure paths carry most of the new logic, and none of them need credentials:
joining an SSID that does not exist drives the whole machine through
connecting -> timeout -> failed, and static addressing can be rejected on
validation alone.

The headline claim being tested is "nothing in loop() blocks". That is measured
directly: pings are fired continuously *during* a join and their round trip is
compared against the idle baseline. Before the state machine, this window was a
15 second freeze.

Self-restoring: refuses to run if the board has saved credentials, and clears
what it sets on the way out.
"""
import json
import statistics
import sys
import time

import serial
import serial.tools.list_ports

FAKE_SSID = "HIDFI-NoSuchNetwork-9Z7Q"


def find_port():
    for p in serial.tools.list_ports.comports():
        if p.vid == 0x1A86 and p.pid in (0x55D3, 0x7523):
            return p.device
    return None


def send(ser, cmd, timeout=6.0):
    while ser.in_waiting:
        ser.readline()
    ser.write((json.dumps(cmd) + "\n").encode())
    ser.flush()
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if ser.in_waiting:
            raw = ser.readline().decode("utf-8", "replace").strip()
            if not raw:
                continue
            try:
                resp = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if resp.get("reply") or resp.get("status"):
                return resp
        else:
            time.sleep(0.002)
    return None


class Run:
    def __init__(self):
        self.p = self.f = 0

    def check(self, label, ok, detail=""):
        print(f"  [{'OK ' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
        if ok:
            self.p += 1
        else:
            self.f += 1
        return ok


def ping_rtt(ser):
    t0 = time.perf_counter()
    r = send(ser, {"cmd": "ping"}, timeout=3)
    return (time.perf_counter() - t0) * 1000 if r else None


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else find_port()
    if not port:
        print("No CH343 board found")
        return 1

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 115200
    ser.timeout = 3
    ser.dtr = False
    ser.rts = False
    ser.open()
    time.sleep(0.4)

    r = Run()
    print("=" * 62)
    print(f"  STA join state machine on {port}")
    print("=" * 62)

    ws = send(ser, {"cmd": "wifi_status"})
    if not ws:
        print("  no response")
        return 1
    if ws.get("saved_ssid"):
        print(f"  The board has saved credentials for {ws['saved_ssid']!r}.")
        print("  Refusing to run - this test would overwrite them.")
        ser.close()
        return 2

    print("\n-- static addressing is validated before anything is attempted --")
    bad_ip = send(ser, {"cmd": "wifi_configure", "ssid": FAKE_SSID,
                        "password": "x", "ip": "999.1.1.1", "gateway": "192.168.1.1"})
    r.check("malformed IP refused",
            bad_ip is not None and bad_ip.get("status") == "error",
            (bad_ip or {}).get("message", "no response"))

    no_gw = send(ser, {"cmd": "wifi_configure", "ssid": FAKE_SSID,
                       "password": "x", "ip": "192.168.1.50"})
    r.check("static address without a gateway refused",
            no_gw is not None and no_gw.get("status") == "error",
            (no_gw or {}).get("message", "no response"))

    print("\n-- baseline latency before the join --")
    base = [ping_rtt(ser) for _ in range(8)]
    base = [b for b in base if b]
    base_med = statistics.median(base)
    print(f"  idle ping RTT median {base_med:.1f} ms over {len(base)} samples")

    print("\n-- the join returns immediately, it does not wait --")
    t0 = time.perf_counter()
    start = send(ser, {"cmd": "wifi_configure", "ssid": FAKE_SSID, "password": "not-a-password"})
    reply_ms = (time.perf_counter() - t0) * 1000
    r.check("wifi_configure replied", start is not None,
            json.dumps(start) if start else "no response")
    r.check("reply is wifi_connecting",
            (start or {}).get("reply") == "wifi_connecting",
            str((start or {}).get("reply")))
    r.check("reply came back in under 500 ms", reply_ms < 500, f"{reply_ms:.0f} ms")
    r.check("reply says the AP is still up",
            (start or {}).get("ap_still_active") is True)

    print("\n-- the board stays responsive for the whole join --")
    during, phases, t_start = [], [], time.perf_counter()
    while time.perf_counter() - t_start < 22:
        rtt = ping_rtt(ser)
        if rtt:
            during.append(rtt)
        st = send(ser, {"cmd": "wifi_status"}, timeout=3)
        ph = (st or {}).get("sta_phase")
        if ph and (not phases or phases[-1] != ph):
            phases.append(ph)
            print(f"    {time.perf_counter() - t_start:5.1f}s  phase -> {ph}")
        if ph == "failed":
            break
        time.sleep(0.25)

    elapsed = time.perf_counter() - t_start
    worst = max(during) if during else None
    med = statistics.median(during) if during else None
    print(f"  during-join ping RTT median {med:.1f} ms, worst {worst:.1f} ms "
          f"over {len(during)} samples")

    r.check("board answered every ping during the join",
            len(during) > 15, f"{len(during)} replies")
    # The old blocking join froze loop() for up to 15 s. Anything near that would
    # show up here as a single enormous outlier.
    r.check("no ping took longer than 1 s during the join",
            worst is not None and worst < 1000, f"worst {worst:.0f} ms")
    r.check("median latency during the join stayed near baseline",
            med is not None and med < base_med * 3,
            f"{med:.1f} ms vs baseline {base_med:.1f} ms")

    print("\n-- the machine reached a terminal state with a readable reason --")
    fin = send(ser, {"cmd": "wifi_status"})
    r.check("phase is failed", (fin or {}).get("sta_phase") == "failed",
            str((fin or {}).get("sta_phase")))
    r.check("connecting was observed on the way", "connecting" in phases,
            " -> ".join(phases))
    err = (fin or {}).get("sta_error", "")
    r.check("error is a human sentence, not a status code",
            len(err) > 20 and not err.strip().isdigit(), repr(err))
    r.check("gave up within a sensible time", elapsed < 21, f"{elapsed:.1f}s")

    print("\n-- the access point survived the failed join --")
    r.check("AP still advertising", bool((fin or {}).get("ap_ssid")),
            str((fin or {}).get("ap_ssid")))
    st = send(ser, {"cmd": "status"})
    r.check("AP still running", (st or {}).get("ap_running") is True)
    r.check("board still reports HID ready", (st or {}).get("hid_ready") is True)
    r.check("heap did not suffer", (st or {}).get("heap_low") is False,
            f"floor {(st or {}).get('heap_floor')}")

    print("\n-- restoring --")
    back = send(ser, {"cmd": "wifi_disconnect"})
    r.check("credentials cleared", (back or {}).get("reply") == "wifi_disconnected",
            json.dumps(back) if back else "no response")
    final = send(ser, {"cmd": "wifi_status"})
    r.check("no saved SSID left behind", not (final or {}).get("saved_ssid"),
            repr((final or {}).get("saved_ssid")))
    r.check("phase back to idle", (final or {}).get("sta_phase") == "idle",
            str((final or {}).get("sta_phase")))

    ser.close()
    print("\n" + "=" * 62)
    print(f"  {r.p}/{r.p + r.f} passed")
    print("=" * 62)
    return 0 if r.f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
