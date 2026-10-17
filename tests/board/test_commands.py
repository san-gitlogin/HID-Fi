"""Board tier: the command surface over serial. Nothing here types into the host.

Anything that would put a keystroke on screen is in tests/e2e/, where it can only
land in the focus-guarded test page.
"""
import pytest

pytestmark = pytest.mark.board


def test_board_identifies_itself(board):
    st = board.status()
    assert st.get("firmware", "").startswith("hid_fi_v"), st
    assert st.get("hid_ready") is True
    for k in ("unicode_mac", "unicode_linux", "host_os", "host_os_source", "host_os_detected",
              "usb_mounted", "detect_phase", "free_heap", "heap_floor", "uptime_sec"):
        assert k in st, f"status lost the {k!r} field"
    assert isinstance(st["unicode_mac"], bool) and isinstance(st["unicode_linux"], bool)


def test_unknown_command_is_an_error_not_silence(board):
    r = board.cmd({"cmd": "definitely_not_a_command"})
    assert r.get("status") == "error"


def test_echo_returns_what_it_was_given(board):
    r = board.cmd({"cmd": "echo", "data": "hello \u00e9 \U0001F600"}, "echo")
    assert r.get("data") == "hello \u00e9 \U0001F600" and r.get("length") == len("hello \u00e9 \U0001F600".encode())


@pytest.mark.parametrize("os_", ["mac", "linux"])
def test_set_unicode_round_trip(board, os_):
    before = board.status()[f"unicode_{os_}"]
    for want in (True, False, before):
        r = board.cmd({"cmd": "set_unicode", "os": os_, "on": want}, "unicode_set")
        assert r.get(f"unicode_{os_}") is want
        assert board.status()[f"unicode_{os_}"] is want, "setting not reflected in status"


@pytest.mark.parametrize("bad", ["windows", "win", "beos", ""])
def test_set_unicode_refuses_other_systems(board, bad):
    r = board.cmd({"cmd": "set_unicode", "os": bad, "on": True}) if bad else None
    if bad:
        assert r.get("status") == "error" and "mac or linux" in r.get("message", "")


@pytest.mark.parametrize("os_", ["windows", "beos"])
def test_unicode_test_refuses_unsupported_systems(board, os_):
    r = board.cmd({"cmd": "unicode_test", "os": os_})
    assert r.get("status") == "error"


def test_type_reports_skipped_and_types_nothing_it_cannot(board):
    """Only an emoji, with Unicode entry off for every OS: the board must press
    nothing and say it skipped one character."""
    st = board.status()
    keep = (st["unicode_mac"], st["unicode_linux"])
    try:
        board.cmd({"cmd": "set_unicode", "os": "mac", "on": False}, "unicode_set")
        board.cmd({"cmd": "set_unicode", "os": "linux", "on": False}, "unicode_set")
        r = board.cmd({"cmd": "type", "text": "\U0001F600"}, "typed")
        assert r.get("skipped") == 1 and r.get("length") == 4, r
    finally:
        board.cmd({"cmd": "set_unicode", "os": "mac", "on": keep[0]}, "unicode_set")
        board.cmd({"cmd": "set_unicode", "os": "linux", "on": keep[1]}, "unicode_set")


def test_type_without_text_is_refused(board):
    r = board.cmd({"cmd": "type", "text": ""})
    assert r.get("status") == "error"


def test_settings_survive_in_status_after_many_commands(board):
    """A burst of status/ping traffic must not disturb what status reports."""
    first = board.status()
    for _ in range(50):
        board.cmd({"cmd": "ping"})
    last = board.status()
    for k in ("unicode_mac", "unicode_linux", "power_mode", "host_os"):
        assert first.get(k) == last.get(k), k
