"""The home network having a bad night, on a real board.

A router reboots, kicks the board, is off when the board starts, or hands it a
new address. None of that is done to the real router here: net_fault (serial
only) makes the board behave as if it happened - the supervisor then has to do
exactly what it would do for real: notice, keep the access point up, rejoin,
notice a changed address, and be reachable again over the network.

| Case | What is staged | What must happen |
|---|---|---|
| kicked | the router lets the board go | back on the network within 15 s, same address |
| router off | the network vanishes for 40 s | access point stays up, reconnecting, back within 40 s of the router |
| off at boot | the board starts while the router is off | it keeps trying and joins once the router is back |
| new address | the board last held another address | the change is noticed, reported, kept, announced |
| auto-off | AP switched off to save power, then the network goes | the AP comes back by itself |
| reachable | after each | WebSocket and mDNS answer at the board's address |

Needs --board and the board on a home network. Nothing here types or moves the
mouse. Every test puts back what it changed.
"""
import json
import socket
import time

import pytest

pytestmark = pytest.mark.board


def _online(board):
    st = board.status()
    if not st.get("wifi_connected"):
        pytest.skip("the board is not on a home network")
    return st


def _fault(board, **kw):
    r = board.cmd(dict({"cmd": "net_fault"}, **kw))
    assert r.get("reply") == "net_fault", r
    return r


def _wait(board, pred, timeout, what):
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        try:
            last = board.status()
        except Exception:
            last = {}
        if last and pred(last):
            return time.time() - t0, last
        time.sleep(1)
    raise AssertionError(f"{what} - not within {timeout}s. Last status: "
                         f"phase={last.get('sta_phase')} connected={last.get('wifi_connected')} "
                         f"down={last.get('sta_down_s')} reason={last.get('sta_reason')}")


def _reachable(request, ip):
    """Over the network, not the cable: the dashboard's socket answers."""
    websocket = pytest.importorskip("websocket", reason="pip install websocket-client")
    end = time.time() + 20
    while time.time() < end:
        try:
            c = websocket.create_connection(f"ws://{ip}:81/", timeout=4)
            c.send(json.dumps({"cmd": "ping"}))
            m = c.recv()
            c.close()
            return json.loads(m)
        except Exception:
            time.sleep(1)
    raise AssertionError(f"nothing answered at ws://{ip}:81/ over the home network")


@pytest.fixture
def home(board):
    """The board on its network before, and back on it after - whatever happened."""
    st = _online(board)
    yield st
    try:
        _fault(board, kind="clear")
        _wait(board, lambda s: s.get("wifi_connected"), 90, "the board did not rejoin after the test")
    finally:
        pass


def test_kicked_by_the_router_it_rejoins_at_once(board, home, request):
    drops = home.get("sta_drops", 0)
    _fault(board, kind="drop")
    took, st = _wait(board, lambda s: s.get("wifi_connected") and s.get("sta_drops", 0) > drops, 20,
                     "rejoining after being let go")
    assert took < 15, f"rejoined after {took:.0f}s"
    assert st["wifi_ip"] == home["wifi_ip"], "a plain rejoin should keep the address"
    _reachable(request, st["wifi_ip"])


def test_router_off_for_a_while_the_ap_stays_and_it_comes_back(board, home, request):
    _fault(board, kind="vanish", s=40)
    _, st = _wait(board, lambda s: not s.get("wifi_connected"), 10, "noticing the network is gone")
    t_gone = time.time()
    mid = _wait(board, lambda s: s.get("sta_phase") in ("reconnecting", "connecting") and s.get("sta_down_s", 0) >= 15,
                40, "saying it is reconnecting")[1]
    assert mid.get("ap_running") is not False, "the board's own WiFi went away with the home network"
    took, st = _wait(board, lambda s: s.get("wifi_connected"), 100, "rejoining once the router is back")
    back_after_router = (time.time() - t_gone) - 40
    assert back_after_router < 40, f"back {back_after_router:.0f}s after the router - should be under 40"
    assert st["sta_phase"] == "online" and "sta_down_s" not in st
    _reachable(request, st["wifi_ip"])


def test_router_off_when_the_board_starts(board, home, request, _board_holder):
    _fault(board, kind="vanish", s=30, at_boot=True)          # the board restarts itself
    time.sleep(8)
    st = board.status()
    assert st.get("uptime_sec", 99) < 30, "the board did not restart"
    assert not st.get("wifi_connected")
    assert st.get("sta_phase") in ("connecting", "reconnecting"), \
        f"a board that booted before the router must keep trying, not give up: {st.get('sta_phase')}"
    took, st = _wait(board, lambda s: s.get("wifi_connected"), 110, "joining once the router came up")
    _reachable(request, st["wifi_ip"])


def test_a_new_address_is_noticed_reported_and_kept(board, home, request):
    real = home["wifi_ip"]
    fake = "192.168.1.250" if real != "192.168.1.250" else "192.168.1.251"
    changes = home.get("sta_ip_changes", 0)
    _fault(board, kind="ip_was", ip=fake)
    board.events.clear()
    _fault(board, kind="drop")
    _, st = _wait(board, lambda s: s.get("wifi_connected") and s.get("sta_ip_changes", 0) > changes, 25,
                  "noticing the address differs from the last one")
    assert st["sta_prev_ip"] == fake and st["wifi_ip"] == real and st["sta_last_ip"] == real
    ev = [e for e in board.events if e.get("event") == "sta_online"]
    assert ev and ev[-1]["changed"] is True and ev[-1]["prev_ip"] == fake and ev[-1]["ip"] == real, board.events
    assert ev[-1].get("mdns"), "the announcement must carry the name that follows the board"


def test_with_the_ap_switched_off_it_comes_back_when_the_network_goes(board, home):
    was = bool(home.get("ap_auto_off"))
    try:
        board.cmd({"cmd": "power_mode", "ap_auto_off": True})
        _fault(board, kind="ap_off")
        assert board.status().get("ap_running") is False
        _fault(board, kind="vanish", s=45)
        took, st = _wait(board, lambda s: s.get("ap_running"), 40, "bringing the access point back")
        assert took < 35, f"the access point took {took:.0f}s"
    finally:
        board.cmd({"cmd": "power_mode", "ap_auto_off": was})
        _fault(board, kind="clear")
        _wait(board, lambda s: s.get("ap_running") and s.get("wifi_connected"), 90, "restoring")


def test_the_name_follows_the_board(board, home):
    name = home.get("mdns")
    if not name:
        pytest.skip("mDNS is not running")
    try:
        ip = socket.gethostbyname(name + ".local")
    except OSError as e:
        pytest.skip(f"this machine cannot resolve .local names ({e})")
    assert ip == home["wifi_ip"], f"{name}.local resolves to {ip}, the board is at {home['wifi_ip']}"


def test_network_tests_are_usb_only(board, home, request):
    websocket = pytest.importorskip("websocket", reason="pip install websocket-client")
    if home.get("auth_set"):
        pytest.skip("needs a board without a PIN to talk to it over the network unsigned")
    url = f"ws://{home['wifi_ip']}:81"           # where it is now, whatever was typed
    c = websocket.create_connection(url, timeout=5)
    try:
        c.send(json.dumps({"cmd": "net_fault", "kind": "drop"}))
        r = json.loads(c.recv())
    finally:
        c.close()
    assert r.get("reply") in ("usb_only", "lan_locked"), r
    assert board.status().get("wifi_connected"), "a network request must not be able to stage a fault"


def test_find_lists_this_board_where_it_is_now(board, home):
    import sys
    from support import paths
    sys.path.insert(0, str(paths.TESTS.parent / "tools"))
    import hidfi
    found = {}
    for _ in range(4):                       # mDNS needs a moment after the rejoins above
        found = hidfi.find()
        if found:
            break
        time.sleep(2)
    if not found:
        pytest.skip("no mDNS answer reached this machine (multicast blocked on this network?)")
    assert found.get(home["mdns"] + ".local") == home["wifi_ip"], found


def test_sharing_the_internet_turns_on_off_and_leaves_the_board_reachable(board, home, request):
    was = bool(home.get("ap_share"))
    try:
        r = board.cmd({"cmd": "internet_share", "on": True})
        assert r.get("reply") == "internet_share" and r.get("on") is True, r
        _, st = _wait(board, lambda s: s.get("ap_share_active"), 10, "sharing becoming active")
        assert st.get("ap_running") and st.get("wifi_connected")
        _reachable(request, st["wifi_ip"])
        # With the home network gone it must stand down, and come back with it.
        _fault(board, kind="vanish", s=20)
        _wait(board, lambda s: not s.get("ap_share_active"), 15, "sharing standing down without a network")
        _wait(board, lambda s: s.get("ap_share_active") and s.get("wifi_connected"), 90, "sharing resuming")
    finally:
        board.cmd({"cmd": "internet_share", "on": was})
    st = _wait(board, lambda s: bool(s.get("ap_share")) == was, 10, "putting sharing back")[1]
    _reachable(request, st["wifi_ip"])


# ---- saved networks, like a phone (v3.11) ------------------------------------------------
# Only ever a fake network is added and forgotten here. The owner's real network is
# never forgotten or pushed out (five saved would evict the oldest) - its password
# is not something a test could put back.
FAKE = "hidfi-test-elsewhere"


def _saved(board):
    return board.cmd({"cmd": "wifi_networks"}, "wifi_networks")["networks"]


def test_the_owners_network_is_saved_and_proven(board, home):
    nets = _saved(board)
    me = [n for n in nets if n["ssid"] == home["wifi_ssid"]]
    assert me, f"the network the board is on is not in its saved list: {[n['ssid'] for n in nets]}"
    assert me[0]["proven"] and me[0]["current"], me[0]
    assert home.get("sta_known", 0) >= 1


def test_a_new_network_out_of_range_falls_back_to_one_that_works(board, home, request):
    before = [n["ssid"] for n in _saved(board)]
    assert len(before) <= 3, "a fourth and fifth network would push the owner's out - not testing on this board"
    try:
        r = board.cmd({"cmd": "wifi_configure", "ssid": FAKE, "password": "not-a-real-network-1"})
        assert r.get("reply") == "wifi_connecting", r
        assert _saved(board)[0]["ssid"] == FAKE, "a new network goes in front"
        # It is not here: the join fails, and the board goes back to the one that works.
        took, st = _wait(board, lambda s: s.get("wifi_connected") and s.get("wifi_ssid") == home["wifi_ssid"],
                         90, "falling back to the network that works")
        nets = _saved(board)
        assert nets[0]["ssid"] == home["wifi_ssid"], "the network that worked goes back to the front"
        fake = [n for n in nets if n["ssid"] == FAKE]
        assert fake and not fake[0]["proven"], "the new one stays saved, not yet proven"
        _reachable(request, st["wifi_ip"])
    finally:
        board.cmd({"cmd": "wifi_forget", "ssid": FAKE})
    assert [n["ssid"] for n in _saved(board)] == before, "the saved list was not put back"


def test_away_from_every_saved_network_it_says_so_and_keeps_its_own_wifi(board, home):
    _fault(board, kind="vanish", s=45)
    _, st = _wait(board, lambda s: s.get("sta_away"), 20, "saying no saved network is in range")
    scans = st.get("sta_scans", 0)
    assert st.get("ap_running") is not False and "in range" in (st.get("sta_error") or ""), st.get("sta_error")
    _wait(board, lambda s: s.get("wifi_connected"), 100, "rejoining when the network is back")
    assert board.status().get("sta_scans", 0) > scans, "it should keep looking while away"
