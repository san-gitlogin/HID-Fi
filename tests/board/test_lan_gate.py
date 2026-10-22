"""The LAN gate holds for binary frames too (fixed in v3.9).

With no access PIN set and the board on a home network, a device reaching it
over that network must not be able to drive the computer. processCommand()
refused its JSON commands, but keys, pointer, buttons and media arrive as binary
WebSocket frames and never went through it - so any device on the LAN could
still type. onWsEvent() now drops them for such a client (wsFromLan).

How it is checked without risk: from this machine, over the home network, send
pointer frames that move by zero - harmless even if the gate failed - and read
the board's command counter over serial. A binary frame that gets past the gate
is counted; a dropped one is not.

Runs only when it can say something true: --board (serial) and --board-url
pointing at the board's HOME-network address (not 192.168.4.x), the board with
no access PIN and on a network. Anything else is a skip with the reason.
"""
import json
import struct
import time

import pytest

from support.board import ws_connect

pytestmark = pytest.mark.board


def test_binary_frames_from_the_lan_are_refused_without_a_pin(board, request):
    from support.board import lan_url
    url = lan_url(request.config.getoption("--board-url"), board)
    if not url:
        pytest.skip("needs --board-url ws://<board's home-network IP>:81 from a machine on that network")
    if "//192.168.4." in url:
        pytest.skip("--board-url is the board's own access point; the LAN gate only applies on the home network")
    st = board.status()
    if st.get("auth_set"):
        pytest.skip("the board has an access PIN - the LAN gate only applies without one")
    if not st.get("wifi_connected"):
        pytest.skip("the board is not on a home network")
    pytest.importorskip("websocket", reason="pip install websocket-client")

    conn = ws_connect(url, timeout=5)
    try:
        # JSON from the LAN without a PIN: refused, and says why (the existing gate).
        conn.send(json.dumps({"cmd": "media", "key": "nope"}))
        reply = json.loads(conn.recv())
        assert reply.get("reply") == "lan_locked", reply

        before = board.status()["cmd_count"]
        for _ in range(5):
            conn.send_binary(struct.pack("<Bhhbb", 1, 0, 0, 0, 0))     # MOVE by nothing
        time.sleep(0.6)
        after = board.status()["cmd_count"]
    finally:
        conn.close()
    # The status read in between is itself one command.
    assert after - before <= 1, (f"{after - before - 1} zero-move frame(s) from the LAN reached the HID path - "
                                 f"the binary LAN gate (wsFromLan in onWsEvent) is not holding")

    # The control: the same frames, from the same place, with a PIN set and the
    # socket signed in, MUST be counted - otherwise the check above proves
    # nothing. The PIN is temporary and removed again (the board had none; the
    # vault is untouched because the old PIN is given back).
    pin = "7316"
    assert board.cmd({"cmd": "set_auth", "token": pin}).get("auth_set") is True
    try:
        conn = ws_connect(url, timeout=5)
        conn.settimeout(2)
        conn.send(json.dumps({"cmd": "auth", "token": pin}))
        authed = False
        for _ in range(6):
            m = conn.recv()
            if isinstance(m, str) and json.loads(m).get("reply") in ("auth_ok", "auth_failed", "auth_locked"):
                authed = json.loads(m).get("reply") == "auth_ok"
                break
        assert authed, "could not sign in with the temporary PIN"
        before = board.status()["cmd_count"]
        for _ in range(5):
            conn.send_binary(struct.pack("<Bhhbb", 1, 0, 0, 0, 0))
        time.sleep(0.6)
        after = board.status()["cmd_count"]
        conn.close()
    finally:
        board.cmd({"cmd": "set_auth", "token": "", "old": pin})
        assert board.status().get("auth_set") is False, "the temporary PIN was not removed - remove it by hand"
    assert after - before - 1 == 5, f"control: only {after - before - 1}/5 signed-in frames were counted"
