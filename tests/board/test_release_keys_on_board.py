"""The release keys, on a real board: a normal build (no test key) accepts a
manifest signed with the primary key and with the offline backup, and refuses one
signed with the TEST key. Needs the private keys of tools/release_keys.json on
this computer (~/.hidfi-release, or HIDFI_RELEASE_KEY_DIR) - skipped elsewhere.
Nothing is installed: checks only. --board.
"""
import json
import os
from pathlib import Path

import pytest

from board.test_ota_download import CHANGELOG, _settle
from support import firmware, paths
from support.ota_board import data_hashes, install, wait_status
from support.release_server import ReleaseServer

pytestmark = [pytest.mark.board, pytest.mark.slow]
import sys  # noqa: E402
sys.path.insert(0, str(paths.TESTS.parent / "tools"))
import ota_release as R  # noqa: E402

KEY_DIR = Path(os.environ.get("HIDFI_RELEASE_KEY_DIR", "~/.hidfi-release")).expanduser()


def _key(name):
    p = KEY_DIR / f"hidfi-release-{name}.pem"
    if not p.exists():
        pytest.skip(f"no {p.name} on this computer")
    return R.load_private(p)


@pytest.fixture(scope="module")
def normal(_board_holder):
    try:
        good = firmware.build_variant("good")
        new = firmware.build_variant("plain99", ["HIDFI_TEST_PATCH=99"])
    except RuntimeError as e:
        pytest.skip(f"could not build: {e}")
    board = _board_holder.get()
    if board.status().get("ota_layout") != "two_slot":
        pytest.skip("single-slot board")
    before = data_hashes(board)
    _settle(board)
    install(_board_holder, good)
    board = _board_holder.get()
    wait_status(board, lambda s: s.get("ota_state") == "valid", 150, "the normal build proving itself")
    board.cmd({"cmd": "ota_settings", "auto": False}, "ota_settings")
    img = new.read_bytes()
    with ReleaseServer({"hid_fi-4.0.99.bin": img}) as srv:
        ip = wait_status(board, lambda s: s.get("wifi_connected"), 60, "on its network")["wifi_ip"]
        board.cmd({"cmd": "ota_source", "url": srv.url_for(ip)}, "ota_source")
        yield board, srv, img
    board = _board_holder.get()
    board.cmd({"cmd": "ota_source", "url": ""}, "ota_source")
    board.cmd({"cmd": "ota_settings", "auto": True}, "ota_settings")
    assert data_hashes(board) == before


def _check_signed_by(board, srv, img, key):
    m = R.canonical(R.build_manifest("4.0.99", img, "4.0.3", "3.3.12", "5.5.5", CHANGELOG, image_name="hid_fi-4.0.99.bin",
                                     _tests_allow_test_build=True))
    srv.files.update({"hidfi-ota.json": m, "hidfi-ota.sig": json.dumps(R.sign(m, key)).encode()})
    wait_status(board, lambda s: s.get("ota_step") == "idle", 90, "idle")
    assert board.cmd({"cmd": "ota_check"}, "ota_check")["status"] == "ok"
    import time; time.sleep(1)
    return wait_status(board, lambda s: s.get("ota_step") == "idle", 90, "the check finishing")


@pytest.mark.parametrize("name", ["primary", "backup"])
def test_a_normal_build_trusts_each_release_key(normal, name):
    board, srv, img = normal
    st = _check_signed_by(board, srv, img, _key(name))
    assert st["ota_available"] == "4.0.99" and st["ota_error"] == "", st


def test_a_normal_build_refuses_the_test_key(normal):
    from cryptography.hazmat.primitives import serialization
    board, srv, img = normal
    vec = json.loads((paths.TESTS / "vectors" / "ota_v1.json").read_text(encoding="utf-8"))
    test = serialization.load_pem_private_key(vec["test_private_key_pem"].encode(), password=None)
    st = _check_signed_by(board, srv, img, test)
    assert st["ota_error"] == "bad_signature" and st["ota_available"] == "", st
