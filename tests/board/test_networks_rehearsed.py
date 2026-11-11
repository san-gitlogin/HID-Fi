"""Where the board goes, rehearsed on the real board: no second router, nobody walking about.

D-02/D-10 decide which network the board is on from what it scans and where the owner's
devices are. Proving that needed a person carrying a phone hotspot about (test_two_homes).
net_sim (USB only, shipped firmware) stages it instead: a made-up network appears in the
board's scans with the strength a test gives it, is joined over the real home network
(the carrier) and counts as a network of its own; "owners" says which network the owner's
devices are on - anywhere else they do not count, as behind another router; speed makes
the 5-minute and 2-minute timers 15 times faster. Everything else is the real firmware
on the real radio: the scans, the joins, the decisions, the marks it saves.

| Story | Staged | Must happen |
|---|---|---|
| joined where nobody is | the made-up network is stronger, the owner is home | it comes home |
| a stronger network nearby | owner home, made-up network 10 dB stronger | it never hops |
| restarted | the stronger network still there | straight to the preferred one |
| the owner moves, hotspot goes | Bring here, then that network vanishes | follows, then finds the owner again |
| Bring here, out of range | a saved network that is not there | back on the home network, preferred again |
| weak access point | one 28 dB stronger of the same network | stays while used, roams when not |
| an extender | another name, same subnet | one network: no "coming home" |

The owner's real Companion on this Mac is the "owner device" (it counts as directly on
the network). Needs --board, the board on its home network and one free saved-network
slot (no saved network is ever pushed out). Prints network names and decisions only.
Every test puts back what it changed: test networks off and forgotten, home preferred.
"""
import time

import pytest

pytestmark = pytest.mark.board

SPEED = 15                      # 5 min -> 20 s, the 2-minute look -> 8 s
B, E = "HIDFI-SIM-B", "HIDFI-SIM-E"
PASSWORD = "rehearsal-only"     # never sent to any router: a made-up network is joined over the carrier


def _sim(board, **kw):
    r = board.cmd(dict({"cmd": "net_sim"}, **kw), "net_sim")
    assert r.get("status") == "ok", r
    return r


def _nets(board):
    r = board.cmd({"cmd": "wifi_networks"}, "wifi_networks")
    return {n["ssid"]: n for n in r.get("networks", [])}


def _current(board):
    for name, n in _nets(board).items():
        if n.get("current"):
            return name
    return None


def _preferred(board):
    for name, n in _nets(board).items():
        if n.get("preferred"):
            return name
    return None


def _wait(board, pred, timeout, what):
    end, last = time.time() + timeout, None
    while time.time() < end:
        try:
            last = _nets(board)
            if pred(last):
                return last
        except Exception:
            pass
        time.sleep(1)
    raise AssertionError(f"{what} - not within {timeout}s. Networks: "
                         + ", ".join(f"{k}{[f for f in ('current', 'used', 'computer', 'preferred') if v.get(f)]}"
                                     for k, v in (last or {}).items()))


def _on(name):
    return lambda nets: nets.get(name, {}).get("current")


def _stays(board, name, secs):
    end = time.time() + secs
    while time.time() < end:
        cur = _current(board)
        assert cur in (name, None), f"moved to {cur} - it must stay on {name}"
        time.sleep(1)
    assert _current(board) == name


def _moves(board, since):
    return [e for e in board.events[since:] if e.get("event") == "wifi_moving"]


def _add(board, ssid):
    r = board.cmd({"cmd": "wifi_configure", "ssid": ssid, "password": PASSWORD}, timeout=10)
    assert r.get("status") == "ok", r


@pytest.fixture
def home(board):
    """The board on its own network, owner devices counted there; put back afterwards."""
    end = time.time() + 60
    while time.time() < end and not board.status().get("wifi_connected"):
        time.sleep(1)
    st = board.status()
    if not st.get("wifi_connected"):
        pytest.skip("the board is not on a home network")
    nets = _nets(board)
    home = _current(board)
    if not home or home.startswith("HIDFI-SIM"):
        pytest.skip(f"the board is on {home!r}, not on a real home network")
    if len([n for n in nets if not n.startswith("HIDFI-SIM")]) >= 5:
        pytest.skip("all 5 saved-network slots are in use; a test network would push a saved one out")
    _sim(board, off=True)
    for n in [n for n in nets if n.startswith("HIDFI-SIM")]:
        board.cmd({"cmd": "wifi_forget", "ssid": n})
    yield home
    try:
        _sim(board, off=True)
        for n in [n for n in _nets(board) if n.startswith("HIDFI-SIM")]:
            board.cmd({"cmd": "wifi_forget", "ssid": n})
        board.cmd({"cmd": "wifi_prefer", "ssid": home, "now": True, "force": True})
        _wait(board, lambda n: n.get(home, {}).get("current") and n.get(home, {}).get("preferred"), 90,
              f"back on {home}, preferred, after the test")
    finally:
        pass


def test_joined_where_nobody_is_it_comes_home(board, home):
    _sim(board, nets=[{"ssid": B, "rssi": -35}], owners=home, speed=SPEED)
    _add(board, B)                                        # joins it at once, as a person adding it would
    _wait(board, _on(B), 40, f"on {B} after adding it")
    since = len(board.events)
    _wait(board, _on(home), 90, f"home again on {home} - nobody is on {B}")
    assert any(m.get("why") == "coming_home" for m in _moves(board, since)), _moves(board, since)
    assert _preferred(board) == home


def test_a_stronger_network_nearby_never_pulls_it_away(board, home):
    _sim(board, nets=[{"ssid": B, "rssi": -25}], owners=home, speed=SPEED)
    _add(board, B)
    _wait(board, _on(home), 90, f"home on {home}")
    since = len(board.events)
    _stays(board, home, 45)                               # five looks at this speed
    assert not _moves(board, since), _moves(board, since)


def test_restarted_it_goes_straight_to_the_preferred_network(board, home):
    _sim(board, nets=[{"ssid": B, "rssi": -25}], owners=home, speed=SPEED)
    _add(board, B)
    _wait(board, _on(home), 90, f"home on {home}")
    up = board.status().get("uptime_sec", 0)
    _sim(board, restart=True)
    end = time.time() + 40
    while time.time() < end:
        try:
            st = board.status()
            if st.get("uptime_sec", up) < up and st.get("wifi_connected"):
                break
        except Exception:
            pass
        time.sleep(1)
    st = board.status()
    assert st.get("uptime_sec", up) < up, "the board did not restart"
    assert _current(board) == home and st.get("wifi_choice") == "preferred", \
        {"on": _current(board), "choice": st.get("wifi_choice")}
    assert board.cmd({"cmd": "net_sim"}, "net_sim").get("on"), "the rehearsal did not survive its own restart"


def test_the_owner_moves_and_their_hotspot_goes_it_finds_them_again(board, home):
    _sim(board, nets=[{"ssid": B, "rssi": -50}], owners=home, speed=SPEED)
    _add(board, B)
    _wait(board, _on(home), 90, f"home on {home}")
    _sim(board, owners=B)                                 # the owner (and the Mac) now on the hotspot
    board.cmd({"cmd": "wifi_prefer", "ssid": B, "now": True, "force": True}, "wifi_prefer")   # Bring the board here
    _wait(board, lambda n: _on(B)(n) and n[B].get("preferred"), 60, f"on {B}, preferred")
    _stays(board, B, 25)                                  # the owner is here: it stays
    _sim(board, nets=[], owners=home)                     # the hotspot is switched off; the owner is home
    _wait(board, lambda n: _on(home)(n) and n[home].get("preferred"), 120,
          f"back on {home}, where the owner is, preferred again")


def test_brought_to_a_network_out_of_range_it_comes_back(board, home):
    _sim(board, nets=[{"ssid": B, "rssi": -50}], owners=home, speed=SPEED)
    _add(board, B)
    _wait(board, _on(home), 90, f"home on {home}")
    _sim(board, nets=[])                                  # B saved, but not there any more
    board.cmd({"cmd": "wifi_prefer", "ssid": B, "now": True, "force": True}, "wifi_prefer")
    _wait(board, lambda n: _on(home)(n) and n[home].get("preferred"), 120,
          f"on {home} again and preferred, after asking for a network that is not there")


def test_a_weak_access_point_roams_only_when_nobody_uses_it(board, home):
    _sim(board, nets=[{"ssid": home, "rssi": -50}], link_rssi=-78, owners=home, speed=SPEED)
    since = len(board.events)
    _stays(board, home, 30)
    assert not [m for m in _moves(board, since) if m.get("why") == "roaming"], "roamed while the owner was using it"
    _sim(board, owners="-")                               # nobody using it now
    end = time.time() + 40
    while time.time() < end and not [m for m in _moves(board, since) if m.get("why") == "roaming"]:
        board.status()                                    # collects the events
        time.sleep(1)
    roams = [m for m in _moves(board, since) if m.get("why") == "roaming"]
    assert roams and roams[0].get("to") == home, _moves(board, since)
    _sim(board, link_rssi=0, owners=home)                 # on the strong one now
    _wait(board, _on(home), 60, f"rejoined {home} after roaming")


def test_an_extender_with_another_name_is_the_same_network(board, home):
    _sim(board, nets=[{"ssid": E, "rssi": -35, "same_net": True}], owners=home, speed=SPEED)
    _add(board, E)
    _wait(board, _on(E), 40, f"on {E}")
    since = len(board.events)
    _stays(board, E, 40)                                  # owner's network: same subnet, so no coming home
    assert not _moves(board, since), _moves(board, since)


def test_net_sim_is_usb_only(board, home):
    pytest.importorskip("websocket", reason="pip install websocket-client")
    import json
    from support.board import ws_connect
    st = board.status()
    c = ws_connect(f"ws://{st['wifi_ip']}:81", timeout=5)
    try:
        c.recv()                                          # auth_required, or nothing to read yet
    except Exception:
        pass
    try:
        c.send(json.dumps({"cmd": "net_sim", "owners": "-"}))
        r = json.loads(c.recv())
        while r.get("event"):
            r = json.loads(c.recv())
    finally:
        c.close()
    assert r.get("reply") in ("usb_only", "auth_required") or r.get("status") == "error", r
    assert not board.cmd({"cmd": "net_sim"}, "net_sim").get("on"), "a network request turned the rehearsal on"


def test_asked_to_move_during_a_join_it_goes_where_asked(board, home):
    """Found 2026-11-11: "move now" while a join was still on its way was noted, and then the
    join finished and kept the board on the wrong network until the next 2-minute look."""
    _sim(board, nets=[{"ssid": B, "rssi": -50}], owners=home)          # real timers: no 2-minute look in this window
    _add(board, B)                                                     # the join to B starts
    r = board.cmd({"cmd": "wifi_prefer", "ssid": home, "now": True, "force": True}, "wifi_prefer")
    assert r.get("status") == "ok", r
    _wait(board, _on(home), 30, f"on {home}, as asked while the join to {B} was on its way")
    _stays(board, home, 20)
