"""Verification for the power, health and exposure work in v3.4, over serial.

Everything here is read-only or self-restoring: the power mode is read first and
put back at the end, so the board is left exactly as it was found.

The exposure gate cannot be exercised from serial by design -- serial is never
gated -- so this only checks that the board reports the fields the gate and the
dashboard depend on.
"""
import json
import sys
import time

import serial
import serial.tools.list_ports


def find_port():
    for p in serial.tools.list_ports.comports():
        if p.vid == 0x1A86 and p.pid in (0x55D3, 0x7523):
            return p.device
    return None


def send(ser, cmd, timeout=5.0):
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

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 115200
    ser.timeout = 3
    ser.dtr = False
    ser.rts = False
    ser.open()
    time.sleep(0.4)

    r = Run()
    print("=" * 60)
    print(f"  Power / health / exposure checks on {port}")
    print("=" * 60)

    st = send(ser, {"cmd": "status"})
    if not st:
        print("  no status response")
        return 1

    original_mode = st.get("power_mode")
    original_apoff = st.get("ap_auto_off")

    print("\n-- status fields --")
    for key, kind in [
        ("power_mode", str),
        ("radio_awake", bool),
        ("ap_auto_off", bool),
        ("ap_running", bool),
        ("lan_exposed", bool),
        ("heap_floor", int),
        ("heap_low", bool),
        ("sta_phase", str),
        ("sta_ip_mode", str),
        ("channel", int),
    ]:
        r.check(f"status has {key}", isinstance(st.get(key), kind), repr(st.get(key)))

    r.check("power_mode is a known value",
            st.get("power_mode") in ("performance", "balanced", "saver"),
            str(st.get("power_mode")))
    # One radio serves both interfaces, so this is the AP's channel as well as the
    # home network's. 1-13 is the 2.4 GHz band the ESP32 is limited to.
    r.check("channel is in the 2.4 GHz band",
            1 <= st.get("channel", 0) <= 13, str(st.get("channel")))
    r.check("heap_floor <= free_heap",
            st.get("heap_floor", 0) <= st.get("free_heap", 0),
            f"floor={st.get('heap_floor')} free={st.get('free_heap')}")
    r.check("heap is not low", st.get("heap_low") is False,
            f"floor={st.get('heap_floor')}")

    print("\n-- power mode switching --")
    for mode, want_awake in [("performance", True), ("saver", False), ("balanced", True)]:
        resp = send(ser, {"cmd": "power_mode", "mode": mode})
        ok = resp is not None and resp.get("mode") == mode
        r.check(f"set {mode}", ok, json.dumps(resp) if resp else "no response")
        if ok:
            r.check(f"{mode} radio_awake={want_awake}",
                    resp.get("radio_awake") is want_awake,
                    f"got {resp.get('radio_awake')}")

    bad = send(ser, {"cmd": "power_mode", "mode": "turbo"})
    r.check("unknown mode rejected",
            bad is not None and bad.get("status") == "error",
            json.dumps(bad) if bad else "no response")

    print("\n-- power mode persists in status --")
    st2 = send(ser, {"cmd": "status"})
    r.check("status agrees with last set",
            st2 is not None and st2.get("power_mode") == "balanced",
            str(st2.get("power_mode") if st2 else None))

    print("\n-- ap_auto_off toggle --")
    on = send(ser, {"cmd": "power_mode", "ap_auto_off": True})
    r.check("ap_auto_off on", on is not None and on.get("ap_auto_off") is True,
            json.dumps(on) if on else "no response")
    off = send(ser, {"cmd": "power_mode", "ap_auto_off": False})
    r.check("ap_auto_off off", off is not None and off.get("ap_auto_off") is False,
            json.dumps(off) if off else "no response")
    r.check("AP still running after toggle",
            off is not None and off.get("ap_running") is True,
            str(off.get("ap_running") if off else None))

    print("\n-- oversized serial line is refused, board survives --")
    ser.write((json.dumps({"cmd": "echo", "data": "x" * 3000}) + "\n").encode())
    ser.flush()
    time.sleep(0.5)
    while ser.in_waiting:
        ser.readline()
    alive = send(ser, {"cmd": "ping"})
    r.check("board still answers after a 3 KB line",
            alive is not None and alive.get("reply") == "pong",
            json.dumps(alive) if alive else "no response")

    print("\n-- wifi_status reports the join state machine --")
    ws = send(ser, {"cmd": "wifi_status"})
    for key in ("sta_phase", "sta_error", "sta_ip_mode", "sta_target", "want_ip"):
        r.check(f"wifi_status has {key}", ws is not None and key in ws)
    r.check("AP reported active", ws is not None and bool(ws.get("ap_ssid")),
            str(ws.get("ap_ssid") if ws else None))

    print("\n-- restoring --")
    if original_mode:
        back = send(ser, {"cmd": "power_mode", "mode": original_mode,
                          "ap_auto_off": bool(original_apoff)})
        r.check(f"restored power_mode to {original_mode}",
                back is not None and back.get("mode") == original_mode,
                json.dumps(back) if back else "no response")
        final = send(ser, {"cmd": "status"})
        r.check("status confirms restore",
                final is not None
                and final.get("power_mode") == original_mode
                and final.get("ap_auto_off") == original_apoff,
                f"mode={final.get('power_mode') if final else None} "
                f"apoff={final.get('ap_auto_off') if final else None}")

    ser.close()
    print("\n" + "=" * 60)
    print(f"  {r.passed}/{r.passed + r.failed} passed")
    print("=" * 60)
    return 0 if r.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
