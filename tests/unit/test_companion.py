"""Screen sharing: the pairing protocol and the board's rules, against the model.

The model (tests/support/companion_sim.py) is held to the firmware by
tests/board/test_sim_contract.py, and its crypto to the shared vectors here, so
these pin the behaviour companion/DESIGN.md specifies.
"""
import json
import pathlib

import pytest

from support.board_sim import BoardSim
from support import companion_sim as cs

VECTORS = json.loads((pathlib.Path(__file__).resolve().parents[2] / "companion" / "vectors" / "pairing_v1.json").read_text())


# ---- the crypto agrees with the Rust app and the firmware -------------------------------
def test_the_model_reproduces_the_shared_vectors():
    v, x = VECTORS, bytes.fromhex
    a_pub, b_pub = cs.x25519(x(v["a_secret"])), cs.x25519(x(v["b_secret"]))
    assert a_pub.hex() == v["a_pub"] and b_pub.hex() == v["b_pub"]
    assert cs.commit(a_pub, x(v["n_a"])).hex() == v["commit"]
    assert "%06d" % cs.sas(a_pub, b_pub, x(v["n_a"]), x(v["n_b"])) == v["code"]
    shared = cs.x25519(x(v["a_secret"]), b_pub)
    assert shared == cs.x25519(x(v["b_secret"]), a_pub)
    key = cs.derive_key(shared, a_pub, b_pub, x(v["n_a"]), x(v["n_b"]))
    assert key.hex() == v["key"]
    assert cs.mac(key, v["beat"]["msg"]) == v["beat"]["mac"]
    assert cs.mac(key, f'tkt|{v["ticket"]["tn"]}|{v["ticket"]["vn"]}') == v["ticket"]["mac"]


def test_rfc7748_vector():
    # RFC 7748 section 5.2, first test vector.
    k = bytes.fromhex("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4")
    u = bytes.fromhex("e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c")
    assert cs.x25519(k, u).hex() == "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"


# ---- the flow -----------------------------------------------------------------------------
@pytest.fixture
def board():
    return BoardSim(pin="4826")


def ws(board, **b):
    """A command from a signed-in dashboard."""
    return board.handle(b, "ws", ip="192.168.1.30")[0]


def pair(board, app=None):
    app = app or cs.FakeApp(board.comp)
    assert app.hello()[1]["state"] == "pending"
    assert ws(board, cmd="companion_pair", id=app.id)["state"] == "confirm"
    code, r = app.advance()
    assert r["state"] == "code_shown"
    r = ws(board, cmd="companion_pair", id=app.id, code=app.code)
    assert r == {"status": "ok", "reply": "companion_pair", "state": "paired", "name": app.name}
    return app


def test_a_computer_pairs_comes_online_and_gets_a_ticket(board):
    app = pair(board)
    assert board.status()["comp_paired"] == 1 and board.status()["comp_online"] == 0
    assert app.beat() == {"status": "ok"}
    assert board.status()["comp_online"] == 1
    t = ws(board, cmd="screen_ticket", id=app.id)
    assert t["reply"] == "screen_ticket" and t["ip"] == app.ip and t["port"] == app.port and t["fp"] == app.fp
    # The app can check the ticket with nothing but its key.
    assert cs.mac(app.key, f'tkt|{app.tn}|{t["vn"]}') == t["ticket"]


def test_the_list_never_carries_the_key(board):
    app = pair(board)
    app.beat()
    text = json.dumps(ws(board, cmd="companion_list"))
    assert app.key.hex() not in text and "key" not in json.loads(text)["paired"][0]


def test_nothing_works_without_an_access_pin():
    board = BoardSim(pin="")
    app = cs.FakeApp(board.comp)
    code, r = app.hello()
    assert (code, r["reply"]) == (403, "pin_required")
    assert ws(board, cmd="companion_pair", id=app.id)["reply"] == "pin_required"
    assert ws(board, cmd="screen_ticket", id=app.id)["reply"] == "pin_required"


def test_a_ticket_is_never_given_over_serial(board):
    app = pair(board)
    app.beat()
    r = board.handle({"cmd": "screen_ticket", "id": app.id}, "serial")[0]
    assert r["reply"] == "wifi_only"


def test_no_ticket_for_a_computer_that_is_not_running(board):
    app = pair(board)
    assert ws(board, cmd="screen_ticket", id=app.id)["reply"] == "pc_offline"
    app.beat()
    board.advance(cs.ONLINE_S + 1)
    assert ws(board, cmd="screen_ticket", id=app.id)["reply"] == "pc_offline"


def test_three_wrong_codes_end_the_attempt(board):
    app = cs.FakeApp(board.comp)
    app.hello()
    ws(board, cmd="companion_pair", id=app.id)
    app.advance()
    wrong = "%06d" % ((int(app.code) + 1) % 1_000_000)
    assert ws(board, cmd="companion_pair", id=app.id, code=wrong)["tries_left"] == 2
    assert ws(board, cmd="companion_pair", id=app.id, code="12a456")["tries_left"] == 1
    assert ws(board, cmd="companion_pair", id=app.id, code=wrong)["reply"] == "aborted"
    # The right code no longer helps, and the app is told.
    assert ws(board, cmd="companion_pair", id=app.id, code=app.code)["reply"] == "not_ready"
    assert app.hello()[1]["state"] == "aborted"
    assert board.status()["comp_paired"] == 0


def test_a_code_cannot_be_typed_before_the_computer_shows_it(board):
    app = cs.FakeApp(board.comp)
    app.hello()
    assert ws(board, cmd="companion_pair", id=app.id, code="123456")["reply"] == "not_ready"
    ws(board, cmd="companion_pair", id=app.id)
    assert ws(board, cmd="companion_pair", id=app.id)["reply"] == "not_ready"


def test_a_reveal_that_breaks_the_commitment_kills_the_attempt(board):
    app = cs.FakeApp(board.comp)
    app.hello()
    ws(board, cmd="companion_pair", id=app.id)
    code, r = app._call(op="reveal", id=app.id, n_a="00" * 16)
    assert (code, r["reply"]) == (400, "bad_mac")
    assert app._call(op="state", id=app.id)[1]["state"] == "none"


def test_a_heartbeat_cannot_be_replayed(board):
    app = pair(board)
    _, r = app._call(op="chal", id=app.id)
    m = cs.mac(app.key, f"beat|{app.id}|{app.port}|{app.fp}|{'1c' * 16}|{r['chal']}")
    beat = dict(op="beat", id=app.id, port=app.port, fp=app.fp, tn="1c" * 16, chal=r["chal"], mac=m)
    assert app._call(**beat)[1] == {"status": "ok"}
    assert app._call(**beat)[1]["reply"] == "stale_chal"


def test_a_wrong_proof_burns_the_challenge(board):
    app = pair(board)
    _, r = app._call(op="chal", id=app.id)
    beat = dict(op="beat", id=app.id, port=app.port, fp=app.fp, tn="1c" * 16, chal=r["chal"], mac="00" * 32)
    assert app._call(**beat)[1]["reply"] == "bad_mac"
    beat["mac"] = cs.mac(app.key, f"beat|{app.id}|{app.port}|{app.fp}|{'1c' * 16}|{r['chal']}")
    assert app._call(**beat)[1]["reply"] == "stale_chal"


def test_a_challenge_expires(board):
    app = pair(board)
    _, r = app._call(op="chal", id=app.id)
    board.advance(cs.CHAL_S + 1)
    m = cs.mac(app.key, f"beat|{app.id}|{app.port}|{app.fp}|{'1c' * 16}|{r['chal']}")
    assert app._call(op="beat", id=app.id, port=app.port, fp=app.fp, tn="1c" * 16, chal=r["chal"], mac=m)[1]["reply"] == "stale_chal"


def test_the_address_is_where_the_request_came_from(board):
    app = pair(board)
    app.ip = "192.168.1.99"
    app.beat()
    assert ws(board, cmd="screen_ticket", id=app.id)["ip"] == "192.168.1.99"


def test_a_blind_pin_reset_removes_every_pairing(board):
    app = pair(board)
    r = board.handle({"cmd": "set_auth", "token": "9999"}, "serial")[0]
    assert r["pairings_wiped"] is True
    assert board.status()["comp_paired"] == 0
    assert app.beat()["reply"] == "unknown_pc"


def test_changing_the_pin_with_the_old_one_keeps_them(board):
    pair(board)
    r = board.handle({"cmd": "set_auth", "token": "9999", "old": "4826"}, "serial")[0]
    assert r["pairings_wiped"] is False and board.status()["comp_paired"] == 1


def test_unpairing(board):
    app = pair(board)
    assert ws(board, cmd="companion_unpair", id=app.id) == {"status": "ok", "reply": "companion_unpair", "removed": True}
    assert ws(board, cmd="companion_unpair", id=app.id)["removed"] is False
    assert app.beat()["reply"] == "unknown_pc"


def test_at_most_four_paired_computers(board):
    apps = []
    for i in range(cs.PAIRED_MAX):
        apps.append(pair(board, cs.FakeApp(board.comp, ip=f"192.168.1.{40 + i}")))
    extra = cs.FakeApp(board.comp, ip="192.168.1.60")
    extra.hello()
    ws(board, cmd="companion_pair", id=extra.id)
    extra.advance()
    assert ws(board, cmd="companion_pair", id=extra.id, code=extra.code)["reply"] == "full"
    # Re-pairing one that is already there replaces it rather than needing a slot.
    again = cs.FakeApp(board.comp, ip=apps[0].ip)
    again.id = apps[0].id
    pair(board, again)
    assert board.status()["comp_paired"] == cs.PAIRED_MAX


def test_one_pending_request_per_address_and_four_in_all(board):
    a = cs.FakeApp(board.comp, ip="192.168.1.70")
    b = cs.FakeApp(board.comp, ip="192.168.1.70")
    a.hello(); b.hello()
    assert [p["id"] for p in ws(board, cmd="companion_list")["pending"]] == [b.id]
    for i in range(3):
        cs.FakeApp(board.comp, ip=f"192.168.1.{80 + i}").hello()
    code, r = cs.FakeApp(board.comp, ip="192.168.1.90").hello()
    assert (code, r["reply"]) == (503, "full")


def test_a_pending_request_expires(board):
    app = cs.FakeApp(board.comp)
    app.hello()
    board.advance(cs.PENDING_S + 1)
    assert board.status()["comp_pending"] == 0
    assert ws(board, cmd="companion_pair", id=app.id)["reply"] == "not_found"


def test_rate_limit_per_address(board):
    app = cs.FakeApp(board.comp)
    for _ in range(cs.RATE_MAX):
        assert app._call(op="state", id=app.id)[0] == 200
    code, r = app._call(op="state", id=app.id)
    assert (code, r["reply"], r["retry_ms"]) == (429, "rate_limited", 5000)
    other = cs.FakeApp(board.comp, ip="192.168.1.21")
    assert other._call(op="state", id=other.id)[0] == 200
    board.advance(cs.RATE_WINDOW_S + 1)
    assert app._call(op="state", id=app.id)[0] == 200


@pytest.mark.parametrize("body", ["", "{", "[]", "null", '"x"', json.dumps({"op": "nope"}),
                                  json.dumps({"op": "state", "id": "AB" * 16}),
                                  json.dumps({"op": "hello", "v": 2}),
                                  json.dumps({"op": "beat", "id": "ab" * 16, "port": True}),
                                  "x" * (cs.BODY_MAX + 1)])
def test_malformed_requests_are_refused_not_crashed(board, body):
    code, r = board.comp.http(body, "192.168.1.20")
    assert code >= 400 and r["status"] == "error"


def test_names_lose_control_characters(board):
    app = cs.FakeApp(board.comp, name="Desk\x1b[2Jtop\x7f")
    app.hello()
    assert ws(board, cmd="companion_list")["pending"][0]["name"] == "Desk [2Jtop "


def test_a_long_name_is_refused(board):
    app = cs.FakeApp(board.comp, name="x" * 33)
    assert app.hello()[1]["reply"] == "bad_request"


def test_commands_check_the_id(board):
    for c in ("companion_pair", "companion_unpair", "screen_ticket"):
        assert ws(board, cmd=c, id="nothex")["reply"] == "bad_request"
