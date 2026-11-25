"""D-14 on a real board: the paired computer and the board on two networks of one home.

Seen at the owner's home (2026-11-23): "ARVISANTU" is the main router (192.168.1.x),
"ARVISANTU C6" an extender with its own subnet (192.168.0.x) behind it; the Mac and the
board each move between them, and the
Companion said "not on this computer's WiFi". A Companion played over real HTTP
(tests/support/companion_sim.FakeApp, paired with the code over USB):

    together on one network, the heartbeat says so and names it
    an address on a network the board never joined is unknown, never guessed
    with the board moved to the home's other network (when this computer can still
      reach it there), the heartbeat names both, and "bring" moves it back

The board's preferred network is put back and the test pairing removed afterwards.
Needs the board on this computer's network; the move needs a second saved network
this computer can route to - otherwise that part skips, saying why.
"""
import socket
import time

import pytest

from support import companion_sim as cs

pytestmark = pytest.mark.board
NAME = "Network test"


def _status(board):
    return board.cmd({"cmd": "status"}, "status")


def _wait(board, pred, what, timeout=90):
    end = time.time() + timeout
    st = {}
    while time.time() < end:
        st = _status(board)
        if pred(st):
            return st
        time.sleep(3)
    raise AssertionError(f"{what} - not within {timeout}s; last: { {k: st.get(k) for k in ('wifi_ssid', 'wifi_ip', 'sta_phase')} }")


def _toward(ip):
    """This computer's own address on the way to ip (what the Companion sends as via)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((ip, 80))
        return s.getsockname()[0]
    finally:
        s.close()


@pytest.fixture
def paired(board):
    st = _wait(board, lambda s: s.get("wifi_connected") and s.get("wifi_ip"), "on a home network")
    if not st.get("auth_set"):
        pytest.skip("the Companion endpoint needs an access PIN on the board")
    start_pref = st.get("wifi_preferred") or st.get("wifi_ssid")
    for p in board.cmd({"cmd": "companion_list"}, "companion_list").get("paired", []):
        if p.get("name") == NAME:                          # a leftover of this test only
            board.cmd({"cmd": "companion_unpair", "id": p["id"]}, "companion_unpair")
    a = cs.FakeApp(cs.RealBoardHttp(st["wifi_ip"]), name=NAME)
    a.ip = _toward(st["wifi_ip"])
    assert a.hello()[1].get("state") == "pending"
    assert board.cmd({"cmd": "companion_pair", "id": a.id}, "companion_pair").get("state") == "confirm"
    assert a.advance()[1].get("state") == "code_shown"
    assert board.cmd({"cmd": "companion_pair", "id": a.id, "code": a.code}, "companion_pair").get("state") == "paired"
    yield a, st
    board.cmd({"cmd": "companion_unpair", "id": a.id}, "companion_unpair")
    if start_pref:
        board.cmd({"cmd": "wifi_prefer", "ssid": start_pref, "now": True, "force": True}, "wifi_prefer")
        _wait(board, lambda s: s.get("wifi_ssid") == start_pref and s.get("wifi_connected"), f"back on {start_pref}")


@pytest.mark.rehearsal
def test_together_the_heartbeat_names_the_one_network(paired):
    app, st = paired
    r = app.beat(via=app.ip)
    assert r["status"] == "ok", r
    assert {k: r["net"][k] for k in ("board", "here", "computer")} == {"board": st["wifi_ssid"], "here": True, "computer": st["wifi_ssid"]}, r
    assert any(sn.startswith(st["wifi_ip"].rsplit(".", 1)[0]) for sn in r["net"]["subnets"]), r


@pytest.mark.rehearsal
def test_a_network_the_board_never_joined_is_unknown(paired):
    app, _ = paired
    r = app.beat(via="10.99.0.5")
    assert r["net"]["computer"] == "" and r["net"]["here"] is False, r
    r = app.bring("10.99.0.5")
    assert r.get("reply") == "unknown_network", r


def test_on_two_networks_the_board_is_named_and_brought_back(board, paired):
    # Real moves between real networks - the one test that has to join the owner's other
    # router for real (D-14 is about real routing). Never automatic (D-17): with
    # HIDFI_REAL_MOVES=1, when the owner says so, ideally at home with both routers.
    import os
    if not os.environ.get("HIDFI_REAL_MOVES"):
        pytest.skip("real moves between the owner's routers are opt-in (HIDFI_REAL_MOVES=1, D-17)")
    app, st = paired
    home = st["wifi_ssid"]
    nets = board.cmd({"cmd": "wifi_networks"}, "wifi_networks").get("networks", [])
    # most recently used first: a network of this place, not one from a trip (an
    # out-of-range one only costs a wait, and its join attempt then gets in the way)
    others = [n["ssid"] for n in sorted(nets, key=lambda n: -(n.get("used") or 0))
              if n.get("proven") and n["ssid"] != home]
    if not others:
        pytest.skip("only one proven saved network: no second network to move the board to")
    away = None
    for ssid in others:
        print(f"{time.strftime('%H:%M:%S')} trying the board on another saved network", flush=True)
        rp = board.cmd({"cmd": "wifi_prefer", "ssid": ssid, "now": True, "force": True}, "wifi_prefer")
        try:
            s2 = _wait(board, lambda s: s.get("wifi_ssid") == ssid and s.get("wifi_connected"), f"on {ssid}", 45)
        except AssertionError as e:
            print(f"  not there: {e}\n  prefer said {rp}\n  " + "\n  ".join(list(board.net_log)[-14:]), flush=True)
            continue
        if s2["wifi_ip"].rsplit(".", 1)[0] == st["wifi_ip"].rsplit(".", 1)[0]:
            continue                                         # the same subnet: one network (router + extender)
        try:
            app.board = cs.RealBoardHttp(s2["wifi_ip"])
            if app.beat(via=app.ip).get("status") == "ok":
                away = (ssid, s2)
                break
        except OSError:
            continue
    if not away:
        pytest.skip("no other saved network this computer can reach the board on")
    ssid, s2 = away
    r = app.beat(via=app.ip)
    assert {k: r["net"][k] for k in ("board", "here", "computer")} == {"board": ssid, "here": False, "computer": home}, r
    print(f"{time.strftime('%H:%M:%S')} away on {s2['wifi_ip']}, asking to bring it back", flush=True)
    t0 = time.time()
    try:
        r = app.bring(app.ip)
        assert r.get("status") == "ok" and r.get("ssid") == home and not r.get("busy"), r
        print(f"  answered in {time.time() - t0:.1f}s", flush=True)
    except OSError as e:
        # The board leaves its network to do what was asked: an answer lost on the way out
        # is allowed (the Companion then says "moving" and finds it); the move is not.
        print(f"  no answer ({e!r}) - the move must still happen", flush=True)
    s3 = _wait(board, lambda s: s.get("wifi_ssid") == home and s.get("wifi_connected"), f"brought back to {home}")
    app.board = cs.RealBoardHttp(s3["wifi_ip"])
    n = app.beat(via=app.ip)["net"]
    assert (n["board"], n["here"], n["computer"]) == (home, True, home), n


@pytest.mark.rehearsal
def test_a_newer_choice_cancels_a_move_still_waiting(board):
    """"Move now" answers first and moves 3 s later (D-14). Asked within those 3 s for
    the network it is already on, the board stays: the earlier move must not still run
    and drop its link (code review, 2026-11-23). Over USB, so nothing is cut off."""
    st = _wait(board, lambda s: s.get("wifi_connected") and s.get("wifi_ip"), "on a home network")
    home = st["wifi_ssid"]
    start_pref = st.get("wifi_preferred") or home
    nets = board.cmd({"cmd": "wifi_networks"}, "wifi_networks").get("networks", [])
    others = [n["ssid"] for n in nets if n["ssid"] != home and n.get("proven")]
    if not others:
        pytest.skip("only one proven saved network: nothing to change its mind about")
    # D-17: inside a rehearsal - were the cancel broken, the move it failed to stop would
    # happen in the board's logic only (and this test would still see it), never the radio.
    board.cmd({"cmd": "net_sim", "owners": ""}, "net_sim")
    try:
        # A saved network on the same subnet counts as this one (D-02: a router and its
        # extender) - two homes both on 192.168.0.x do (Bangalore, 2026-11-25): asking for
        # it moves nothing. Take one the board counts as another network.
        for other in others:
            r = board.cmd({"cmd": "wifi_prefer", "ssid": other, "now": True, "force": True}, "wifi_prefer")
            assert r.get("status") == "ok", r
            if r.get("on_it") is False:
                break
        else:
            board.cmd({"cmd": "wifi_prefer", "ssid": home, "force": True}, "wifi_prefer")
            pytest.skip("every other saved network counts as this one here (same subnet): no move to cancel")
        r = board.cmd({"cmd": "wifi_prefer", "ssid": home, "now": True, "force": True}, "wifi_prefer")
        assert r.get("status") == "ok" and r.get("on_it") is True, r
        end = time.time() + 12                       # well past the 3 s the first move waited
        while time.time() < end:
            s = _status(board)
            assert s.get("wifi_connected") and s.get("wifi_ssid") == home and s.get("wifi_ip") == st["wifi_ip"], \
                f"the cancelled move still ran: { {k: s.get(k) for k in ('wifi_ssid', 'wifi_connected', 'sta_phase')} }"
            time.sleep(0.5)
    finally:
        board.cmd({"cmd": "net_sim", "off": True}, "net_sim")
        if start_pref != home:
            board.cmd({"cmd": "wifi_prefer", "ssid": start_pref, "force": True}, "wifi_prefer")
