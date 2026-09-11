"""
Comprehensive PC Lock State Test — runs all tests in ONE serial session.

Opens the port once, waits for boot, then executes:
  1. PING → verify pc_state=unknown (fresh boot)
  2. LOCK → verify reply=locked, pc_state=locked
  3. LOCK again → verify reply=already_locked, pc_state=locked
  4. Wait 3s for lock screen
  5. UNLOCK → verify reply=unlock_done, pc_state=unlocked
  6. UNLOCK again → verify reply=already_unlocked, pc_state=unlocked
  7. STATUS → verify pc_state=unlocked
  8. set_lock_state unknown → verify reply=state_set, pc_state=unknown
  9. LOCK (force) → from unknown state, verify reply=locked
  10. Wait 3s
  11. UNLOCK → verify reply=unlock_done

Usage:
    python test_lock_state.py COM6 --password "Sherlock@123"
"""

import serial
import json
import sys
import time

BAUD_RATE = 115200
TIMEOUT = 5
LOCK_SETTLE = 3


def send_cmd(ser, cmd_dict, timeout=None):
    """Send JSON command, return parsed response."""
    timeout = timeout or TIMEOUT
    
    # Drain stale data
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
                    reply = resp.get("reply", "")
                    if reply and "_started" not in reply:
                        return resp
                except json.JSONDecodeError:
                    pass  # skip log lines
        else:
            time.sleep(0.05)
    return None


def wait_for_boot(ser):
    """Wait for ESP32 boot to complete (boot JSON event)."""
    print("\nWaiting for ESP32 boot...")
    time.sleep(0.3)
    
    lines = 0
    boot_done = False
    deadline = time.time() + 7.0
    
    while time.time() < deadline:
        if ser.in_waiting:
            raw = ser.readline().decode("utf-8", errors="replace").strip()
            if raw:
                lines += 1
                try:
                    msg = json.loads(raw)
                    if msg.get("event") == "boot":
                        boot_done = True
                        pc_state = msg.get("pc_state", "?")
                        print(f"  Boot complete! pc_state={pc_state}, hid={msg.get('hid_ready')}")
                        break
                except json.JSONDecodeError:
                    if lines <= 3 or "HID" in raw or "AP" in raw:
                        print(f"  [boot] {raw}")
        else:
            time.sleep(0.05)
    
    if not boot_done:
        print(f"  WARNING: No boot event after {lines} messages — proceeding anyway")
    else:
        print(f"  ({lines} boot messages received)")
    
    # Small extra pause for HID enumeration
    time.sleep(0.5)


def test_step(ser, step_num, description, cmd_dict, expected_reply, expected_pc_state, timeout=None):
    """Run one test step and validate reply + pc_state."""
    print(f"\n{'─' * 60}")
    print(f"  TEST {step_num}: {description}")
    print(f"  Sending: {json.dumps(cmd_dict)}")
    
    resp = send_cmd(ser, cmd_dict, timeout=timeout)
    
    if not resp:
        print(f"  ✗ FAIL — No response")
        return False
    
    reply = resp.get("reply", "")
    pc_state = resp.get("pc_state", "N/A")
    status = resp.get("status", "?")
    
    print(f"  Response: status={status}, reply={reply}, pc_state={pc_state}")
    
    # Extra fields
    if "message" in resp:
        print(f"  Message: {resp['message']}")
    if "note" in resp:
        print(f"  Note: {resp['note']}")
    
    # Validate
    reply_ok = reply == expected_reply
    state_ok = (expected_pc_state is None) or (pc_state == expected_pc_state)
    
    if reply_ok and state_ok:
        print(f"  ✓ PASS — reply={reply}, pc_state={pc_state}")
        return True
    else:
        if not reply_ok:
            print(f"  ✗ FAIL — expected reply={expected_reply}, got {reply}")
        if not state_ok:
            print(f"  ✗ FAIL — expected pc_state={expected_pc_state}, got {pc_state}")
        return False


def main():
    # Parse args
    port = None
    password = None
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg.startswith("COM") or arg.startswith("/dev/"):
            port = arg
        elif arg == "--password" and i + 1 <= len(sys.argv) - 1:
            password = sys.argv[i + 1]
    
    if not port:
        print("Usage: python test_lock_state.py COM6 --password \"YourPassword\"")
        sys.exit(1)
    if not password:
        print("ERROR: --password is required for lock/unlock testing")
        sys.exit(1)
    
    print("=" * 60)
    print("  COMPREHENSIVE PC LOCK STATE TEST")
    print(f"  Port: {port} | Password: {'*' * len(password)}")
    print("=" * 60)
    
    # Open port
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
    
    # ── Test 1: PING — verify fresh boot state ──
    results.append(test_step(ser, 1,
        "PING — verify pc_state=unknown after fresh boot",
        {"cmd": "ping"},
        expected_reply="pong",
        expected_pc_state="unknown"
    ))
    
    # ── Test 2: LOCK — first lock ──
    results.append(test_step(ser, 2,
        "LOCK — lock the PC (Win+L)",
        {"cmd": "lock"},
        expected_reply="locked",
        expected_pc_state="locked"
    ))
    
    # ── Test 3: LOCK again — should say already_locked ──
    results.append(test_step(ser, 3,
        "LOCK again — should return already_locked",
        {"cmd": "lock"},
        expected_reply="already_locked",
        expected_pc_state="locked"
    ))
    
    # ── Wait for lock screen ──
    print(f"\n  ⏳ Waiting {LOCK_SETTLE}s for lock screen to settle...")
    time.sleep(LOCK_SETTLE)
    
    # ── Test 4: UNLOCK — unlock the PC ──
    results.append(test_step(ser, 4,
        "UNLOCK — type password to unlock",
        {"cmd": "unlock", "password": password},
        expected_reply="unlock_done",
        expected_pc_state="unlocked",
        timeout=10
    ))
    
    # ── Brief pause for desktop to appear ──
    print(f"\n  ⏳ Waiting 2s for desktop to appear...")
    time.sleep(2)
    
    # ── Test 5: UNLOCK again — should say already_unlocked ──
    results.append(test_step(ser, 5,
        "UNLOCK again — should return already_unlocked",
        {"cmd": "unlock", "password": password},
        expected_reply="already_unlocked",
        expected_pc_state="unlocked"
    ))
    
    # ── Test 6: STATUS — verify state persists ──
    results.append(test_step(ser, 6,
        "STATUS — verify pc_state=unlocked persists",
        {"cmd": "status"},
        expected_reply="status",
        expected_pc_state="unlocked"
    ))
    
    # ── Test 7: set_lock_state unknown — manual state reset ──
    results.append(test_step(ser, 7,
        "SET_LOCK_STATE unknown — manual state reset",
        {"cmd": "set_lock_state", "state": "unknown"},
        expected_reply="state_set",
        expected_pc_state="unknown"
    ))
    
    # ── Test 8: LOCK with force from unknown ──
    results.append(test_step(ser, 8,
        "LOCK (force) — should lock from unknown state",
        {"cmd": "lock", "force": True},
        expected_reply="locked",
        expected_pc_state="locked"
    ))
    
    # ── Wait for lock screen ──
    print(f"\n  ⏳ Waiting {LOCK_SETTLE}s for lock screen to settle...")
    time.sleep(LOCK_SETTLE)
    
    # ── Test 9: UNLOCK — final unlock ──
    results.append(test_step(ser, 9,
        "UNLOCK — final unlock to restore desktop",
        {"cmd": "unlock", "password": password},
        expected_reply="unlock_done",
        expected_pc_state="unlocked",
        timeout=10
    ))
    
    # ── Test 10: LOCK with force when already unlocked ──
    # Brief pause
    time.sleep(2)
    results.append(test_step(ser, 10,
        "LOCK (force) — force lock even though state=unlocked",
        {"cmd": "lock", "force": True},
        expected_reply="locked",
        expected_pc_state="locked"
    ))
    
    # ── Wait and unlock to leave PC usable ──
    print(f"\n  ⏳ Waiting {LOCK_SETTLE}s for lock screen...")
    time.sleep(LOCK_SETTLE)
    
    results.append(test_step(ser, 11,
        "UNLOCK — restore desktop (cleanup)",
        {"cmd": "unlock", "password": password},
        expected_reply="unlock_done",
        expected_pc_state="unlocked",
        timeout=10
    ))
    
    # ── Summary ──
    ser.close()
    
    passed = sum(results)
    total = len(results)
    
    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {passed}/{total} passed")
    for i, ok in enumerate(results):
        mark = "✓" if ok else "✗"
        print(f"    {mark} Test {i+1}")
    print(f"{'=' * 60}")
    
    if passed == total:
        print("  ALL TESTS PASSED — Lock state tracking is working correctly!")
    else:
        print(f"  {total - passed} test(s) FAILED")
    
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
