"""Host OS detection checks, over serial.

This is the regression test for the v3.7 detection rewrite. Three things went
wrong with the old Num Lock probe and each one is covered here:

  1. It could stall and never decide. Measured on a Windows laptop: the same
     board resolved in 5 s on one boot and was still "pending" 9 s into the next.
  2. Its macOS branch never undid the Num Lock toggle it had just caused, so a
     Windows host slower than the reply window was misdetected *and* left with
     Num Lock on.
  3. It never re-armed, so moving the board to another computer kept the old
     answer.

Opening the port resets the board, which is exactly what this test wants: it
watches a real boot and asserts detection settles. Any manual OS pin found on the
board is read first and restored at the end.
"""
import ctypes
import json
import sys
import time

import serial
import serial.tools.list_ports

SETTLE_DEADLINE = 12.0   # generous; the firmware's own window is 3 s


def find_port():
    for p in serial.tools.list_ports.comports():
        if p.vid == 0x1A86 and p.pid in (0x55D3, 0x7523):
            return p.device
    return None


def lock_keys():
    """Num Lock / Caps Lock state on Windows, or None elsewhere."""
    if not sys.platform.startswith("win"):
        return None
    u = ctypes.windll.user32
    return (u.GetKeyState(0x90) & 1, u.GetKeyState(0x14) & 1)


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


def wait_settled(ser, deadline=SETTLE_DEADLINE):
    """Poll status until detection stops reporting 'pending'."""
    t0 = time.perf_counter()
    st = None
    while time.perf_counter() - t0 < deadline:
        st = send(ser, {"cmd": "status"}, timeout=2.0)
        if st and st.get("host_os_source") and st.get("host_os_source") != "pending":
            return st
        time.sleep(0.5)
    return st


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

    locks_before = lock_keys()

    # Deferred open with both lines low: the board still resets (the auto-reset
    # circuit is wired to DTR/RTS) but it boots the application rather than
    # risking the ROM bootloader.
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 115200
    ser.timeout = 3
    ser.dtr = False
    ser.rts = False
    ser.open()

    r = Run()
    print("=" * 60)
    print(f"  Host OS detection checks on {port}")
    print("=" * 60)

    # --- detection must reach a verdict, and not sit on "pending" ---
    print("\n-- detection settles --")
    t0 = time.perf_counter()
    st = wait_settled(ser)
    elapsed = time.perf_counter() - t0
    source = st.get("host_os_source") if st else None

    if not r.check("status responded", st is not None):
        ser.close()
        return 1
    r.check("detection reached a verdict", source in ("auto", "manual", "undetermined"),
            f"{source} after {elapsed:.1f}s")
    r.check("did not stall on 'pending'", source != "pending", str(source))

    # --- the fields the dashboard and any argument about detection depend on ---
    print("\n-- evidence reported --")
    for key, kind in [
        ("host_os", str),
        ("host_os_source", str),
        ("host_os_detected", str),
        ("detect_phase", str),
        ("usb_str_reqs", int),
        ("usb_str_rereads", int),
        ("usb_set_idle", int),
        ("usb_ctrl_reqs", int),
        ("usb_led_reports", int),
    ]:
        r.check(f"status has {key}", isinstance(st.get(key), kind), repr(st.get(key)))

    r.check("host_os is a known value",
            st.get("host_os") in ("windows", "mac", "linux", "unknown"), str(st.get("host_os")))
    r.check("detect_phase is settled", st.get("detect_phase") == "settled",
            str(st.get("detect_phase")))
    r.check("a host enumerated us", st.get("usb_ctrl_reqs", 0) > 0,
            f"{st.get('usb_ctrl_reqs')} control requests")

    # The discriminator is the shape of the host's string-descriptor reads: macOS
    # re-reads nearly every index (2 bytes, then the whole string), Windows does
    # it for the odd one. Measured on Windows 11 against this board: 11 requests,
    # 2 repeats. So the verdict is the ratio, and a Windows host must stay well
    # under half.
    if sys.platform.startswith("win"):
        r.check("Windows host detected as windows", st.get("host_os_detected") == "windows",
                str(st.get("host_os_detected")))
        reqs, rereads = st.get("usb_str_reqs", 0), st.get("usb_str_rereads", 0)
        r.check("Windows host read our string descriptors", reqs > 0, str(reqs))
        r.check("Windows re-read ratio stays under half", rereads * 2 < reqs,
                f"{rereads}/{reqs}")
        r.check("the raw fingerprint is reported", isinstance(st.get("usb_str_seq"), str),
                str(st.get("usb_str_seq")))

    # --- nothing was typed into the host ---
    print("\n-- host left alone --")
    locks_after = lock_keys()
    if locks_before is None:
        print("  [SKIP] lock-key state only readable on Windows")
    else:
        r.check("Num Lock not touched by detection", locks_before[0] == locks_after[0],
                f"{locks_before[0]} -> {locks_after[0]}")
        r.check("Caps Lock not touched by detection", locks_before[1] == locks_after[1],
                f"{locks_before[1]} -> {locks_after[1]}")

    # --- pin / auto round trip, restoring whatever was there ---
    print("\n-- set_host_os round trip --")
    original_source = st.get("host_os_source")
    original_os = st.get("host_os")

    pin = send(ser, {"cmd": "set_host_os", "os": "mac"})
    r.check("can pin macOS", pin is not None and pin.get("host_os") == "mac",
            str(pin.get("host_os") if pin else None))
    r.check("pin reports source=manual", pin is not None and pin.get("host_os_source") == "manual",
            str(pin.get("host_os_source") if pin else None))
    # A pin must not erase what was actually detected, or the dashboard cannot
    # warn that the two disagree.
    r.check("pin still reports what was detected",
            pin is not None and pin.get("host_os_detected") in ("windows", "mac", "linux", "unknown"),
            str(pin.get("host_os_detected") if pin else None))

    auto = send(ser, {"cmd": "set_host_os", "os": "auto"})
    r.check("can return to auto", auto is not None and auto.get("host_os_source") != "manual",
            str(auto.get("host_os_source") if auto else None))

    # auto re-arms detection, so give it the window again before restoring.
    again = wait_settled(ser)
    r.check("re-detects after auto", again is not None and again.get("host_os_source") == "auto",
            str(again.get("host_os_source") if again else None))
    # Re-arming must not throw away the evidence from the enumeration already in
    # progress -- the host only reads our descriptors once, when it first sets the
    # keyboard up, so a re-judge that zeroed the counters could never succeed
    # without physically replugging the cable.
    r.check("re-detect reaches the same verdict",
            again is not None and again.get("host_os_detected") == st.get("host_os_detected"),
            f"{st.get('host_os_detected')} -> {again.get('host_os_detected') if again else None}")

    locks_end = lock_keys()
    if locks_before is not None:
        r.check("Num Lock still untouched after a re-detect",
                locks_before[0] == locks_end[0], f"{locks_before[0]} -> {locks_end[0]}")

    # restore the board exactly as found
    restore = original_os if original_source == "manual" else "auto"
    send(ser, {"cmd": "set_host_os", "os": restore})
    final = wait_settled(ser)
    r.check("board restored to how it was found",
            final is not None and final.get("host_os_source") == original_source,
            f"wanted {original_source}, got {final.get('host_os_source') if final else None}")

    ser.close()

    print("=" * 60)
    print(f"  {r.passed} passed, {r.failed} failed")
    print("=" * 60)
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
