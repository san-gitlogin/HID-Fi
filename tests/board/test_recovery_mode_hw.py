"""Recovery mode on the board itself (docs/RECOVERY_MODE.md).

BOOT is pressed by the COM port: with DTR raised and RTS low, the dev kit's
auto-program circuit holds GPIO0 low and leaves EN high, exactly as a finger on
the button (proven on 09F7C8 before these tests were written). So every press here
is real, timed, and needs no person. The board must have its native USB plugged
into this computer too: that is where the recovery link appears.

Nothing here prints a board line or a secret: the WiFi password reply is checked by
its keys only. Every test that opens recovery closes it again.
"""
import json
import platform
import subprocess
import time

import pytest
import serial
import serial.tools.list_ports

pytestmark = pytest.mark.board

HOLD_S = 8.6                 # past RECOVERY_HOLD_MS (8 s)
LINK_WAIT_S = 20.0


# ---- helpers ------------------------------------------------------------------------
def _mac(board):
    return board.status()["mac"].replace(":", "").upper()


def _links(mac):
    return [p for p in serial.tools.list_ports.comports()
            if p.vid == 0x303A and (p.serial_number or "").replace(":", "").upper() == mac]


def _wait(pred, secs, step=0.25):
    end = time.time() + secs
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    return None


def _pump(board, secs):
    """Read the COM port for a while, keeping events (boots, recovery) only."""
    end = time.time() + secs
    while time.time() < end:
        line = board._readline()
        if line.startswith("{"):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "event" in d:
                board.events.append(d)


def _boots(board):
    return sum(1 for e in board.events if e.get("event") == "boot")


def _hold(board, secs):
    board.ser.dtr = True                 # GPIO0 low, EN high
    t0 = time.time()
    try:
        while time.time() - t0 < secs:
            _pump(board, 0.1)
    finally:
        board.ser.dtr = False            # always let go (esptool once left it held)
    return time.time() - t0


class Link:
    """The recovery link. DTR raised: the board's USB serial only answers then."""

    def __init__(self, device, dtr=True, baud=115200):
        self.s = serial.Serial()
        self.s.port, self.s.baudrate, self.s.timeout = device, baud, 0.2
        self.s.dtr, self.s.rts = dtr, False
        self.s.open()
        time.sleep(0.2)
        self.s.reset_input_buffer()

    def cmd(self, body, secs=3.0):
        self.s.write((json.dumps(body) + "\n").encode())
        end = time.time() + secs
        while time.time() < end:
            line = self.s.readline().decode(errors="ignore").strip()
            if line.startswith("{"):
                d = json.loads(line)
                if "status" in d and not d.get("event"):
                    return d
        return None

    def close(self):
        self.s.close()


def _usb_identity(mac):
    """macOS: the HID side of the board's USB identity, read passively from the
    IORegistry (no request reaches the board, so host-OS detection is undisturbed)."""
    import plistlib
    tree = plistlib.loads(subprocess.run(["ioreg", "-l", "-a", "-w0", "-r", "-c", "IOUSBHostDevice"],
                                         capture_output=True, check=True).stdout)
    roots = tree if isinstance(tree, list) else [tree]
    found = {}

    def walk(n, dev):
        if n.get("IOObjectClass") == "IOUSBHostInterface":
            dev["interfaces"].append((n.get("bInterfaceClass"), n.get("bInterfaceSubClass"),
                                      n.get("bInterfaceProtocol"), n.get("bNumEndpoints")))
        if "ReportDescriptor" in n and "report" not in dev:
            dev["report"] = bytes(n["ReportDescriptor"]).hex()
        for c in n.get("IORegistryEntryChildren", []):
            walk(c, dev)

    for r in roots:
        if r.get("USB Serial Number", "").replace(":", "").upper() == mac and r.get("IORegistryEntryID") not in found:
            dev = {"interfaces": [], "product": r.get("idProduct"), "vendor": r.get("idVendor")}
            walk(r, dev)
            found[r.get("IORegistryEntryID")] = dev
    return next(iter(found.values()), None)


@pytest.fixture
def mac(board):
    m = _mac(board)
    if not _wait(lambda: _usb_identity(m) if platform.system() == "Darwin" else True, 15):
        pytest.skip("the board's native USB is not plugged into this computer")
    assert not _links(m), "a normal boot must not show a serial link on the native USB"
    return m


@pytest.fixture
def recovery(board, mac):
    """Open recovery with a real 8.6 s press, yield the link's device, always close it."""
    b0 = _boots(board)
    _hold(board, HOLD_S)
    port = _wait(lambda: _links(mac), LINK_WAIT_S)
    assert port, "no recovery link appeared after BOOT was held 8 s and let go"
    _wait(lambda: _boots(board) > b0 or (_pump(board, 0.5) and False), 15)
    yield port[0].device
    if _links(mac):
        try:
            link = Link(port[0].device)
            link.cmd({"cmd": "recovery_exit"})
            link.close()
        except serial.SerialException:
            pass
        _wait(lambda: not _links(mac), 15)
        _pump(board, 9.0)


# ---- tests --------------------------------------------------------------------------
def test_a_normal_boot_reports_recovery_closed(board, mac):
    assert board.status()["recovery"] is False


def test_recovery_exit_outside_recovery_is_refused(board, mac):
    r = board.cmd({"cmd": "recovery_exit"})
    assert (r["status"], r["reply"]) == ("error", "not_in_recovery")


@pytest.mark.parametrize("secs", [0.5 + 2.1, 5.0])   # past the 2 s hold, so letting go never types
def test_a_shorter_hold_opens_nothing(board, mac, secs):
    b0, n0 = _boots(board), len(board.events)
    _hold(board, secs)
    _pump(board, 6.0)
    assert _boots(board) == b0, "a hold shorter than 8 s restarted the board"
    assert not [e for e in board.events[n0:] if e.get("event") == "recovery"]
    assert not _links(mac)


def test_eight_seconds_says_ready_before_letting_go(board, mac):
    n0 = len(board.events)
    board.ser.dtr = True
    try:
        _pump(board, HOLD_S)
        ready = [e for e in board.events[n0:] if e.get("event") == "recovery"]
        assert ready and ready[-1].get("state") == "ready", "no 'recovery ready' while BOOT was still held"
        assert not _links(mac), "recovery opened before BOOT was let go"
    finally:
        board.ser.dtr = False
    link = _wait(lambda: _links(mac), LINK_WAIT_S)
    assert link, "letting go after 8 s did not open recovery"
    lk = Link(link[0].device)
    assert lk.cmd({"cmd": "recovery_exit"})["reply"] == "recovery_exit"
    lk.close()
    assert _wait(lambda: not _links(mac), 15)
    _pump(board, 9.0)


def test_the_link_answers_as_the_com_port_does(board, recovery):
    link = Link(recovery)
    try:
        st = link.cmd({"cmd": "status"})
        assert st["recovery"] is True and 0 < st["recovery_left_s"] <= 300
        assert st["hid_ready"] is True
        ap = link.cmd({"cmd": "ap_password"})                 # USB only: allowed here, never printed
        assert ap["status"] == "ok" and {"ssid", "password", "open", "generated"} <= set(ap)
        assert link.cmd({"cmd": "definitely_not_a_command"})["message"] == "Unknown command"
    finally:
        link.close()


def test_the_link_stays_silent_without_dtr(board, recovery):
    quiet = Link(recovery, dtr=False)
    try:
        assert quiet.cmd({"cmd": "status"}, secs=2.0) is None
    finally:
        quiet.close()


def test_the_link_carries_no_log(board, recovery):
    link = Link(recovery)
    try:
        time.sleep(3.0)                                        # the board logs and broadcasts meanwhile
        unasked = link.s.read(link.s.in_waiting or 1)
        assert unasked in (b"",), f"{len(unasked)} bytes arrived unasked"
    finally:
        link.close()


def test_opening_the_link_never_resets_the_board(board, recovery):
    """Opening a port pulses DTR/RTS, and a 1200 baud 'touch' is how the Arduino IDE
    reboots boards: the board's CDC must ignore both (enableReboot(false))."""
    b0 = _boots(board)
    bauds = [1200, 9600, 115200, 921600, 2000000]
    for i in range(50):
        s = serial.Serial()
        s.port, s.baudrate, s.timeout = recovery, bauds[i % 5], 0.1
        s.dtr, s.rts = bool(i % 2), bool(i % 3 == 0)
        s.open()
        s.dtr, s.rts = False, True
        time.sleep(0.02)
        s.dtr, s.rts = True, False
        time.sleep(0.02)
        s.close()
    _pump(board, 3.0)
    assert _boots(board) == b0
    link = Link(recovery)
    try:
        assert link.cmd({"cmd": "status"})["recovery"] is True
    finally:
        link.close()


def test_recovery_exit_closes_it(board, recovery):
    mac = _mac(board)
    link = Link(recovery)
    r = link.cmd({"cmd": "recovery_exit"})
    link.close()
    assert r == {"status": "ok", "reply": "recovery_exit", "restart": True}
    assert _wait(lambda: not _links(mac), 15)
    _pump(board, 9.0)
    assert board.status()["recovery"] is False


def test_a_reset_closes_it(board, recovery):
    mac = _mac(board)
    b0 = _boots(board)
    board.ser.rts = True                                       # EN low: a chip reset
    time.sleep(0.15)
    board.ser.rts = False
    assert _wait(lambda: not _links(mac), 15)
    _pump(board, 9.0)
    assert _boots(board) > b0
    assert board.status()["recovery"] is False


@pytest.mark.skipif(platform.system() != "Darwin", reason="reads the USB identity from the macOS IORegistry")
def test_hid_is_byte_identical_in_and_out_of_recovery(board, mac):
    before = _usb_identity(mac)
    _hold(board, HOLD_S)
    assert _wait(lambda: _links(mac), LINK_WAIT_S)
    during = _wait(lambda: _usb_identity(mac), 10)
    link = Link(_links(mac)[0].device)
    link.cmd({"cmd": "recovery_exit"})
    link.close()
    assert _wait(lambda: not _links(mac), 15)
    _pump(board, 9.0)
    after = _wait(lambda: _usb_identity(mac), 15)
    assert during["report"] == before["report"], "the HID report descriptor changed in recovery"
    assert before["interfaces"][0] in during["interfaces"], "the HID interface changed in recovery"
    assert (2, 2, 0, 1) in during["interfaces"] and (10, 0, 0, 2) in during["interfaces"], "no CDC beside HID"
    assert after == before, "a normal boot after recovery is not byte-identical to the one before"


@pytest.mark.slow
def test_recovery_closes_itself_after_five_idle_minutes(board, recovery):
    mac = _mac(board)
    t0 = time.time()
    assert _wait(lambda: time.time() - t0 > 285, 300) and _links(mac), "closed before 5 minutes"
    assert _wait(lambda: not _links(mac), 60), "still open after 5 minutes and 45 s"
    _pump(board, 9.0)
    assert board.status()["recovery"] is False
