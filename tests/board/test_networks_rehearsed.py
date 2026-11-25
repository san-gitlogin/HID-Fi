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

The owner's computer is played by the test itself: a Companion paired over USB with the
board's code (tests/support/companion_sim.FakeApp, real signed HTTP), beating from this
computer, so it counts as directly on the network only where it really is. It used to be
the owner's real Companion, never checked: when the board started on a network this
computer does not reach, or the app was not running, the scenarios failed for that
(2026-11-23). The board is first put on a saved network this computer is on. Needs
--board, an access PIN on the board and one free saved-network slot (no saved network
is ever pushed out). Prints network names and decisions only.
Every test puts back what it changed: test networks off and forgotten, home preferred.
"""
import ipaddress
import socket
import subprocess
import threading
import time

import pytest

from support import companion_sim as cs

pytestmark = [pytest.mark.board, pytest.mark.rehearsal]   # D-17: zero real joins, checked by conftest

SPEED = 15                      # 5 min -> 20 s, the 2-minute look -> 8 s
B, E = "HIDFI-SIM-B", "HIDFI-SIM-E"
PASSWORD = "rehearsal-only"     # never sent to any router: a made-up network is joined over the carrier


def _sim(board, **kw):
    # A real saved network listed here becomes a made-up one riding the carrier, with a
    # made-up subnet the board then saves for the real network (it did, 2026-11-23). Only
    # the carrier - the network the board is on - stays real when listed.
    if kw.get("nets"):
        saved, cur = _nets(board), _current(board)
        bad = [o["ssid"] for o in kw["nets"] if o["ssid"] in saved and not o["ssid"].startswith("HIDFI-SIM")
               and o["ssid"] != cur]
        assert not bad, f"net_sim would turn the saved network(s) {bad} into made-up ones: use hide= instead"
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
            if OWNER:
                OWNER.track(board)
            last = _nets(board)
            if pred(last):
                return last
        except Exception:
            pass
        time.sleep(1)
    # What the board decided, from its own log: a failure without it could not be
    # explained (2026-11-23: it went to the neighbouring extender, nobody knew why).
    raise AssertionError(f"{what} - not within {timeout}s. Networks: "
                         + ", ".join(f"{k}{[f for f in ('current', 'used', 'computer', 'preferred') if v.get(f)]}"
                                     for k, v in (last or {}).items())
                         + "\nboard log:\n  " + "\n  ".join(list(board.net_log)[-30:]))


def _on(name):
    return lambda nets: nets.get(name, {}).get("current")


def _stays(board, name, secs):
    end = time.time() + secs
    while time.time() < end:
        if OWNER:
            OWNER.track(board)
        cur = _current(board)
        assert cur in (name, None), f"moved to {cur} - it must stay on {name}"
        time.sleep(1)
    assert _current(board) == name


def _moves(board, since):
    return [e for e in board.events[since:] if e.get("event") == "wifi_moving"]


def _add(board, ssid):
    r = board.cmd({"cmd": "wifi_configure", "ssid": ssid, "password": PASSWORD}, timeout=10)
    assert r.get("status") == "ok", r


def _this_computer_on(ip):
    """This computer's own address toward ip, and whether ip is inside that address's
    subnet by the interface's real mask (macOS: route + ipconfig) - never a guessed /24."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((ip, 80))
        me = s.getsockname()[0]
    finally:
        s.close()
    try:
        ifc = next(l.split(":")[1].strip() for l in subprocess.run(["route", "-n", "get", ip], capture_output=True,
                                                                    text=True).stdout.splitlines() if "interface:" in l)
        mask = subprocess.run(["ipconfig", "getoption", ifc, "subnet_mask"], capture_output=True, text=True).stdout.strip()
        return me, ipaddress.ip_address(ip) in ipaddress.ip_network(f"{me}/{mask}", strict=False)
    except (StopIteration, ValueError):
        return me, False


class _Owner:
    """The owner's computer: a paired Companion beating every 2 s to wherever the board is."""

    def __init__(self, app):
        self.app, self.ip, self.stop = app, None, threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop.wait(2):
            ip = self.ip
            if not ip:
                continue
            try:
                self.app.board = cs.RealBoardHttp(ip)
                self.app.ip = _this_computer_on(ip)[0]
                self.app.beat(via=self.app.ip)
            except Exception:
                pass                               # not reachable from here: then it is not here

    def track(self, board):
        ip = board.status().get("wifi_ip")
        if ip:
            self.ip = ip


OWNER = None


def _pair_owner(board, ip):
    for p in board.cmd({"cmd": "companion_list"}, "companion_list").get("paired", []):
        if p.get("name") == "Rehearsal computer":                   # a leftover of this module only
            board.cmd({"cmd": "companion_unpair", "id": p["id"]}, "companion_unpair")
    app = cs.FakeApp(cs.RealBoardHttp(ip), name="Rehearsal computer")
    app.ip = _this_computer_on(ip)[0]
    assert app.hello()[1].get("state") == "pending"
    assert board.cmd({"cmd": "companion_pair", "id": app.id}, "companion_pair").get("state") == "confirm"
    assert app.advance()[1].get("state") == "code_shown"
    assert board.cmd({"cmd": "companion_pair", "id": app.id, "code": app.code}, "companion_pair").get("state") == "paired"
    return app


@pytest.fixture
def home(board):
    """The board on a network this computer is on, the owner's computer (played by the
    test) right there; put back afterwards."""
    global OWNER
    end = time.time() + 60
    while time.time() < end and not board.status().get("wifi_connected"):
        time.sleep(1)
    st = board.status()
    if not st.get("wifi_connected"):
        pytest.skip("the board is not on a home network")
    if not st.get("auth_set"):
        pytest.skip("the owner's computer is a paired Companion, which needs an access PIN on the board")
    if not _this_computer_on(st["wifi_ip"])[1]:
        for ssid, n in _nets(board).items():
            if ssid.startswith("HIDFI-SIM") or not n.get("proven"):
                continue
            board.cmd({"cmd": "wifi_prefer", "ssid": ssid, "now": True, "force": True}, "wifi_prefer")
            try:
                _wait(board, _on(ssid), 45, f"on {ssid}")
                time.sleep(3)
            except AssertionError:
                continue
            if _this_computer_on(board.status().get("wifi_ip") or "0.0.0.0")[1]:
                break
        else:
            pytest.skip("this computer is on none of the board's saved networks: the owner's computer cannot be right there")
    st = board.status()
    app = _pair_owner(board, st["wifi_ip"])
    OWNER = _Owner(app)
    OWNER.ip = st["wifi_ip"]
    OWNER.thread.start()
    end = time.time() + 30
    while time.time() < end and not (board.status().get("comp_online", 0) and not board.status().get("comp_routed")):
        time.sleep(1)
    nets = _nets(board)
    home = _current(board)
    if not home or home.startswith("HIDFI-SIM"):
        pytest.skip(f"the board is on {home!r}, not on a real home network")
    if len([n for n in nets if not n.startswith("HIDFI-SIM")]) >= 5:
        pytest.skip("all 5 saved-network slots are in use; a test network would push a saved one out")
    _sim(board, off=True)
    for n in [n for n in nets if n.startswith("HIDFI-SIM")]:
        board.cmd({"cmd": "wifi_forget", "ssid": n})
    try:
        yield home
    finally:
        OWNER.stop.set()
        board.cmd({"cmd": "companion_unpair", "id": app.id}, "companion_unpair")
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


def test_the_hotspot_goes_and_a_stronger_neighbour_does_not_keep_it_from_the_owner(board, home):
    """Home drops away for a minute and the board falls back to another saved network,
    louder than home (an extender with its own subnet decided this 2 runs in 3 at the
    owner's home, 2026-11-23). Home back, the owner there: the board must go home and stay
    there preferred, not stay on the louder neighbour (D-02, D-15).

    All rehearsed (D-17): the neighbour is made up, home's absence is staged, the radio
    never leaves the real network. One made-up network only - the owner's saved networks
    leave one free slot, and a second would push one of them out."""
    _sim(board, nets=[{"ssid": B, "rssi": -35}], owners=home, speed=SPEED)
    _add(board, B)
    _wait(board, _on(home), 90, f"home on {home}")
    r = board.cmd({"cmd": "net_fault", "kind": "vanish", "ssid": home, "s": 60}, "net_fault")
    assert r.get("status") == "ok", r
    _wait(board, _on(B), 60, f"fallen back to {B} while {home} is away")
    _wait(board, lambda n: _on(home)(n) and n[home].get("preferred"), 150,
          f"back on {home} where the owner is, preferred - not kept on the louder {B}")


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


def test_two_homes_on_one_subnet_are_two_networks(board, home):
    """D-16. A made-up network on the home's own subnet behind another router - the owner's
    hometown extender and his Bangalore router are both 192.168.0.x (2026-11-25). It is
    another network: with nobody there, the board comes home. (The extender scenario
    above, same subnet and same router, is one network: it stays.)"""
    _wait(board, lambda n: n.get(home, {}).get("router_known"), 45, f"the router of {home} learned")
    _sim(board, nets=[{"ssid": B, "rssi": -35, "other_router": True}], owners=home, speed=SPEED)
    _add(board, B)
    _wait(board, _on(B), 40, f"on {B}")
    since = len(board.events)
    _wait(board, _on(home), 90, f"home again on {home} - {B} is another network (another router), nobody there")
    assert any(m.get("why") == "coming_home" for m in _moves(board, since)), _moves(board, since)
