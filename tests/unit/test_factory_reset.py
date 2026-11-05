"""Factory reset (docs/FACTORY_RESET.md), against the board model: who may ask, what
proves it, what is erased and what stays. The firmware's own run of the same rules is
tests/board/test_factory_reset_hw.py."""
import time

import pytest

from support.board_sim import BoardSim

pytestmark = pytest.mark.unit
from support import companion_sim as cs


def ws(board, **b):
    """A command from a signed-in dashboard (test_companion.py's helper)."""
    return board.handle(b, "ws", ip="192.168.1.30")[0]


def pair(board, app=None):
    """A computer paired through the dashboard (test_companion.py's helper)."""
    app = app or cs.FakeApp(board.comp)
    assert app.hello()[1]["state"] == "pending"
    assert ws(board, cmd="companion_pair", id=app.id)["state"] == "confirm"
    code, r = app.advance()
    assert r["state"] == "code_shown"
    assert ws(board, cmd="companion_pair", id=app.id, code=app.code)["state"] == "paired"
    return app


@pytest.fixture
def board():
    b = BoardSim(pin="4826")
    b.vault = {0: {"label": "Work PC", "type": "password", "secret": "s"}}
    b.macros = {0: {"name": "m", "steps": ["type hi"]}}
    b.nets = [{"ssid": "HomeNet", "password": "p", "proven": True, "static": False, "refused": False}]
    b.ap_ssid, b.ap_pass = "My HID-Fi", "chosen-by-me"            # a person renamed it
    b.ota["slot"], b.ota["from"], b.ota["auto"] = "app1", "hid_fi_v4.2.1", False
    return b


def serial(board, **b):
    return board.handle(b, "serial")[0]


def link(board, **b):
    return board.handle(b, "serial", link=True)[0]


def settle(board, s=0.6):
    time.sleep(s)
    board.tick()


def erased_and_kept(board, own_ssid, own_pw):
    assert board.pin is None and board.vault == {} and board.macros == {} and board.nets == []
    assert board.comp.paired == []
    assert (board.ap_ssid, board.ap_pass, board.ap_open) == (own_ssid, own_pw, False)   # the board's own
    assert board.ota["slot"] == "app1" and board.ota["from"] == "hid_fi_v4.2.1"            # the journal stays
    assert board.ota["auto"] is True                                                      # a choice: back to default
    assert board.status()["fresh"] is True


def test_over_usb_it_needs_confirm_and_erases_everything_a_person_set_up(board):
    own = (board.ap_ssid_own, board.ap_genpw)
    pair(board)
    assert board.status()["fresh"] is False
    r = serial(board, cmd="factory_reset")
    assert r["reply"] == "confirm_needed" and board.vault                 # nothing happened
    boots = board.boots
    r = serial(board, cmd="factory_reset", confirm=True)
    assert r == {"status": "ok", "reply": "factory_reset", "restart": True}
    settle(board)
    erased_and_kept(board, *own)
    assert board.boots == boots + 1


@pytest.mark.parametrize("state,step,why", [("pending_verify", "idle", "verifying"),
                                            ("valid", "downloading", "busy"), ("valid", "ready", "busy")])
def test_never_while_an_update_needs_the_board(board, state, step, why):
    board.ota["state"], board.ota["step"] = state, step
    assert serial(board, cmd="factory_reset", confirm=True)["reply"] == why
    assert board.vault and board.wiped == 0


def test_from_a_signed_in_dashboard_it_needs_confirm_too(board):
    assert ws(board, cmd="factory_reset")["reply"] == "confirm_needed"
    assert ws(board, cmd="factory_reset", confirm=True)["reply"] == "factory_reset"
    settle(board)
    assert board.wiped == 1


def test_a_paired_computer_opens_the_cable_and_erases_down_it(board):
    own = (board.ap_ssid_own, board.ap_genpw)
    app = pair(board)
    r = app.reset()
    assert r["status"] == "ok" and len(r["token"]) == 32 and board.wiped == 0     # nothing erased yet
    settle(board, 0.4)
    st = board.status()
    assert st["recovery"] is True and st["recovery_kind"] == "reset" and st["recovery_left_s"] <= 60
    # That link erases only: never the WiFi password, a PIN reset or anything else.
    for cmd in ({"cmd": "ap_password"}, {"cmd": "set_auth", "token": "1111"}, {"cmd": "vault_list"}, {"cmd": "type", "text": "x"}):
        assert link(board, **cmd)["reply"] == "not_allowed", cmd
    assert link(board, cmd="factory_reset", proof=app.reset_proof(r["token"]))["reply"] == "factory_reset"
    settle(board)
    erased_and_kept(board, *own)


def test_the_token_is_one_try_and_bound_to_the_computer(board):
    app, other = pair(board), pair(board, cs.FakeApp(board.comp, name="Other PC", ip="192.168.1.21"))
    tok = app.reset()["token"]
    settle(board, 0.4)
    assert link(board, cmd="factory_reset", proof=other.reset_proof(tok))["reply"] == "bad_proof"   # another key
    assert link(board, cmd="factory_reset", proof=app.reset_proof(tok))["reply"] == "bad_proof"     # used up
    assert board.wiped == 0 and board.vault


def test_the_com_cable_is_not_limited_on_a_reset_boot(board):
    """Only the link a paired computer opened is limited; the COM port is physical."""
    app = pair(board)
    app.reset()
    settle(board, 0.4)
    assert serial(board, cmd="ap_password")["reply"] == "ap_password"


def test_a_reset_request_needs_proof(board):
    app = pair(board)
    _, r = app._call(op="chal", id=app.id)
    bad = cs.mac(app.key, f"reset|{app.id}|{'0' * 32}")
    assert app._call(op="reset", id=app.id, chal=r["chal"], mac=bad)[1]["reply"] == "bad_mac"
    stranger = cs.FakeApp(board.comp, name="Stranger")
    stranger.key = app.key
    assert stranger.reset()["reply"] == "unknown_pc"
    assert board.reset_boot is False and board.wiped == 0


def test_a_computer_that_is_not_paired_asks_and_the_owner_approves_with_the_pin(board):
    laptop = cs.FakeApp(board.comp, name="LAPTOP-X", ip="192.168.1.40")
    r = laptop.reset_request()
    rid = r["request"]
    assert board.status()["reset_request"]["name"] == "LAPTOP-X"
    assert laptop.reset_request_status(rid)["state"] == "pending"
    # A second computer cannot pile on while one waits.
    assert cs.FakeApp(board.comp, name="X2", ip="192.168.1.41").reset_request()["reply"] == "busy"
    r = ws(board, cmd="factory_reset", confirm=True, request=rid)
    assert r["reply"] == "factory_reset"
    time.sleep(1.2)                                                        # a poll a second later
    assert laptop.reset_request_status(rid)["state"] == "approved"         # still there: 2.5 s
    settle(board, 1.6)
    assert board.wiped == 1 and board.status()["fresh"] is True


def test_the_owner_can_decline_and_a_request_expires(board):
    laptop = cs.FakeApp(board.comp, name="LAPTOP-X", ip="192.168.1.40")
    rid = laptop.reset_request()["request"]
    assert ws(board, cmd="factory_reset_decline", request="0" * 32)["reply"] == "no_request"
    assert ws(board, cmd="factory_reset_decline", request=rid)["reply"] == "factory_reset_decline"
    assert laptop.reset_request_status(rid)["state"] == "declined" and board.wiped == 0
    rid = laptop.reset_request()["request"]
    board.advance(121)
    assert laptop.reset_request_status(rid)["state"] == "unknown" and "reset_request" not in board.status()


def test_a_board_without_a_pin_takes_no_companion_request():
    b = BoardSim(pin=None)
    code, r = b.comp.http('{"op":"reset_request","name":"LAPTOP"}', "192.168.4.2")
    assert code == 403 and r["reply"] == "pin_required"
