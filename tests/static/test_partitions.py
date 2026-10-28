"""The flash layout (hid_fi/partitions.csv) - pinned, because boards depend on it.

Once a board has this table, it can never change over the air: the table is
written only over the cable, and an update that moved a partition would leave a
board whose bootloader, settings or slots are somewhere else. So the table is
pinned here row by row; changing a row fails this test until the change is
deliberate, here, with its reason (docs/OTA.md "Layout").

The one rule that keeps everyone's data: NVS stays at 0x9000, 0x5000 bytes - the
same as the old single-slot layout (huge_app). Moving it would lose every
setting, PIN, vault entry, macro and pairing on the board that is switched.
Phase 0 (docs/OTA.md) proved on a board that switching keeps NVS byte-identical.
"""
import pytest

from support import firmware

pytestmark = pytest.mark.static

FLASH = 16 * 1024 * 1024

PINNED = [
    # name       type    subtype      offset     size       why
    ("nvs",      "data", "nvs",       0x9000,    0x5000),   # every setting - never moves
    ("otadata",  "data", "ota",       0xE000,    0x2000),   # which slot boots - where boot_app0 is written
    ("app0",     "app",  "ota_0",     0x10000,   0x640000), # where every flasher writes the app
    ("app1",     "app",  "ota_1",     0x650000,  0x640000), # the other slot: updates land here first
    ("nvs_ext",  "data", "nvs",       0xC90000,  0x40000),  # reserved, empty: room beyond nvs's 630 entries
    ("spare",    "data", "undefined", 0xCD0000,  0x320000), # reserved, empty
    ("coredump", "data", "coredump",  0xFF0000,  0x10000),
]


def test_the_table_is_exactly_the_pinned_one():
    got = [(r["name"], r["type"], r["subtype"], r["offset"], r["size"]) for r in firmware.partitions()]
    assert got == PINNED, ("hid_fi/partitions.csv changed. A board that already has the old table keeps it "
                           "(it is only written over the cable) - read docs/OTA.md 'Layout' before changing it")


def test_settings_stay_where_every_released_board_has_them():
    nvs = [r for r in firmware.partitions() if r["name"] == "nvs"]
    assert len(nvs) == 1 and (nvs[0]["offset"], nvs[0]["size"]) == (0x9000, 0x5000)


def test_two_equal_app_slots_and_otadata():
    rows = firmware.partitions()
    apps = [r for r in rows if r["type"] == "app"]
    assert [a["subtype"] for a in apps] == ["ota_0", "ota_1"]
    assert apps[0]["size"] == apps[1]["size"], "an update is written to the other slot - it must be as large"
    assert apps[0]["offset"] == 0x10000, "the flashers and the release FLASH.md write the app at 0x10000"
    assert sum(1 for r in rows if r["subtype"] == "ota") == 1


def test_no_overlap_nothing_past_the_flash_and_aligned():
    rows = sorted(firmware.partitions(), key=lambda r: r["offset"])
    assert rows[0]["offset"] >= 0x9000, "0x0-0x8fff holds the bootloader and the table itself"
    for a, b in zip(rows, rows[1:]):
        assert a["offset"] + a["size"] <= b["offset"], f"{a['name']} overlaps {b['name']}"
    assert rows[-1]["offset"] + rows[-1]["size"] <= FLASH
    for r in rows:
        align = 0x10000 if r["type"] == "app" else 0x1000
        assert r["offset"] % align == 0 and r["size"] % 0x1000 == 0, f"{r['name']} is not aligned"


def test_the_flashers_build_with_this_table():
    """PartitionScheme=custom: the sketch's partitions.csv is the table, and the
    size check that matters is the slot (tests/compile/test_build.py), not the menu's."""
    assert "PartitionScheme=custom" in firmware.fqbn()
