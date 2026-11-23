"""D-14: the board and the paired computer on two networks of one home, named, and the
board brought to the computer's network from the Companion.

Seen at the owner's home (2026-11-23): "ARVISANTU" is the main router (192.168.1.x),
"ARVISANTU C6" an extender with its own subnet (192.168.0.x) behind it. The Mac moves
between them by itself, so does the
board, and the Companion said "not on this computer's WiFi - set it up from the
board's own WiFi" while both were on saved home networks. Here, against the board
model (tests/support/companion_sim.py, held to the firmware by the board tier):

    the heartbeat says which network the board is on and which saved network the
      computer's own address belongs to (never a guessed mask)
    "bring" moves the board to the computer's network, signed like an update
    someone using the board right on its network is asked about first (D-10)
    a computer on a network the board has never been on is told so, nothing moves
    a forged or replayed request moves nothing
And the firmware glue: masks are recorded, saved and moved with their network.
"""
import pytest

from support import companion_sim as cs
from support import firmware, paths
from support.board_sim import BoardSim

pytestmark = pytest.mark.unit
C6 = {"ssid": "ARVISANTU C6", "subnet": "192.168.0.0", "mask": "255.255.255.0"}
ARV = {"ssid": "ARVISANTU", "subnet": "192.168.1.0", "mask": "255.255.255.0"}
BOARD_ON_ARV = "192.168.1.11"


def _net(d):
    return dict({"password": "x", "proven": True, "static": False, "refused": False, "used": 0,
                 "computer": False, "tried": False}, **d)


@pytest.fixture
def home():
    b = BoardSim(pin="4826")
    b.nets = [_net(ARV), _net(C6)]
    b.air = {"ARVISANTU": -66, "ARVISANTU C6": -64}
    b.sta.update(phase="online", ssid="ARVISANTU", ip=BOARD_ON_ARV)
    return b


def pair(board, ip):
    app = cs.FakeApp(board.comp, ip=ip)
    assert app.hello()[1]["state"] == "pending"
    assert board.handle({"cmd": "companion_pair", "id": app.id}, "ws", ip="192.168.4.2")[0]["state"] == "confirm"
    app.advance()
    assert board.handle({"cmd": "companion_pair", "id": app.id, "code": app.code}, "ws", ip="192.168.4.2")[0]["state"] == "paired"
    return app


def test_the_heartbeat_names_both_networks_when_they_differ(home):
    # The Mac on C6 reaches the board through the router: the request arrives from the
    # router's address, the Mac says its own.
    app = pair(home, ip="192.168.1.3")
    r = app.beat(via="192.168.0.248")
    assert r["status"] == "ok"
    assert {k: r["net"][k] for k in ("board", "here", "computer")} == {"board": "ARVISANTU", "here": False, "computer": "ARVISANTU C6"}
    assert r["net"]["subnets"] == ["192.168.1.0/255.255.255.0", "192.168.0.0/255.255.255.0"], "where to look if it moves"


def test_the_heartbeat_says_here_when_both_are_on_one_network(home):
    app = pair(home, ip="192.168.1.105")
    n = app.beat(via="192.168.1.105")["net"]
    assert (n["board"], n["here"], n["computer"]) == ("ARVISANTU", True, "ARVISANTU")


def test_a_computer_on_a_network_the_board_never_joined_is_unknown_not_guessed(home):
    app = pair(home, ip="192.168.1.3")
    assert app.beat(via="192.168.2.7")["net"]["computer"] == ""
    # a /23 would put 192.168.1.x on C6's 192.168.0.0: the board never guesses a mask
    home.nets = [_net(dict(C6, mask=""))]
    assert app.beat(via="192.168.0.9")["net"]["computer"] == ""


def test_a_routed_computer_carrying_its_own_address_is_not_right_on_the_network(home):
    """Traffic from the extender's subnet arrived with the Mac's own address: seen on
    09F7C8, "said == came from" held for a Mac on the other router, so it was counted on
    the board's network and "bring" asked about someone using it."""
    app = pair(home, ip="192.168.0.248")
    r = app.beat(via="192.168.0.248")
    assert (r["net"]["board"], r["net"]["here"], r["net"]["computer"]) == ("ARVISANTU", False, "ARVISANTU C6")
    assert home.status()["comp_routed"] is True
    r = app.bring("192.168.0.248")
    assert not r.get("busy") and home.sta["ssid"] == "ARVISANTU C6", "the asker itself is never the one in the way"


def test_after_a_move_nobody_counts_as_right_on_the_new_network_until_they_say_so(home):
    """A computer on-link before the board moved is not on-link after it: its old mark made
    "bring" ask about someone using the board (seen on 09F7C8)."""
    near = pair(home, ip="192.168.1.105")
    near.beat(via="192.168.1.105")
    assert home.status()["comp_routed"] is False
    far = pair(home, ip="192.168.0.248")
    r = far.bring("192.168.0.248", force=True)               # off to C6
    assert home.sta["ssid"] == "ARVISANTU C6"
    assert not any(c.get("on_link") for c in home.comp.paired)


def test_bring_moves_the_board_to_the_computers_network(home):
    app = pair(home, ip="192.168.1.3")
    r = app.bring("192.168.0.248")
    assert r["status"] == "ok" and r["ssid"] == "ARVISANTU C6" and not r.get("busy")
    assert home.sta["ssid"] == "ARVISANTU C6"
    assert next(n for n in home.nets if n["ssid"] == "ARVISANTU C6")["computer"] is True


def test_bring_asks_first_when_someone_is_using_the_board_on_its_network(home):
    app = pair(home, ip="192.168.1.3")
    home.others_on_link = 1
    r = app.bring("192.168.0.248")
    assert r["busy"] is True and r["using"] == 1 and home.sta["ssid"] == "ARVISANTU"
    r = app.bring("192.168.0.248", force=True)
    assert not r.get("busy") and home.sta["ssid"] == "ARVISANTU C6"


def test_bring_to_a_network_the_board_does_not_know_moves_nothing(home):
    app = pair(home, ip="192.168.1.3")
    r = app.bring("192.168.2.7")
    assert r["status"] == "error" and r["reply"] == "unknown_network" and "add this computer's WiFi" in r["message"]
    assert home.sta["ssid"] == "ARVISANTU"


def test_bring_when_already_together_says_so(home):
    app = pair(home, ip="192.168.1.105")
    app.beat(via="192.168.1.105")
    assert app.bring("192.168.1.105") == {"status": "ok", "ssid": "ARVISANTU", "here": True}


def test_a_forged_or_replayed_bring_moves_nothing(home):
    app = pair(home, ip="192.168.1.3")
    _, ch = app._call(op="chal", id=app.id)
    bad = app._call(op="bring", id=app.id, via="192.168.0.248", force=False, chal=ch["chal"], mac="00" * 32)[1]
    assert bad["reply"] == "bad_mac" and home.sta["ssid"] == "ARVISANTU"
    m = cs.mac(app.key, f"bring|{app.id}|192.168.0.248|0|{ch['chal']}")
    again = app._call(op="bring", id=app.id, via="192.168.0.248", force=False, chal=ch["chal"], mac=m)[1]
    assert again["reply"] == "stale_chal", "a challenge is spent by any attempt, right or wrong"
    assert home.sta["ssid"] == "ARVISANTU"


def test_the_firmware_records_each_networks_mask_and_answers_bring_signed():
    ino = firmware.ino_text()
    comp = (paths.TESTS.parent / "hid_fi" / "companion.h").read_text(encoding="utf-8")
    assert 'd["sm"] = netSmask[i]' in ino and 'netSmask[netCount] = d["sm"] | 0u' in ino, "saved and loaded with its network"
    assert ino.count("netSmask[k] = netSmask[k-1]") == 1 and ino.count("netSmask[k] = netSmask[k+1]") == 1, \
        "moved with its network when the list is reordered or one is forgotten"
    assert "netSmask[netCurrent] = sm" in ino, "recorded when the board joins"
    bring = comp[comp.index("static void compBring("):]
    bring = bring[:bring.index("\n}\n")]
    assert 'snprintf(msg, sizeof(msg), "bring|%s|%s|%d|%s"' in bring and "compChalTake(c, chal)" in bring
    assert bring.index("comp_ct_equal") < bring.index("wifiPreferCore("), "nothing moves before the proof holds"
    assert '"unknown_network"' in bring
    assert 'else if (!strcmp(op, "bring"))  compBring(in, ip);' in comp
    assert "compNetFacts(d, via[0] ? (uint32_t)said : ip, c.onLink);" in comp
    beat = comp[comp.index("static void compBeat("):]
    beat = beat[:beat.index("\n}\n")]
    core = ino[ino.index("void wifiPreferCore(int i, bool now, bool force, int askerWs, JsonDocument& d) {"):]
    core = core[:core.index("\n}\n")]
    assert core.index('netMoveTo = "";') < core.index("netMoveTo = ssid;"), \
        "a newer choice cancels a move still waiting to run, before deciding its own"
    assert "WiFi.disconnect" not in core and "netMoveTo = ssid;" in core, \
        "the answer leaves before the board moves: a reply after the move never arrived"
    loop = ino[ino.index("void loop() {"):]
    assert "netMoveTick();" in loop[:loop.index("\n}\n")]
    owner = ino[ino.index("void netOwnerHere(bool computer) {"):]
    assert "if (netMoveTo.length()) return;" in owner[:owner.index("\n}\n")], \
        "a heartbeat in the moment before an asked-for move must not anchor the network being left"
    online = ino[ino.index("void staCameOnline(const String& ip) {"):]
    assert "for (auto& c : compPaired) c.onLink = false;" in online[:online.index("\n}\n")]
    assert "c.onLink = local && " in beat and "(ip & sm) == (me & sm)" in beat, \
        "right on the board's network means inside its subnet (or on its access point), not just said == came from"
