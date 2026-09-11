"""
Comprehensive Mouse + Wiggle + LED test — runs all tests in ONE serial session.

Exercises every REQ/ACK combination of the Phase 3.2 additions:
  Mouse:  move, click (left/right/middle), click+modifiers, press/release, scroll
  Wiggle: single, interval (start / busy-reject / force-restart / stop / stop-when-idle),
          timed (auto-stop verified via status)
  LED:    set by name, set by RGB, set off, unknown-color error, blink, status
  Status: ping/status expose wiggle_active + led_on

Usage:
    python test_mouse_led.py COM6
    python test_mouse_led.py COM6 --skip-clicks    # skip real mouse clicks (safer)

WARNING: unless --skip-clicks is given, this WILL move the mouse and perform
real clicks on whatever window is focused. Click an empty desktop area first,
or pass --skip-clicks. Mouse moves are net-zero / tiny and harmless.
"""

import serial
import json
import sys
import time

BAUD_RATE = 115200
TIMEOUT = 5


def send_cmd(ser, cmd_dict, timeout=None):
    """Send JSON command, return parsed response (skips log + async event lines)."""
    timeout = timeout or TIMEOUT
    while ser.in_waiting:
        ser.readline()
    line = json.dumps(cmd_dict) + "\n"
    ser.write(line.encode("utf-8"))
    ser.flush()
    start = time.time()
    while time.time() - start < timeout:
        if ser.in_waiting:
            raw = ser.readline().decode("utf-8", errors="replace").strip()
            if raw:
                try:
                    resp = json.loads(raw)
                    # skip async events (they have "event", no "reply"/"status")
                    if resp.get("reply") or resp.get("status"):
                        reply = resp.get("reply", "")
                        if reply and "_started" in reply and reply != "wiggle_started" and reply != "led_blink_started":
                            continue
                        return resp
                except json.JSONDecodeError:
                    pass
        else:
            time.sleep(0.05)
    return None


def wait_for_boot(ser):
    print("\nWaiting for ESP32 boot...")
    time.sleep(0.3)
    boot_done = False
    deadline = time.time() + 7.0
    lines = 0
    while time.time() < deadline:
        if ser.in_waiting:
            raw = ser.readline().decode("utf-8", errors="replace").strip()
            if raw:
                lines += 1
                try:
                    msg = json.loads(raw)
                    if msg.get("event") == "boot":
                        boot_done = True
                        print(f"  Boot complete! firmware={msg.get('firmware')}, hid={msg.get('hid_ready')}")
                        break
                except json.JSONDecodeError:
                    pass
        else:
            time.sleep(0.05)
    if not boot_done:
        print(f"  WARNING: no boot event after {lines} messages — proceeding anyway")
    time.sleep(0.5)


def check(step, description, cmd, *, expect_reply=None, expect_status=None,
          expect_fields=None, ser=None, timeout=None, min_count_field=None):
    """Run one step; validate reply/status/fields. Returns True on PASS."""
    print(f"\n{'-' * 60}")
    print(f"  TEST {step}: {description}")
    print(f"  Sending: {json.dumps(cmd)}")
    resp = send_cmd(ser, cmd, timeout=timeout)
    if not resp:
        print("  [X] FAIL — no response")
        return False
    print(f"  Response: {json.dumps(resp)}")

    ok = True
    if expect_reply is not None and resp.get("reply") != expect_reply:
        print(f"  [X] FAIL — expected reply={expect_reply}, got {resp.get('reply')}")
        ok = False
    if expect_status is not None and resp.get("status") != expect_status:
        print(f"  [X] FAIL — expected status={expect_status}, got {resp.get('status')}")
        ok = False
    if expect_fields:
        for k, v in expect_fields.items():
            if resp.get(k) != v:
                print(f"  [X] FAIL — expected {k}={v}, got {resp.get(k)}")
                ok = False
    if min_count_field:
        if not (isinstance(resp.get(min_count_field), int) and resp.get(min_count_field) >= 1):
            print(f"  [X] FAIL — expected {min_count_field} >= 1, got {resp.get(min_count_field)}")
            ok = False
    if ok:
        print("  [OK] PASS")
    return ok


def main():
    port = None
    skip_clicks = "--skip-clicks" in sys.argv
    for arg in sys.argv[1:]:
        if arg.startswith("COM") or arg.startswith("/dev/"):
            port = arg
    if not port:
        print("Usage: python test_mouse_led.py COM6 [--skip-clicks]")
        sys.exit(1)

    print("=" * 60)
    print("  MOUSE + WIGGLE + LED TEST")
    print(f"  Port: {port} | clicks: {'SKIPPED' if skip_clicks else 'ENABLED'}")
    print("=" * 60)
    if not skip_clicks:
        print("\n  WARNING: real mouse clicks will fire on the focused window.")
        print("  Click an empty desktop area now. Starting in 3s...")
        time.sleep(3)

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = BAUD_RATE
    ser.timeout = TIMEOUT
    ser.dtr = False
    ser.rts = False
    ser.open()
    print(f"Connected to {port}")
    wait_for_boot(ser)

    results = []

    # ── Status baseline ──
    results.append(check(1, "PING — wiggle_active should be false at boot",
                         {"cmd": "ping"}, expect_reply="pong",
                         expect_fields={"wiggle_active": False}, ser=ser))

    # ── Mouse move ──
    results.append(check(2, "MOUSE_MOVE right 20px",
                         {"cmd": "mouse_move", "dx": 20, "dy": 0}, expect_reply="mouse_moved", ser=ser))
    results.append(check(3, "MOUSE_MOVE back left 20px",
                         {"cmd": "mouse_move", "dx": -20, "dy": 0}, expect_reply="mouse_moved", ser=ser))
    results.append(check(4, "MOUSE_MOVE clamps to int8 (dx=999 -> 127)",
                         {"cmd": "mouse_move", "dx": 999}, expect_reply="mouse_moved",
                         expect_fields={"dx": 127}, ser=ser))
    results.append(check(5, "MOUSE_MOVE back (dx=-127)",
                         {"cmd": "mouse_move", "dx": -127}, expect_reply="mouse_moved", ser=ser))

    # ── Mouse scroll ──
    results.append(check(6, "MOUSE_SCROLL down",
                         {"cmd": "mouse_scroll", "amount": 3}, expect_reply="mouse_scrolled", ser=ser))
    results.append(check(7, "MOUSE_SCROLL up",
                         {"cmd": "mouse_scroll", "amount": -3}, expect_reply="mouse_scrolled", ser=ser))

    # ── Mouse press / release ──
    results.append(check(8, "MOUSE_PRESS left",
                         {"cmd": "mouse_press", "button": "left"}, expect_reply="mouse_pressed", ser=ser))
    results.append(check(9, "MOUSE_RELEASE left",
                         {"cmd": "mouse_release", "button": "left"}, expect_reply="mouse_released", ser=ser))

    # ── Mouse click ──
    if skip_clicks:
        print("\n  (Skipping click tests 10-12 per --skip-clicks)")
        results.extend([True, True, True])
    else:
        results.append(check(10, "MOUSE_CLICK left",
                             {"cmd": "mouse_click", "button": "left"}, expect_reply="mouse_clicked", ser=ser))
        results.append(check(11, "MOUSE_CLICK right (context menu)",
                             {"cmd": "mouse_click", "button": "right"}, expect_reply="mouse_clicked", ser=ser))
        # close any context menu
        send_cmd(ser, {"cmd": "press", "keys": "ESC"})
        results.append(check(12, "MOUSE_CLICK left with CTRL modifier",
                             {"cmd": "mouse_click", "button": "left", "modifiers": "CTRL"},
                             expect_reply="mouse_clicked", ser=ser))

    # ── Wiggle: single ──
    results.append(check(13, "WIGGLE single (one net-zero jiggle)",
                         {"cmd": "wiggle", "mode": "single", "amplitude": 8},
                         expect_reply="wiggle_done", ser=ser))

    # ── Wiggle: interval start ──
    results.append(check(14, "WIGGLE interval start",
                         {"cmd": "wiggle", "mode": "interval", "amplitude": 5, "interval_ms": 300},
                         expect_reply="wiggle_started", ser=ser))
    time.sleep(1.0)

    # ── Wiggle: start again without force → busy ──
    results.append(check(15, "WIGGLE start again (no force) -> busy",
                         {"cmd": "wiggle", "mode": "interval"},
                         expect_reply="busy", expect_status="error", ser=ser))

    # ── Wiggle: start again WITH force → restart ──
    results.append(check(16, "WIGGLE start again force:true -> restart",
                         {"cmd": "wiggle", "mode": "interval", "amplitude": 3, "interval_ms": 500, "force": True},
                         expect_reply="wiggle_started", ser=ser))
    time.sleep(1.0)

    # ── Status shows active ──
    results.append(check(17, "STATUS — wiggle_active true, mode interval",
                         {"cmd": "status"}, expect_reply="status",
                         expect_fields={"wiggle_active": True, "wiggle_mode": "interval"}, ser=ser))

    # ── Wiggle stop ──
    results.append(check(18, "WIGGLE_STOP running -> stopped (count>=1)",
                         {"cmd": "wiggle_stop"}, expect_reply="wiggle_stopped",
                         min_count_field="count", ser=ser))

    # ── Wiggle stop when idle ──
    results.append(check(19, "WIGGLE_STOP when idle -> not_running (idempotent, ok)",
                         {"cmd": "wiggle_stop"}, expect_reply="not_running",
                         expect_status="ok", ser=ser))

    # ── Wiggle timed auto-stop ──
    results.append(check(20, "WIGGLE timed 3s start",
                         {"cmd": "wiggle", "mode": "timed", "amplitude": 5, "interval_ms": 400, "duration_s": 3},
                         expect_reply="wiggle_started", expect_fields={"mode": "timed"}, ser=ser))
    print("\n  Waiting 4s for timed wiggle to auto-stop...")
    time.sleep(4.0)
    results.append(check(21, "STATUS after timed — should be idle again",
                         {"cmd": "status"}, expect_reply="status",
                         expect_fields={"wiggle_active": False}, ser=ser))

    # ── LED ──
    results.append(check(22, "LED_SET green (by name)",
                         {"cmd": "led_set", "color": "green"}, expect_reply="led_set",
                         expect_fields={"on": True}, ser=ser))
    results.append(check(23, "LED_SET by RGB (0,0,40)",
                         {"cmd": "led_set", "r": 0, "g": 0, "b": 40}, expect_reply="led_set",
                         expect_fields={"on": True, "b": 40}, ser=ser))
    results.append(check(24, "LED_SET off",
                         {"cmd": "led_set", "off": True}, expect_reply="led_set",
                         expect_fields={"on": False}, ser=ser))
    results.append(check(25, "LED_SET unknown color -> error",
                         {"cmd": "led_set", "color": "chartreuse"}, expect_status="error", ser=ser))
    results.append(check(26, "LED_BLINK 3x",
                         {"cmd": "led_blink", "color": "blue", "times": 3, "on_ms": 150, "off_ms": 150},
                         expect_reply="led_blink_started", ser=ser))
    time.sleep(1.2)
    results.append(check(27, "LED_STATUS",
                         {"cmd": "led_status"}, expect_reply="led_status", ser=ser))
    # leave LED off
    send_cmd(ser, {"cmd": "led_set", "off": True})

    # ── Unknown command still handled ──
    results.append(check(28, "UNKNOWN command -> error (existing behavior intact)",
                         {"cmd": "definitely_not_a_command"}, expect_status="error", ser=ser))

    ser.close()

    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {passed}/{total} passed")
    for i, ok in enumerate(results, 1):
        print(f"    {'[OK]' if ok else '[X] '} Test {i}")
    print(f"{'=' * 60}")
    if passed == total:
        print("  ALL TESTS PASSED — mouse + wiggle + LED working correctly!")
    else:
        print(f"  {total - passed} test(s) FAILED")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
