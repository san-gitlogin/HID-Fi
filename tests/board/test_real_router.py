"""The home router, for real - a few gentle cases that check the rehearsals tell the truth.

Rehearsals (D-17) move the board in its logic while the radio stays on the router, so the
tests never stress it. Whether that matches what a real router does can only be seen on
one: here each real action is paired with its rehearsed twin, and both must end the same
way (back online, the same address, the drop counted, the router still recognised).

Gentle by design - a router must never take this for an attack:
    opt-in only: HIDFI_REAL_ROUTER=1 (never part of an automatic run)
    three real actions at most, at least SPACING_S apart, one at a time
    every real join counted by the board (radio_joins) and held to the run's budget
"""
import os
import time

import pytest

pytestmark = pytest.mark.board
SPACING_S = 180
_last_real = [0.0]


@pytest.fixture(autouse=True)
def opted_in():
    if not os.environ.get("HIDFI_REAL_ROUTER"):
        pytest.skip("real-router checks are opt-in: HIDFI_REAL_ROUTER=1 (gentle, but they do touch the router)")


def _spaced():
    wait = _last_real[0] + SPACING_S - time.time()
    if wait > 0:
        print(f"\n  waiting {wait:.0f} s between real router actions")
        time.sleep(wait)
    _last_real[0] = time.time()


def _online(board, timeout):
    end, st = time.time() + timeout, {}
    while time.time() < end:
        st = board.status()
        if st.get("wifi_connected") and st.get("wifi_ip"):
            return st
        time.sleep(1)
    raise AssertionError(f"not back online within {timeout} s: { {k: st.get(k) for k in ('sta_phase', 'wifi_ssid')} }")


def _sta_online_events(board, since):
    return [e for e in board.events[since:] if e.get("event") == "sta_online"]


def test_a_drop_ends_the_same_rehearsed_and_real(board):
    st0 = _online(board, 60)
    # rehearsed: no radio
    j0, d0, drops0 = st0["radio_joins"], st0["radio_drops"], st0["sta_drops"]
    since = len(board.events)
    assert board.cmd({"cmd": "net_fault", "kind": "drop"}, "net_fault")["status"] == "ok"
    time.sleep(3)
    sr = _online(board, 30)
    assert (sr["radio_joins"], sr["radio_drops"]) == (j0, d0), "the rehearsed drop touched the radio"
    assert sr["sta_drops"] == drops0 + 1 and sr["wifi_ip"] == st0["wifi_ip"]
    rehearsed_events = _sta_online_events(board, since)
    # real: one disconnect, one rejoin, spaced
    _spaced()
    since = len(board.events)
    assert board.cmd({"cmd": "net_fault", "kind": "drop", "radio": True}, "net_fault")["status"] == "ok"
    time.sleep(3)
    rl = _online(board, 45)
    assert rl["radio_drops"] == d0 + 1 and rl["radio_joins"] == j0 + 1, "exactly one real drop and one real join"
    assert rl["sta_drops"] == drops0 + 2 and rl["wifi_ip"] == st0["wifi_ip"]
    real_events = _sta_online_events(board, since)
    assert bool(rehearsed_events) == bool(real_events), "the dashboard is told the same either way"
    assert next(n for n in board.cmd({"cmd": "wifi_networks"}, "wifi_networks")["networks"]
                if n.get("current")).get("router_known"), "the router is recognised after a real rejoin (D-16)"


def test_a_restart_rejoins_the_router_once(board):
    _online(board, 60)
    _spaced()
    up = board.status().get("uptime_sec", 0)
    board.cmd({"cmd": "net_sim", "owners": "", "restart": True}, "net_sim")   # a software restart (USB only)
    end = time.time() + 60
    while time.time() < end:
        try:
            st = board.status()
            if st.get("uptime_sec", up) < up and st.get("wifi_connected"):
                break
        except Exception:
            pass
        time.sleep(1)
    st = _online(board, 30)
    board.cmd({"cmd": "net_sim", "off": True}, "net_sim")
    assert st["uptime_sec"] < up, "the board did not restart"
    assert st["radio_joins"] == 1, f"a restart joins the router once, not {st['radio_joins']} times"
