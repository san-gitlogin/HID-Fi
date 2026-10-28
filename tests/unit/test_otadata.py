"""tests/support/otadata.py - the bootloader's slot choice, as the real one makes it.

Checked against the core's own boot_app0.bin (what every cable flash writes at
0xe000), and against what DA1A2C and 09F7C8 showed on 2026-10-28 (docs/OTA.md,
phase 0): NEW was turned VALID by the first start, PENDING_VERIFY after a reset
became ABORTED and the other slot started.
"""
import pytest

from support import firmware, otadata as od

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def boot_app0():
    data = firmware.arduino_data_dir()
    core = firmware.installed_core_version()
    f = data and core and data / "packages" / "esp32" / "hardware" / "esp32" / core / "tools" / "partitions" / "boot_app0.bin"
    if not f or not f.exists():
        pytest.skip("the ESP32 core is not installed")
    return f.read_bytes()


def test_boot_app0_starts_slot_0_and_our_crc_agrees(boot_app0):
    assert len(boot_app0) == od.SIZE
    entries = od.read(boot_app0)
    assert entries[0][1:] == (1, od.UNDEFINED, True), "our CRC must agree with the core's file"
    assert od.winner(boot_app0) == (0, 1, 0, od.UNDEFINED)


def test_switching_writes_the_other_sector_and_keeps_the_old_choice(boot_app0):
    new = od.switch_to(boot_app0, slot=1)
    assert new[:od.SECTOR] == boot_app0[:od.SECTOR], "the current winner's sector must be untouched"
    assert od.winner(new) == (1, 2, 1, od.NEW)
    back = od.switch_to(new, slot=0)
    assert back[od.SECTOR:] == new[od.SECTOR:]
    assert od.winner(back) == (0, 3, 0, od.NEW)


def test_a_half_written_sector_leaves_the_old_choice(boot_app0):
    new = bytearray(od.switch_to(boot_app0, slot=1))
    new[od.SECTOR + 28:od.SECTOR + 32] = b"\xff\xff\xff\xff"        # power cut before the CRC landed
    assert od.winner(bytes(new)) == (0, 1, 0, od.UNDEFINED)


@pytest.mark.parametrize("state, starts", [(od.ABORTED, 0), (od.INVALID, 0), (od.PENDING_VERIFY, 1),
                                           (od.VALID, 1), (od.NEW, 1)])
def test_rejected_slots_are_skipped(boot_app0, state, starts):
    data = od.switch_to(boot_app0, slot=1, state=state)
    assert od.winner(data)[2] == starts


def test_sequences_always_land_on_the_asked_slot(boot_app0):
    data = boot_app0
    for want in [1, 1, 0, 0, 1, 0, 1]:
        data = od.switch_to(data, slot=want)
        assert od.winner(data)[2] == want
