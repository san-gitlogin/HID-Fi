"""Over the air, on a real board, from a release server on this computer.

The board runs a test build that trusts the TEST key of tests/vectors/ota_v1.json
(-DHIDFI_TEST_OTA_KEY - a release never contains it). A server here hands out a
manifest signed with that key for "4.0.99" and the image of a test build that
claims to be 4.0.99. ota_source (USB only) points the board at it. Then:

    a check finds it, with every CHANGELOG section newer than what runs
    a signature from another key is refused and nothing is shown
    bytes that are not the signed ones are refused - nothing switched
    cancel while downloading changes nothing; cancel when ready switches back
    a server that ignores Range makes the board start over, and it still works
    a board too old for the release is told so
    end to end: the connection drops at 40%, the download resumes with Range,
    the board restarts when idle, proves the new version and keeps it - while the
    WebSocket hot path (the dashboard's ping) stays responsive during the download

Afterwards the board gets its normal firmware back over the cable and the
source is reset. The person's data is compared by hash before and after.
--board; slow (builds three images once, several minutes on the board).
"""
import base64
import datetime
import json
import os
import struct
import sys
import time

import pytest

from support import firmware, paths
from support.ota_board import SLOT_OFFSET, data_hashes, esptool, install, wait_status
from support.release_server import ReleaseServer

sys.path.insert(0, str(paths.TESTS.parent / "tools"))
import ota_release as R  # noqa: E402

pytestmark = [pytest.mark.board, pytest.mark.slow]

VEC = json.loads((paths.TESTS / "vectors" / "ota_v1.json").read_text(encoding="utf-8"))
CHANGELOG = """## v4.0.99 - 2026-11-01

Test release.

## v4.0.5 - 2026-10-30

A section between.

## v4.0.4 - 2026-10-27

What runs now.
"""


def _key():
    from cryptography.hazmat.primitives import serialization
    return serialization.load_pem_private_key(VEC["test_private_key_pem"].encode(), password=None)


def _untrusted_sig(m):
    from cryptography.hazmat.primitives.asymmetric import ec
    return R.sign(m, ec.generate_private_key(ec.SECP256R1()))


def release(image, min_from="4.0.3", table=None):
    m = R.canonical(R.build_manifest("4.0.99", image, min_from, "3.3.12", "5.5.5", CHANGELOG,
                                     released=datetime.date(2026, 11, 1), image_name="hid_fi-4.0.99.bin",
                                     table_bytes=table, _tests_allow_test_build=True))
    return m, json.dumps(R.sign(m, _key())).encode()


@pytest.fixture(scope="module")
def env(_board_holder):
    try:
        base = firmware.build_variant("otakey", ["HIDFI_TEST_OTA_KEY"])
        new = firmware.build_variant("otakey99", ["HIDFI_TEST_OTA_KEY", "HIDFI_TEST_PATCH=99"])
        normal = firmware.build_variant("good")
    except RuntimeError as e:
        pytest.skip(f"could not build the test images: {e}")
    board = _board_holder.get()
    st = board.status()
    if st.get("ota_layout") != "two_slot":
        pytest.skip("the board is on the single-slot layout - flash it once over the cable")
    if not st.get("wifi_connected"):
        wait_status(board, lambda s: s.get("wifi_connected"), 40, "the board joining its home network")
    forget_test_pairings(board)          # a run that died mid-way leaves its Companion paired
    before = data_hashes(board)
    _settle(board)                           # a ready update from an interrupted run would block the install
    board, *_ = install(_board_holder, base)
    wait_status(board, lambda s: s.get("ota_state") == "valid", 150, "the test build proving itself")
    img = new.read_bytes()
    table = (new.parent / "hid_fi.ino.partitions.bin").read_bytes()
    man, sig = release(img, table=table)
    files = {"hidfi-ota.json": man, "hidfi-ota.sig": sig, "hid_fi-4.0.99.bin": img, "partitions.bin": table}
    with ReleaseServer(files) as srv:
        ip = wait_status(board, lambda s: s.get("wifi_connected"), 60, "the board back on its network")["wifi_ip"]
        # The board's own daily check would race the tests' checks (it did: "busy").
        board.cmd({"cmd": "ota_settings", "auto": False}, "ota_settings")
        wait_status(board, lambda s: s.get("ota_step") == "idle", 90, "any check already running finishing")
        r = board.cmd({"cmd": "ota_source", "url": srv.url_for(ip)}, "ota_source")
        assert r["status"] == "ok", r
        yield {"board": board, "srv": srv, "files": files, "img": img, "holder": _board_holder, "base": base}
    board = _board_holder.get()
    _settle(board)
    board.cmd({"cmd": "ota_source", "url": ""}, "ota_source")
    board.cmd({"cmd": "ota_settings", "auto": True}, "ota_settings")
    board, *_ = install(_board_holder, normal)
    wait_status(board, lambda s: s.get("ota_state") == "valid", 150, "the normal firmware proving itself")
    assert data_hashes(board) == before, "the person's data changed during the update tests"


TEST_PCS = ("OTA E2E PC", "OTA test")


def forget_test_pairings(board):
    """Only computers these tests paired, by the names they use - never anyone else's."""
    for p in board.cmd({"cmd": "companion_list"}, "companion_list").get("paired", []):
        if p.get("name") in TEST_PCS:
            board.cmd({"cmd": "companion_unpair", "id": p["id"]}, "companion_unpair")


def check(board):
    r = board.cmd({"cmd": "ota_check"}, "ota_check")
    assert r["status"] == "ok", f"the check was refused: {r}"
    time.sleep(1)
    return wait_status(board, lambda s: s.get("ota_step") == "idle", 90, "the check finishing")


def fresh(env):
    """Back to the plain state between scenarios: right files, no faults."""
    srv = env["srv"]
    srv.files.update({k: v for k, v in env["files"].items()})
    srv.faults.update({"cut_at": None, "ignore_range": False, "chunk_delay": 0.0, "swap": None})
    board = env["holder"].get()
    _settle(board)
    return board


def on_base(env):
    """The board on the base test build, whatever an earlier scenario left (the end
    to end one keeps 4.0.99 - after it, nothing is newer). Order-independent."""
    board = fresh(env)
    if board.status()["firmware"] != "hid_fi_v4.0.4-test":
        install(env["holder"], env["base"])
        board = env["holder"].get()
        wait_status(board, lambda s: s.get("ota_state") == "valid", 150, "the base build proving itself")
        board.cmd({"cmd": "ota_settings", "auto": False}, "ota_settings")
        wait_status(board, lambda s: s.get("wifi_connected") and s.get("ota_step") == "idle", 90,
                    "the base build on its network, idle")
    return board


def _settle(board):
    """Whatever an earlier scenario left running or ready is cancelled first - one
    failure must not hold every scenario after it (it did)."""
    st = board.status()
    if st.get("ota_step") not in ("idle", None):
        board.cmd({"cmd": "ota_cancel"}, "ota_cancel")
    wait_status(board, lambda s: s.get("ota_state") == "valid" and s.get("ota_step") in ("idle", None), 150,
                "the board settled before the next scenario")


def test_a_check_finds_the_signed_release(env):
    board = fresh(env)
    st = check(board)
    assert st["ota_available"] == "4.0.99" and st["ota_error"] == "", st
    rel = board.cmd({"cmd": "ota_status"}, "ota_status")["release"]
    assert rel["installable"] and rel["size"] == len(env["img"])
    assert [s["version"] for s in rel["changelog"]] == ["4.0.99", "4.0.5"], \
        "every section newer than what runs - not only the newest, not the one that runs"


def test_a_signature_from_another_key_shows_nothing(env):
    board = fresh(env)
    check(board)                                     # a good check first: something is "available"
    env["srv"].files["hidfi-ota.sig"] = json.dumps(_untrusted_sig(env["files"]["hidfi-ota.json"])).encode()
    st = check(board)
    assert st["ota_error"] == "bad_signature" and st["ota_available"] == "", \
        "a failed check must forget what an earlier one found"
    assert "release" not in board.cmd({"cmd": "ota_status"}, "ota_status")
    r = board.cmd({"cmd": "ota_install"}, "ota_install")
    assert r["status"] == "error" and r["message"] == "nothing_to_install"


def test_bytes_that_are_not_the_signed_ones_are_refused(env):
    board = fresh(env)
    check(board)
    slot = board.status()["ota_slot"]
    bad = bytearray(env["img"]); bad[len(bad) // 2] ^= 0xFF
    env["srv"].faults["swap"] = bytes(bad)
    board.cmd({"cmd": "ota_install"}, "ota_install")
    st = wait_status(board, lambda s: s.get("ota_step") == "idle" and s.get("ota_error"), 240, "the install stopping")
    assert st["ota_error"] == "hash" and st["ota_slot"] == slot and st["ota_state"] == "valid"


def test_cancel_while_downloading_changes_nothing(env):
    board = fresh(env)
    check(board)
    env["srv"].faults["chunk_delay"] = 0.05
    board.cmd({"cmd": "ota_install"}, "ota_install")
    wait_status(board, lambda s: s.get("ota_bytes", 0) > 50000, 120, "the download starting")
    assert board.cmd({"cmd": "ota_cancel"}, "ota_cancel")["done"] == "stopping"
    st = wait_status(board, lambda s: s.get("ota_step") == "idle", 60, "the download stopping")
    assert st["ota_error"] == "cancelled" and st["ota_state"] == "valid"


def test_cancel_right_after_install_withdraws_it(env):
    """The install is queued for the worker; a cancel in that moment must withdraw
    it - it once answered "nothing" and the install went on to "ready"."""
    board = fresh(env)
    check(board)
    slot = board.status()["ota_slot"]
    board.cmd({"cmd": "ota_install"}, "ota_install")
    assert board.cmd({"cmd": "ota_cancel"}, "ota_cancel")["done"] == "stopping"
    st = wait_status(board, lambda s: s.get("ota_step") == "idle", 60, "the cancelled install stopping")
    time.sleep(3)
    st = board.status()
    assert st["ota_step"] == "idle" and st["ota_error"] == "cancelled" and st["ota_slot"] == slot


def test_cancel_when_ready_switches_back(env):
    board = fresh(env)
    check(board)
    slot, fw = board.status()["ota_slot"], board.status()["firmware"]
    board.cmd({"cmd": "ota_install"}, "ota_install")
    wait_status(board, lambda s: s.get("ota_step") == "ready", 300, "the install getting ready")
    assert board.cmd({"cmd": "ota_cancel"}, "ota_cancel")["done"] == "switched_back"
    holder = env["holder"]
    other = "app1" if slot == "app0" else "app0"
    holder.release()
    head = paths.CACHE / "other-slot-head.bin"
    esptool(holder.port, "--after", "no-reset", "read-flash", hex(SLOT_OFFSET[other]), "0x20", str(head))
    assert head.read_bytes() == b"\xff" * 0x20, "the cancelled image must be unbootable (its header erased)"
    board = holder.get()                     # a restart: the old choice must start, already trusted
    st = wait_status(board, lambda s: s.get("ota_slot"), 30, "the board back")
    assert st["ota_slot"] == slot and st["firmware"] == fw
    assert st["ota_state"] == "valid", ("switching back must not put the running version on probation - a reset "
                                        "would then roll into the cancelled image")


def test_a_server_that_ignores_range_makes_the_board_start_over(env):
    board = fresh(env)
    check(board)
    env["srv"].faults.update({"ignore_range": True, "cut_at": len(env["img"]) // 3})
    board.cmd({"cmd": "ota_install"}, "ota_install")
    wait_status(board, lambda s: s.get("ota_step") == "ready", 400, "the install, started over, getting ready")
    gets = [e for e in env["srv"].log if e[0].endswith(".bin")]
    assert len(gets) >= 2 and gets[-1][2] == 200 and gets[-1][3] == len(env["img"])
    assert board.cmd({"cmd": "ota_cancel"}, "ota_cancel")["done"] == "switched_back"


def test_a_board_too_old_for_the_release_is_told(env):
    board = fresh(env)
    man, sig = release(env["img"], min_from="4.0.50")
    env["srv"].files.update({"hidfi-ota.json": man, "hidfi-ota.sig": sig})
    st = check(board)
    assert st["ota_error"] == "too_old" and st["ota_available"] == ""


def _ws(board, request):
    pin = request.config.getoption("--board-pin")
    ip = board.status().get("wifi_ip")
    websocket = pytest.importorskip("websocket", reason="pip install websocket-client")
    if not ip:
        return None
    c = websocket.create_connection(f"ws://{ip}:81/", timeout=5)
    c.settimeout(1.0)
    try:
        greet = c.recv()
    except Exception:
        greet = ""
    if isinstance(greet, str) and "auth_required" in greet:
        if not pin:
            c.close()
            return None
        c.send(json.dumps({"cmd": "auth", "token": pin}))
        end = time.time() + 5
        while time.time() < end:
            m = c.recv()
            if isinstance(m, str) and "auth_ok" in m:
                break
    c.settimeout(2.0)
    return c


def _pings(c, n, base=0):
    """A person using the pointer: a move by nothing (harmless on the computer),
    then the dashboard's ping, timed. Pings alone are keep-alives and do not
    count as use - by design - so they would not show what a person feels."""
    out = []
    for i in range(base, base + n):
        c.send_binary(struct.pack("<Bhhbb", 1, 0, 0, 0, 0))
        c.send_binary(struct.pack("<BI", 4, i))
        t0 = time.perf_counter()
        while True:
            m = c.recv()
            if isinstance(m, bytes) and len(m) == 5 and m[0] == 0x81 and struct.unpack("<I", m[1:])[0] == i:
                out.append((time.perf_counter() - t0) * 1000)
                break
        time.sleep(0.05)
    return sorted(out)


def test_end_to_end_with_a_dropped_connection(env, request):
    board = fresh(env)
    check(board)
    env["srv"].faults["cut_at"] = int(len(env["img"]) * 0.4)
    ws = _ws(board, request)
    idle = _pings(ws, 40) if ws else None
    board.cmd({"cmd": "ota_install"}, "ota_install")
    during = None
    if ws:
        wait_status(board, lambda s: s.get("ota_step") == "downloading" and s.get("ota_bytes", 0) > 100000, 120,
                    "the download under way")
        during = _pings(ws, 60, 1000)
        st = board.status()
        assert st["ota_paused"], "while the pointer is in use the download must wait"
        held = st["ota_bytes"]
        _pings(ws, 20, 2000)
        assert board.status()["ota_bytes"] == held, "the download went on while the pointer was in use"
        ws.close()
    wait_status(board, lambda s: s.get("ota_step") == "ready", 400, "the install getting ready")
    ranged = [e for e in env["srv"].log if e[0].endswith(".bin") and e[1]]
    assert ranged and ranged[-1][2] == 206, "after the drop the board must resume with Range, not start over"
    r = board.cmd({"cmd": "ota_apply"}, "ota_apply")
    assert r["status"] == "ok"
    st = wait_status(board, lambda s: s.get("firmware", "").startswith("hid_fi_v4.0.99")
                     and s.get("ota_state") == "pending_verify", 90, "the restart into 4.0.99")
    st = wait_status(board, lambda s: s.get("ota_state") == "valid", 150, "the self-test keeping 4.0.99")
    assert st["ota_result"] == "updated"
    o = board.cmd({"cmd": "ota_status"}, "ota_status")
    assert o["to"].startswith("hid_fi_v4.0.99") and o["from"].startswith("hid_fi_v4.0.4")
    if idle is None:
        pytest.skip("hot-path latency not measured: the board has a PIN and none was given (--board-pin / HIDFI_PIN)")
    p95 = lambda xs: xs[int(len(xs) * 0.95) - 1]
    print(f"WebSocket ping p95: idle {p95(idle):.0f} ms, during the download {p95(during):.0f} ms")
    (paths.REPORTS / "ota_download_latency.json").write_text(json.dumps({"idle_ms": idle, "during_ms": during}), encoding="utf-8")
    assert p95(during) <= p95(idle) + 15, \
        f"the download slows the input path: p95 {p95(idle):.0f} ms idle -> {p95(during):.0f} ms during"
    # A single spike is the home WiFi, not the board: measured 2026-10-28, idle runs
    # showed lone samples up to 311 ms and the download is paused (no flash, no
    # network) when they happen. What the board would cause is many slow samples -
    # the first measurement, before the fix, had p50 51 ms - so: at most one.
    slow = [x for x in during if x > 150]
    assert len(slow) <= 1, f"{len(slow)} inputs waited over 150 ms during the download: {[round(x) for x in slow]}"


# ---- the paired Companion (hid_fi/ota.h compOta), over the board's real HTTP -----------------
@pytest.fixture
def app(env):
    """A Companion paired for real: hello over HTTP, confirmed over USB with the code
    it computed, unpaired afterwards (the data check at the end includes pairings)."""
    from support import companion_sim as cs
    board = env["holder"].get()
    ip = board.status()["wifi_ip"]
    a = cs.FakeApp(cs.RealBoardHttp(ip), name="OTA test")
    a.ip = env["srv"].url_for(ip).split("//")[1].split(":")[0]     # this computer, as the board sees it
    assert a.hello()[1].get("state") == "pending"
    r = board.cmd({"cmd": "companion_pair", "id": a.id}, "companion_pair")
    assert r.get("state") == "confirm", r
    assert a.advance()[1].get("state") == "code_shown"
    r = board.cmd({"cmd": "companion_pair", "id": a.id, "code": a.code}, "companion_pair")
    assert r.get("state") == "paired", r
    yield a
    env["holder"].get().cmd({"cmd": "companion_unpair", "id": a.id}, "companion_unpair")


def test_the_paired_companion_checks_installs_and_cancels(env, app):
    board = on_base(env)
    r = app.ota("check")
    assert r["status"] == "ok" and r["act"] == "check", r
    st = wait_status(board, lambda s: s.get("ota_step") == "idle", 90, "the Companion's check finishing")
    assert st["ota_available"] == "4.0.99", st
    env["srv"].faults["chunk_delay"] = 0.05
    assert app.ota("install")["status"] == "ok"
    wait_status(board, lambda s: s.get("ota_bytes", 0) > 50000, 120, "the Companion's download starting")
    assert app.ota("cancel").get("done") == "stopping"
    st = wait_status(board, lambda s: s.get("ota_step") == "idle", 60, "the download stopping")
    assert st["ota_error"] == "cancelled" and st["ota_state"] == "valid"


def test_a_companion_request_cannot_be_replayed_or_reused_for_another_act(env, app):
    from support import companion_sim as cs
    board = on_base(env)
    _, r = app._call(op="chal", id=app.id)
    m = cs.mac(app.key, f"ota|{app.id}|check||{r['chal']}")
    req = dict(op="ota", id=app.id, act="check", url="", chal=r["chal"], mac=m)
    assert app._call(**req)[1]["status"] == "ok"
    wait_status(board, lambda s: s.get("ota_step") == "idle", 90, "the check finishing")
    assert app._call(**req)[1]["reply"] == "stale_chal"
    _, r = app._call(op="chal", id=app.id)
    assert app._call(**dict(req, chal=r["chal"], act="download_mode"))[1]["reply"] == "bad_mac"
    assert board.status()["ota_state"] == "valid", "a refused request changed nothing"


def test_the_companion_relays_a_release_for_one_check_only(env, app):
    """The board is pointed somewhere with no release; the Companion serves one from
    this computer. The relay is used once: the next check reads the setting again."""
    board = on_base(env)
    srv_url = env["srv"].url_for(board.status()["wifi_ip"])
    try:
        board.cmd({"cmd": "ota_source", "url": srv_url + "nothing-here/"}, "ota_source")
        assert app.ota("from", "http://192.0.2.1:9/").get("reply") == "bad_request", "only this computer may relay"
        assert app.ota("from", srv_url)["status"] == "ok"
        st = wait_status(board, lambda s: s.get("ota_step") == "idle", 90, "the relayed check finishing")
        assert st["ota_available"] == "4.0.99" and st["ota_error"] == "", st
        r = board.cmd({"cmd": "ota_source"}, "ota_source")
        assert r["url"] == srv_url + "nothing-here/", "reading the setting must not use up or show the relay"
        st = check(board)
        assert st["ota_available"] == "", "the relay is for one check; the next one reads the setting"
    finally:
        board.cmd({"cmd": "ota_source", "url": srv_url}, "ota_source")


def test_download_mode_is_refused_while_an_update_is_running(env, app):
    board = on_base(env)
    check(board)
    env["srv"].faults["chunk_delay"] = 0.05
    board.cmd({"cmd": "ota_install"}, "ota_install")
    wait_status(board, lambda s: s.get("ota_bytes", 0) > 20000, 120, "the download starting")
    assert app.ota("download_mode").get("reply") == "busy"
    board.cmd({"cmd": "ota_cancel"}, "ota_cancel")
    wait_status(board, lambda s: s.get("ota_step") == "idle", 60, "the download stopping")
