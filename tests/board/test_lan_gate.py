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

pytestmark = pytest.mark.board


def test_binary_frames_from_the_lan_are_refused_without_a_pin(board, request):
    url = request.config.getoption("--board-url")
    if not url:
        pytest.skip("needs --board-url ws://<board's home-network IP>:81 from a machine on that network")
    if "//192.168.4." in url:
        pytest.skip("--board-url is the board's own access point; the LAN gate only applies on the home network")
    st = board.status()
    if st.get("auth_set"):
        pytest.skip("the board has an access PIN - the LAN gate only applies without one")
    if not st.get("wifi_connected"):
        pytest.skip("the board is not on a home network")
    websocket = pytest.importorskip("websocket", reason="pip install websocket-client")

    conn = websocket.create_connection(url, timeout=5)
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
