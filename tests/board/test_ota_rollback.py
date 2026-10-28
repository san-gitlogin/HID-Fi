"""A new firmware is kept only when it proves itself; otherwise the board goes back.

On a real board on the two-slot layout (hid_fi/partitions.csv), each scenario
installs an image into the slot that is NOT running - over the cable, exactly as
the Companion's cable path will (docs/OTA.md, steps C2/C3): write the slot, then
a new otadata entry in the sector that does not hold the current choice. Then
the board starts it and the firmware's own self-test decides (hid_fi/ota.h):

    good image                -> kept after a minute: ota_result "updated"
    self-test fails (test build)   -> the board goes back: "rolled_back", why "test_build"
    crashes before the verdict (test build) -> the bootloader goes back: "did_not_start"
    a restart asked for while proving itself -> waits for the verdict, update kept
    the COM port opened while proving itself -> rolls back (why AGENTS.md forbids it)

Every scenario checks that the person's data is untouched (compared by hash,
never printed) and that the board ends on a working, non-test firmware.

The port is opened once per install and kept open through the self-test: on
macOS every open resets the board, and a reset before the verdict rolls back.
Slow (builds two test images once, about a minute per scenario): --board, and
the board must already have the two-slot layout.
"""
import time

import pytest

from support import firmware
from support.ota_board import data_hashes, install, wait_status

pytestmark = [pytest.mark.board, pytest.mark.slow]

@pytest.fixture(scope="module")
def images():
    try:
        return {"good": firmware.build_variant("good"),
                "selftest_fail": firmware.build_variant("selftest_fail", ["HIDFI_TEST_SELFTEST_FAIL"]),
                "crash": firmware.build_variant("crash", ["HIDFI_TEST_CRASH_AT_BOOT"])}
    except RuntimeError as e:
        pytest.skip(f"could not build the test images: {e}")


@pytest.fixture
def two_slot(board):
    st = board.status()
    if st.get("ota_layout") != "two_slot":
        pytest.skip("the board is on the single-slot layout - flash it once over the cable (docs/FLASHING.md)")
    if st.get("ota_state") == "pending_verify":
        wait_status(board, lambda s: s.get("ota_state") == "valid", 200, "a previous update finishing")
    return st


def test_a_good_update_is_kept(_board_holder, two_slot, images):
    board = _board_holder.get()
    before = data_hashes(board)
    board, was, now = install(_board_holder, images["good"])
    st = board.status()
    assert st["ota_slot"] == now and st["ota_state"] == "pending_verify" and st["ota_result"] == "verifying", st
    o = board.cmd({"cmd": "ota_status"}, "ota_status")
    assert o["selftest"]["remaining_s"] > 0 and o["selftest"]["waiting_for"] in ("running", "usb_hid")
    st = wait_status(board, lambda s: s.get("ota_state") == "valid", 120, "the self-test keeping the new firmware")
    assert st["ota_slot"] == now and st["ota_result"] == "updated"
    assert any(e.get("event") == "ota" and e.get("result") == "updated" for e in board.events)
    assert data_hashes(board) == before, "the person's data changed during the update"


def test_a_failing_self_test_goes_back(_board_holder, two_slot, images):
    board = _board_holder.get()
    before = data_hashes(board)
    board, was, now = install(_board_holder, images["selftest_fail"])
    st = wait_status(board, lambda s: s.get("ota_slot") == was and s.get("ota_state") == "valid", 90,
                     "going back after the failed self-test")
    assert st["ota_result"] == "rolled_back" and not st["firmware"].endswith("-test")
    o = board.cmd({"cmd": "ota_status"}, "ota_status")
    assert o["why"] == "test_build" and o["to"].endswith("-test")
    assert data_hashes(board) == before


def test_a_crash_before_the_verdict_goes_back(_board_holder, two_slot, images):
    board = _board_holder.get()
    before = data_hashes(board)
    board, was, now = install(_board_holder, images["crash"])
    st = wait_status(board, lambda s: s.get("ota_slot") == was and s.get("ota_result") == "rolled_back", 90,
                     "the bootloader going back after the crash")
    assert st["ota_state"] == "valid" and not st["firmware"].endswith("-test")
    assert board.cmd({"cmd": "ota_status"}, "ota_status")["why"] == "did_not_start"
    assert data_hashes(board) == before


def test_a_restart_asked_for_while_proving_itself_waits(_board_holder, two_slot, images):
    board = _board_holder.get()
    mode = board.status()["pointer_mode"]
    other = "absolute" if mode == "relative" else "relative"
    board, was, now = install(_board_holder, images["good"])
    try:
        r = board.cmd({"cmd": "pointer_mode", "mode": other}, "pointer_mode")
        assert r.get("rebooting") is True
        time.sleep(5)
        st = board.status()
        assert st["ota_state"] == "pending_verify" and st["uptime_sec"] >= 5, \
            "the board restarted while the new firmware was proving itself - that rolls the update back"
        assert board.cmd({"cmd": "ota_status"}, "ota_status")["restart_deferred"] is True
        st = wait_status(board, lambda s: s.get("pointer_mode") == other, 150, "the deferred restart, after the verdict")
        assert st["ota_slot"] == now and st["ota_result"] == "updated", "the update must be kept, then the restart run"
    finally:
        board = _board_holder.get()
        if board.status().get("pointer_mode") != mode:
            board.cmd({"cmd": "pointer_mode", "mode": mode}, "pointer_mode")
            wait_status(board, lambda s: s.get("pointer_mode") == mode, 30, "restoring the pointer mode")


def test_opening_the_port_while_proving_itself_rolls_back(_board_holder, two_slot, images):
    """Why the Companion must never open the COM port during pending verify
    (AGENTS.md, "Updates and flashing"): on macOS the open is a reset."""
    board = _board_holder.get()
    board, was, now = install(_board_holder, images["good"])
    assert board.status()["ota_state"] == "pending_verify"
    _board_holder.release()
    time.sleep(1)
    board = _board_holder.get()          # a second open: a reset before the verdict
    if board.boots < 1:
        pytest.skip("this computer's port open does not reset the board - the trap does not exist here")
    st = wait_status(board, lambda s: s.get("ota_result") == "rolled_back", 60, "the rollback the reset causes")
    assert st["ota_slot"] == was
