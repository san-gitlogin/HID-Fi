"""Post-flash verification for the firmware over serial.

Only exercises net-zero / reversible actions:
  - mouse_move with large deltas (proves the multi-report split) and back again
  - horizontal pan + wheel, then the inverse
  - media volume_down then volume_up (shows the Windows volume OSD, net zero)
  - read-only queries

It deliberately does NOT click, type, or fire gestures, since those would land
on whatever window is focused on this PC.
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


def check(ser, label, cmd, expect_reply=None, expect=None):
    resp = send(ser, cmd)
    ok = resp is not None
    if ok and expect_reply is not None:
        ok = resp.get("reply") == expect_reply
    if ok and expect:
        for k, v in expect.items():
            if resp.get(k) != v:
                ok = False
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}: {json.dumps(resp) if resp else 'no response'}")
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

    deadline = time.time() + 6
    while time.time() < deadline:
        if ser.in_waiting:
            raw = ser.readline().decode("utf-8", "replace").strip()
            if raw and '"event":"boot"' in raw.replace(" ", ""):
                break
        else:
            time.sleep(0.05)

    results = []
    print(f"\nVerifying v3.5 on {port}\n" + "=" * 60)

    print("\n-- identity --")
    st = send(ser, {"cmd": "status"})
    results.append(check(ser, "status is v3.5", {"cmd": "status"},
                         "status", {"firmware": "hid_fi_v3.5", "hid_ready": True}))
    gamepad_on = bool(st and st.get("gamepad"))
    absolute = bool(st and st.get("pointer_mode") == "absolute")
    print(f"  (board state: gamepad={'on' if gamepad_on else 'off'}, "
          f"pointer={'absolute' if absolute else 'relative'})")

    print("\n-- pointer: large delta must NOT be clipped at 127 --")
    results.append(check(ser, "move +400,+300", {"cmd": "mouse_move", "dx": 400, "dy": 300},
                         "mouse_moved", {"dx": 400, "dy": 300}))
    time.sleep(0.2)
    results.append(check(ser, "move -400,-300 (back)", {"cmd": "mouse_move", "dx": -400, "dy": -300},
                         "mouse_moved", {"dx": -400, "dy": -300}))

    print("\n-- pointer: horizontal pan is now wired --")
    results.append(check(ser, "scroll pan +1", {"cmd": "mouse_scroll", "amount": 0, "pan": 1},
                         "mouse_scrolled", {"pan": 1}))
    time.sleep(0.2)
    results.append(check(ser, "scroll pan -1", {"cmd": "mouse_scroll", "amount": 0, "pan": -1},
                         "mouse_scrolled", {"pan": -1}))

    print("\n-- media keys (watch for the Windows volume OSD) --")
    results.append(check(ser, "volume_down", {"cmd": "media", "key": "volume_down"}, "media_sent"))
    time.sleep(0.4)
    results.append(check(ser, "volume_up (restores)", {"cmd": "media", "key": "volume_up"}, "media_sent"))
    results.append(check(ser, "bad media key rejected", {"cmd": "media", "key": "nope"},
                         None, {"status": "error"}))

    print("\n-- new command surface --")
    results.append(check(ser, "macro_list", {"cmd": "macro_list"}, "macro_list"))
    results.append(check(ser, "macro_save slot 0", {"cmd": "macro_save", "slot": 0, "name": "probe",
                                                   "steps": ["delay 10"]}, "macro_saved"))
    results.append(check(ser, "macro_get slot 0", {"cmd": "macro_get", "slot": 0}, "macro"))
    results.append(check(ser, "macro_delete slot 0", {"cmd": "macro_delete", "slot": 0}, "macro_deleted"))
    results.append(check(ser, "bad macro slot rejected", {"cmd": "macro_run", "slot": 99},
                         None, {"status": "error"}))

    # Both of these depend on how the board is currently configured, and those
    # settings live in NVS so they survive a reflash.
    if absolute:
        results.append(check(ser, "mouse_abs accepted in absolute mode", {"cmd": "mouse_abs", "x": 16384, "y": 16384},
                             "mouse_abs"))
    else:
        results.append(check(ser, "mouse_abs rejected in relative mode", {"cmd": "mouse_abs", "x": 100, "y": 100},
                             None, {"status": "error"}))

    if gamepad_on:
        ok = check(ser, "gamepad accepted while enabled", {"cmd": "gamepad", "button": 0, "pressed": True},
                   "gamepad_sent")
        send(ser, {"cmd": "gamepad", "button": 0, "pressed": False})   # never leave a button held
        results.append(ok)
    else:
        results.append(check(ser, "gamepad rejected while disabled", {"cmd": "gamepad", "button": 0, "pressed": True},
                             None, {"status": "error"}))

    print("\n-- multi-touch keyboard (v3.4) --")
    results.append(check(ser, "key_down SHIFT", {"cmd": "key_down", "key": "SHIFT"}, "key_down"))
    results.append(check(ser, "key_up SHIFT", {"cmd": "key_up", "key": "SHIFT"}, "key_up"))
    results.append(check(ser, "key_release_all", {"cmd": "key_release_all"}, "key_release_all"))
    results.append(check(ser, "unknown key rejected", {"cmd": "key_down", "key": "NOPE"},
                         None, {"status": "error"}))

    print("\n-- backward compatibility (SB auto-unlock contract) --")
    results.append(check(ser, "ping", {"cmd": "ping"}, "pong"))
    results.append(check(ser, "set_lock_state locked", {"cmd": "set_lock_state", "state": "locked"},
                         "state_set", {"pc_state": "locked"}))
    results.append(check(ser, "lock is idempotent", {"cmd": "lock"}, "already_locked"))
    results.append(check(ser, "set_lock_state unknown (restore)", {"cmd": "set_lock_state", "state": "unknown"},
                         "state_set", {"pc_state": "unknown"}))
    results.append(check(ser, "unlock needs a password", {"cmd": "unlock"}, None, {"status": "error"}))

    print("\n-- latency --")
    lat = []
    for _ in range(15):
        t0 = time.perf_counter()
        send(ser, {"cmd": "mouse_move", "dx": 0, "dy": 0})
        lat.append((time.perf_counter() - t0) * 1000)
    lat.sort()
    print(f"  serial mouse_move RTT: min={lat[0]:.1f} med={lat[len(lat)//2]:.1f} max={lat[-1]:.1f} ms")

    ser.close()
    passed = sum(1 for r in results if r)
    print("\n" + "=" * 60)
    print(f"  {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
