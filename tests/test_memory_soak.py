"""Sustained-load soak over serial, to catch a leak rather than assume there is none.

Sends a few thousand commands across every allocating path -- JSON parsing, String
building, status serialisation, oversized frames -- and compares free heap before
and after, plus the allocator's own low-water mark.

Net-zero by construction: every mouse_move is undone by an equal and opposite one,
so the cursor finishes where it started. Nothing is clicked or typed.
"""
import json
import sys
import time

import serial
import serial.tools.list_ports

ROUNDS = 400


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

    print("=" * 60)
    print(f"  Memory soak on {port} - {ROUNDS} rounds")
    print("=" * 60)

    before = send(ser, {"cmd": "status"})
    if not before:
        print("  no status response")
        return 1
    h0 = before["free_heap"]
    f0 = before["heap_floor"]
    c0 = before["cmd_count"]
    print(f"  start: free={h0} floor={f0} commands={c0}")

    t0 = time.perf_counter()
    sent = 0
    for i in range(ROUNDS):
        # Every path that allocates: parse, dispatch, build a reply, serialise.
        send(ser, {"cmd": "mouse_move", "x": 7, "y": 5}, timeout=2)
        send(ser, {"cmd": "mouse_move", "x": -7, "y": -5}, timeout=2)
        send(ser, {"cmd": "echo", "data": "soak" * 30}, timeout=2)
        send(ser, {"cmd": "status"}, timeout=2)
        send(ser, {"cmd": "wifi_status"}, timeout=2)
        sent += 5
        if (i + 1) % 50 == 0:
            now = send(ser, {"cmd": "status"}, timeout=3)
            print(f"  {i + 1:4d}/{ROUNDS}  free={now['free_heap']}  floor={now['heap_floor']}")

    elapsed = time.perf_counter() - t0

    # Let anything deferred settle before the final reading.
    time.sleep(2.0)
    after = send(ser, {"cmd": "status"})
    h1 = after["free_heap"]
    f1 = after["heap_floor"]
    c1 = after["cmd_count"]

    print("-" * 60)
    print(f"  commands sent      : {sent} in {elapsed:.1f}s "
          f"({sent / elapsed:.0f}/s)")
    print(f"  board counted      : {c1 - c0}")
    print(f"  free heap  before  : {h0}")
    print(f"  free heap  after   : {h1}   delta {h1 - h0:+d}")
    print(f"  heap floor before  : {f0}")
    print(f"  heap floor after   : {f1}   delta {f1 - f0:+d}")
    print(f"  heap_low flag      : {after['heap_low']}")
    print("-" * 60)

    failed = 0

    def check(label, ok, detail=""):
        nonlocal failed
        print(f"  [{'OK ' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
        if not ok:
            failed += 1

    # A steady state is what matters. Some churn is normal; a downward trend is
    # not. 4 KB over 2000 commands would be ~2 bytes per command, which would
    # exhaust the board in a day of use.
    check("free heap did not fall by more than 4 KB",
          h1 > h0 - 4096, f"delta {h1 - h0:+d}")
    check("heap floor did not fall by more than 8 KB",
          f1 > f0 - 8192, f"delta {f1 - f0:+d}")
    check("heap_low never tripped", after["heap_low"] is False)
    check("board answered every command", c1 - c0 >= sent, f"{c1 - c0} of {sent}")

    ser.close()
    print("=" * 60)
    print("  no leak detected" if not failed else f"  {failed} check(s) failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
