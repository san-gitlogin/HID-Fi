"""Every key name the firmware accepts has a Linux key, derived from the sources.

Guards the Linux end-to-end suite's expectations - and catches a firmware key
name that maps to nothing (it would be silently dropped on every host).
Downloads the kernel's hid-input.c once (cached in tests/.build).
"""
import urllib.error

import pytest

from support import linux_keys

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def table():
    try:
        return linux_keys.kernel_hid_keyboard()
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(f"could not fetch the kernel's hid-input.c (offline?): {e}")


def test_every_firmware_key_name_has_a_linux_key(table):
    try:
        ev = linux_keys.evdev_for_names()
    except RuntimeError as e:
        pytest.skip(str(e))
    missing = sorted(n for n, c in ev.items() if not c)
    assert len(ev) > 80, f"only {len(ev)} key names parsed from mapKeyName() - the parser broke"
    assert not missing, f"key names with no Linux key: {missing}"


def test_every_media_key_has_a_linux_key(table):
    m = linux_keys.media_names()
    assert len(m) >= 15
    missing = sorted(k for k, u in m.items() if linux_keys.CONSUMER.get(u) is None)
    assert not missing, f"media keys with no verified Linux mapping: {missing}"


def test_every_printable_character_is_typeable(table):
    try:
        m = linux_keys.ascii_to_evdev()
    except RuntimeError as e:
        pytest.skip(str(e))
    assert len(m) == 95 and all(code for code, _ in m.values())
