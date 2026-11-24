"""A shortcut never stalls the pointer, on a real board (hid_fi/combo.h).

`press` held loop() for 150 ms per shortcut until 2026-11-23: with a shortcut five
times a second the dashboard's ping went from 8 ms to a 96 ms median on 09F7C8.
Here one socket presses Shift (harmless on any host) five times a second while
another pings, in alternating phases with quiet ones, and every press is answered.
Needs the board on this computer's network and HIDFI_PIN when a PIN is set.
"""
import json
import struct
import threading
import time

import pytest

from support.latency_probe import Ws, stats

pytestmark = [pytest.mark.board, pytest.mark.slow]


def _host(board, wait=40):
    end = time.time() + wait
    while time.time() < end:
        st = board.cmd({"cmd": "status"}, "status")
        if st.get("wifi_connected") and st.get("wifi_ip"):
            from support.board import reachable
            return reachable(st["wifi_ip"]), st.get("auth_set")
        time.sleep(2)
    pytest.skip("the board is not on a home network this computer shares")


def test_shortcuts_five_times_a_second_leave_the_ping_where_it_was(board, request):
    host, auth = _host(board)
    pin = request.config.getoption("--board-pin") or ""
    if auth and not pin:
        pytest.skip("the board has an access PIN: set HIDFI_PIN")
    probe, presser = Ws(f"ws://{host}:81", pin), Ws(f"ws://{host}:81", pin)
    replies = {"pressed": 0, "sent": 0}
    try:
        quiet, loaded = [], []
        for phase in range(4):
            busy = phase % 2 == 1
            stop = threading.Event()

            def press_loop():
                presser.c.settimeout(0.05)
                while not stop.is_set():
                    presser.c.send(json.dumps({"cmd": "press", "keys": "shift"}))
                    replies["sent"] += 1
                    end = time.time() + 0.2
                    while time.time() < end:
                        try:
                            m = presser.c.recv_data(control_frame=True)[1]
                            if isinstance(m, bytes) and b'"reply":"pressed"' in m:
                                replies["pressed"] += 1
                        except Exception:
                            pass
            t = threading.Thread(target=press_loop, daemon=True) if busy else None
            if t:
                t.start()
            end = time.time() + 20
            while time.time() < end:
                (loaded if busy else quiet).append(probe.ping())
                time.sleep(0.05)
            stop.set()
            if t:
                t.join(3)
        q, l = stats(quiet), stats(loaded)
        detail = json.dumps({"quiet": q, "with_shortcuts": l, **replies})
        assert l["p50"] <= q["p50"] + 3.0, f"shortcuts stalled the pointer path: {detail}"
        assert replies["pressed"] >= replies["sent"] - 2, f"a shortcut went unanswered: {detail}"
    finally:
        probe.close()
        presser.close()
