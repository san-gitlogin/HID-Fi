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

pytestmark = pytest.mark.unit

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


def test_a_waiting_request_stays_while_its_computer_keeps_asking(board):
    # Seen live: plugged in at 20:16, request gone at 20:18, dashboard opened at
    # 20:32 - the computer never showed. The app polls; that keeps it.
    app = cs.FakeApp(board.comp)
    app.hello()
    for _ in range(4):
        board.advance(cs.PENDING_S - 10)
        assert app._call(op="state", id=app.id)[1]["state"] == "pending"
    assert [p["id"] for p in ws(board, cmd="companion_list")["pending"]] == [app.id]
    # Another address polling with its id does not keep it.
    other = cs.FakeApp(board.comp, ip="192.168.1.99")
    board.advance(cs.PENDING_S - 10)
    other._call(op="state", id=app.id)
    board.advance(20)
    assert board.status()["comp_pending"] == 0


def test_not_now_stays_gone_while_the_computer_polls(board):
    app = cs.FakeApp(board.comp)
    app.hello()
    ws(board, cmd="companion_unpair", id=app.id)       # the dashboard's "Not now"
    assert app._call(op="state", id=app.id)[1]["state"] == "none"
    assert board.status()["comp_pending"] == 0


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


# ---- updates through the paired Companion (hid_fi/ota.h compOta) ------------------------------
def test_the_update_proof_matches_the_shared_vector():
    v = VECTORS
    o = v["ota"]
    assert f'ota|{o["id"]}|{o["act"]}|{o["url"]}|{o["chal"]}' == o["msg"]
    assert cs.mac(bytes.fromhex(v["key"]), o["msg"]) == o["mac"]


def stage_release(board, version="4.1.0"):
    board.firmware = "hid_fi_v4.0.4"
    board.ota["release"] = {"version": version, "size": 1500000, "changelog": []}


def test_the_companion_checks_installs_cancels_and_applies(board):
    app = pair(board)
    stage_release(board)
    r = app.ota("check")
    assert r == {"status": "ok", "act": "check", "step": "checking"}
    board._ota_checked()
    assert board.status()["ota_available"] == "4.1.0"
    assert app.ota("apply")["reply"] == "not_ready"
    assert app.ota("install")["step"] == "downloading"
    assert app.ota("cancel")["done"] == "stopping"
    assert board.status()["ota_error"] == "cancelled"


def test_nothing_to_install_until_a_check_found_it(board):
    app = pair(board)
    assert app.ota("install")["reply"] == "nothing_to_install"


def test_a_relay_must_be_the_requesting_computer(board):
    app = pair(board)
    stage_release(board)
    assert app.ota("from", "http://192.168.1.250:8000/rel/")["reply"] == "bad_request"
    assert not board.ota.get("relay")
    r = app.ota("from", f"http://{app.ip}:47801/rel")
    assert r["step"] == "checking" and board.ota["relay"] == f"http://{app.ip}:47801/rel/"


def test_download_mode_only_when_nothing_is_in_progress(board):
    app = pair(board)
    board.ota["state"] = "pending_verify"
    assert app.ota("download_mode")["reply"] == "verifying", "a reset now would roll the update back"
    board.ota["state"], board.ota["step"] = "valid", "downloading"
    assert app.ota("download_mode")["reply"] == "busy"
    board.ota["step"] = "idle"
    assert app.ota("download_mode") == {"status": "ok", "act": "download_mode", "step": "idle"}
    assert board.ota["download_mode"]


def test_an_update_request_cannot_be_replayed_or_forged(board):
    app = pair(board)
    _, r = app._call(op="chal", id=app.id)
    m = cs.mac(app.key, f"ota|{app.id}|check||{r['chal']}")
    req = dict(op="ota", id=app.id, act="check", url="", chal=r["chal"], mac=m)
    assert app._call(**req)[1]["status"] == "ok"
    assert app._call(**req)[1]["reply"] == "stale_chal"
    _, r = app._call(op="chal", id=app.id)
    forged = dict(req, chal=r["chal"], act="install")        # a proof for "check" used for "install"
    forged["mac"] = cs.mac(app.key, f"ota|{app.id}|check||{r['chal']}")
    assert app._call(**forged)[1]["reply"] == "bad_mac"


def test_update_requests_from_strangers_or_without_a_pin_are_refused(board):
    stranger = cs.FakeApp(board.comp)
    assert stranger.ota("check")["reply"] == "unknown_pc"
    no_pin = BoardSim(pin="")
    assert cs.FakeApp(no_pin.comp).ota("check")["reply"] == "pin_required"


@pytest.mark.parametrize("act, url", [("reboot", ""), ("check", "x" * 161)])
def test_unknown_acts_and_long_urls_are_refused(board, act, url):
    app = pair(board)
    r = app.ota(act, url)
    assert r["status"] == "error" and r["reply"] == "bad_request"


def test_a_heartbeat_and_an_update_can_overlap(board):
    """Found end to end: one Companion asks for challenges from its heartbeat and
    from an update at the same time; a single slot made the first one "expired"."""
    app = pair(board)
    _, r1 = app._call(op="chal", id=app.id)
    _, r2 = app._call(op="chal", id=app.id)
    m2 = cs.mac(app.key, f"ota|{app.id}|check||{r2['chal']}")
    assert app._call(op="ota", id=app.id, act="check", url="", chal=r2["chal"], mac=m2)[1]["status"] == "ok"
    m1 = cs.mac(app.key, f"beat|{app.id}|{app.port}|{app.fp}|{'1c' * 16}|{r1['chal']}")
    assert app._call(op="beat", id=app.id, port=app.port, fp=app.fp, tn="1c" * 16, chal=r1["chal"], mac=m1)[1] == {"status": "ok"}


def test_a_fifth_challenge_replaces_the_oldest(board):
    app = pair(board)
    chals = [app._call(op="chal", id=app.id)[1]["chal"] for _ in range(cs.CHALS + 1)]
    beat = lambda ch: app._call(op="beat", id=app.id, port=app.port, fp=app.fp, tn="1c" * 16, chal=ch,
                                mac=cs.mac(app.key, f"beat|{app.id}|{app.port}|{app.fp}|{'1c' * 16}|{ch}"))[1]
    assert beat(chals[0])["reply"] == "stale_chal", "the oldest went"
    for ch in chals[1:]:
        assert beat(ch) == {"status": "ok"}
        assert beat(ch)["reply"] == "stale_chal", "each one once"


def test_a_wrong_challenge_burns_none_of_the_live_ones(board):
    app = pair(board)
    ch = app._call(op="chal", id=app.id)[1]["chal"]
    bad = dict(op="beat", id=app.id, port=app.port, fp=app.fp, tn="1c" * 16, chal="ab" * 16, mac="00" * 32)
    assert app._call(**bad)[1]["reply"] == "stale_chal"
    m = cs.mac(app.key, f"beat|{app.id}|{app.port}|{app.fp}|{'1c' * 16}|{ch}")
    assert app._call(op="beat", id=app.id, port=app.port, fp=app.fp, tn="1c" * 16, chal=ch, mac=m)[1] == {"status": "ok"}
