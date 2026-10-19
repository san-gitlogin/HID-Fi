"""v3.10 ownership rules on a real board.

- The board's WiFi password is readable over the USB cable, and never over WiFi.
- Over the home network, the first access PIN needs that password.
- Wrong PINs lock out the device that typed them; USB is never locked out.
- A connection that does not sign in is closed.

Safety: nothing here touches a PIN the owner set. Tests that need a PIN run only
on a board without one, use a temporary PIN, and check it is removed again. The
WiFi password is compared, never printed. Tests that need the home network take
--board-url ws://<board's home-network IP>:81 and skip without it.
"""
import json
import struct
import time

import pytest

pytestmark = pytest.mark.board

TEMP_PIN = "7316"


def _lan(request, board):
    from support.board import lan_url
    url = lan_url(request.config.getoption("--board-url"), board)
    if not url or "//192.168.4." in url:
        pytest.skip("needs --board-url ws://<board's home-network IP>:81 from a machine on that network")
    st = board.status()
    if not st.get("wifi_connected"):
        pytest.skip("the board is not on a home network")
    return url, st


def _ws(url):
    websocket = pytest.importorskip("websocket", reason="pip install websocket-client")
    c = websocket.create_connection(url, timeout=5)
    c.settimeout(3)
    return c


def _ask(conn, body):
    conn.send(json.dumps(body))
    end = time.time() + 5
    while time.time() < end:
        m = conn.recv()
        if isinstance(m, str):
            d = json.loads(m)
            if d.get("reply") != "auth_required" or "message" in d:     # skip the connect greeting
                return d
    raise AssertionError(f"no answer to {body['cmd']}")


class TempPin:
    """A PIN for the length of a test, on a board that had none."""

    def __init__(self, board):
        self.board = board

    def __enter__(self):
        assert self.board.cmd({"cmd": "set_auth", "token": TEMP_PIN}).get("auth_set") is True
        return TEMP_PIN

    def __exit__(self, *a):
        self.board.cmd({"cmd": "set_auth", "token": "", "old": TEMP_PIN})
        assert self.board.status().get("auth_set") is False, "the temporary PIN was not removed - remove it by hand"


def _no_pin(board):
    if board.status().get("auth_set"):
        pytest.skip("this board has an access PIN - these tests only use a temporary one on a board without")


def test_the_wifi_password_is_read_over_usb(board):
    r = board.cmd({"cmd": "ap_password"})
    assert r.get("status") == "ok", r
    assert r.get("ssid") == board.status().get("ap_ssid")
    assert r["open"] or 8 <= len(r.get("password", "")) <= 63
    assert isinstance(r.get("generated"), bool)


def test_the_wifi_password_never_travels_over_wifi(board, request):
    url, _ = _lan(request, board)
    secret = board.cmd({"cmd": "ap_password"}).get("password") or "\x00never"
    conn = _ws(url)
    try:
        conn.send(json.dumps({"cmd": "ap_password"}))
        seen = []
        end = time.time() + 3
        while time.time() < end:
            try:
                m = conn.recv()
            except Exception:
                break
            if isinstance(m, str):
                seen.append(m)
                if '"usb_only"' in m or ('"auth_required"' in m and "message" in m) or '"lan_locked"' in m:
                    break
    finally:
        conn.close()
    assert seen, "the board said nothing"
    assert not any(secret in m for m in seen), "the WiFi password was sent over WiFi"


def test_the_first_pin_over_the_home_network_needs_the_wifi_password(board, request):
    url, _ = _lan(request, board)
    _no_pin(board)
    ap = board.cmd({"cmd": "ap_password"})
    if ap.get("open"):
        pytest.skip("the board's own WiFi is open - its first PIN can only be set from that WiFi")
    conn = _ws(url)
    try:
        r = _ask(conn, {"cmd": "set_auth", "token": TEMP_PIN})
        assert r.get("reply") == "ap_password_required", r
        r = _ask(conn, {"cmd": "set_auth", "token": TEMP_PIN, "ap_password": "not-the-password"})
        assert r.get("reply") == "ap_password_required", r
        assert board.status().get("auth_set") is False, "a PIN was set without proof"
        r = _ask(conn, {"cmd": "set_auth", "token": TEMP_PIN, "ap_password": ap["password"]})
        assert r.get("reply") == "auth_set", r
    finally:
        conn.close()
        board.cmd({"cmd": "set_auth", "token": "", "old": TEMP_PIN})
        assert board.status().get("auth_set") is False, "the temporary PIN was not removed - remove it by hand"


def test_wrong_pins_lock_out_this_device_and_never_the_usb_cable(board, request):
    url, _ = _lan(request, board)
    _no_pin(board)
    with TempPin(board) as pin:
        conn = _ws(url)
        try:
            for _ in range(3):
                r = _ask(conn, {"cmd": "auth", "token": "0000"})
            r = _ask(conn, {"cmd": "auth", "token": pin})
            assert r.get("reply") == "auth_locked", f"the right PIN must be refused while this device is locked: {r}"
        finally:
            conn.close()
        # The cable is never locked out.
        assert board.cmd({"cmd": "auth", "token": pin}).get("reply") == "auth_ok"


def test_a_connection_that_never_signs_in_is_closed(board, request):
    url, _ = _lan(request, board)
    _no_pin(board)
    with TempPin(board):
        conn = _ws(url)
        conn.settimeout(30)
        t0 = time.time()
        closed = False
        try:
            while time.time() - t0 < 28:
                m = conn.recv()
                if m in ("", None):
                    closed = True
                    break
        except Exception:
            closed = True
        finally:
            conn.close()
        assert closed, "an unsigned connection stayed open for 28 s"
        assert 18 <= time.time() - t0 <= 28, f"closed after {time.time() - t0:.1f} s, expected about 20"
