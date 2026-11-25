"""The end-to-end page runs with nobody at the computer (tests/support/sink_auto.py),
and must stay as safe as the click it replaced: the board types into whatever has the
keyboard, so nothing is sent unless the page reports its box focused AND macOS reports
the test browser in front - and a run whose canary did not arrive never goes on.
"""
import json
import sys
import time

import pytest

from support import paths
from support.sink import Sink, SinkNotFocused

pytestmark = pytest.mark.unit


class FakeAuto:
    def __init__(self, front):
        self.front = front

    def in_front(self):
        return self.front


def focused_sink():
    s = Sink()
    with s.lock:
        s.state["focused"] = True
    return s


def test_a_focused_page_is_not_enough_when_another_app_is_in_front():
    s = focused_sink()
    try:
        s.auto = FakeAuto(front=False)
        with pytest.raises(SinkNotFocused, match="not the frontmost application"):
            s.assert_focused()
        s.auto = FakeAuto(front=True)
        s.assert_focused()                       # both proofs: goes ahead
    finally:
        s.close()


def test_the_browser_in_front_is_not_enough_when_the_page_lost_focus():
    s = Sink()
    try:
        s.auto = FakeAuto(front=True)
        with pytest.raises(SinkNotFocused, match="lost focus"):
            s.assert_focused()
    finally:
        s.close()


def test_the_manual_page_keeps_its_one_proof():
    s = focused_sink()
    try:
        s.auto = None
        s.assert_focused()
    finally:
        s.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS reads the frontmost application")
def test_macos_names_a_real_frontmost_process():
    from support.sink_auto import frontmost_pid
    pid = frontmost_pid()
    assert isinstance(pid, int) and pid > 0


def test_the_fixture_proves_the_whole_path_before_any_test():
    src = (paths.TESTS / "conftest.py").read_text(encoding="utf-8")
    auto = src[src.index("from support.sink_auto import AutoPage"):src.index("tw.line(\"=\" * 72, yellow=True)\n    if remote:")]
    assert auto.index("s.auto.aim()") < auto.index('"mouse_click"') < auto.index("wait_ready(") \
        < auto.index("_canary(board, s)") < auto.index("yield s"), \
        "in front under the cursor before the board clicks; focused, then the canary, then the tests"
    assert "pytest.fail(" in auto and "pytest.skip(" not in auto, "an unattended page that fails never becomes a skip"
    canary = src[src.index("def _canary("):src.index("@pytest.fixture\ndef host(")]
    assert "if got != CANARY:" in canary and "pytest.fail(" in canary


def test_the_browser_is_never_driven_through_playwright():
    """A Playwright-driven page EMULATES focus (it said "focused" while keystrokes went
    to another app): the unattended browser is a plain app with its own profile."""
    src = (paths.TESTS / "support" / "sink_auto.py").read_text(encoding="utf-8")
    code = src[src.index("class AutoPage"):]
    assert "launch(" not in code and "new_page" not in code and "sync_playwright" not in code
    assert "--user-data-dir=" in code


def test_a_run_that_loses_a_saved_network_fails_and_never_prints_its_name():
    """The board-level 'left as found' check covers the owner's saved networks: count
    and hash only in what people read (tests/conftest.py _redacted)."""
    import conftest
    before = {"macros": {}, "saved_networks": ["Home", "Office"], "preferred_network": "Home"}
    after = dict(before, saved_networks=["Home"])
    assert before != after                                   # the run's final != snap check fails
    shown = json.dumps(conftest._redacted(after))
    assert "1 saved" in shown and "Home" not in shown and "Office" not in shown


def test_a_hardware_test_whose_computer_changed_network_is_an_error_not_a_verdict():
    """The 390 ms 'regression' of 2026-11-24 was the Mac hopping networks mid-test."""
    src = (paths.TESTS / "conftest.py").read_text(encoding="utf-8")
    f = src[src.index("def _this_computer_stays_on_its_network("):src.index("@pytest.fixture\ndef board(")]
    assert '("board", "e2e", "perf", "linux")' in f and "stop.wait(1)" in f and "pytest.fail(" in f
    import conftest
    assert conftest._this_computers_address() is not None


def test_hardware_tests_reach_the_board_before_judging_it():
    """macOS calls a host 'down' for 20 s after a failed lookup: a board just restarted
    reports online before this computer can reach it. Every direct socket retries."""
    import re
    assert "REACH_S = 45" in (paths.TESTS / "support" / "board.py").read_text(encoding="utf-8")
    for f in ["board/test_ota_download.py", "perf/test_perf.py", "board/test_press_latency_hw.py",
              "board/test_reach_by_name_live.py"]:
        src = (paths.TESTS / f).read_text(encoding="utf-8")
        assert not re.search(r"websocket\.create_connection\(", src), f"{f} connects without retrying"


def test_a_board_behind_usb_hubs_is_found_on_the_native_usb():
    """13 recovery-mode tests skipped 'not plugged into this computer' for a board that
    was - behind two hubs, nested in the IORegistry, where only the top was looked at."""
    import sys
    sys.path.insert(0, str(paths.TESTS / "board"))
    import test_recovery_mode_hw as t
    board = {"IOObjectClass": "IOUSBHostDevice", "USB Serial Number": "DCB4D909F7C8", "IORegistryEntryID": 7,
             "idVendor": 0x303A, "IORegistryEntryChildren": [{"IOObjectClass": "IOUSBHostInterface", "bInterfaceClass": 3}]}
    hub = lambda kids, i: {"IOObjectClass": "IOUSBHostDevice", "USB Product Name": "USB2.0 HUB",
                           "IORegistryEntryID": i, "IORegistryEntryChildren": kids}
    tree = [hub([hub([board], 2)], 1)]
    assert t._usb_identity("DCB4D909F7C8", tree), "a board two hubs deep is plugged in"
    assert not t._usb_identity("B81F3FDA1A2C", tree)


def test_rehearsals_touch_no_radio():
    """D-17: in the firmware every leave/join of a rehearsal goes through staDrop/the
    rehearsed join; the radio is disconnected only on the real paths."""
    ino = (paths.TESTS.parent / "hid_fi" / "hid_fi.ino").read_text(encoding="utf-8")
    assert ino.count("WiFi.disconnect(false)") == 4, "a new direct disconnect: route it through staDrop()"
    begin = ino[ino.index("void staBeginJoin("):]
    assert begin.index("if (staRehearsing() && radioOnline()) {") < begin.index("staRadioJoins++") < begin.index("WiFi.begin(")
    conf = (paths.TESTS / "conftest.py").read_text(encoding="utf-8")
    assert '"rehearsal" in request.node.keywords and (joins - (1 if restarted else 0) > 0 or drops)' in conf
    assert "ROUTER_JOINS_PER_10_MIN = 10" in conf
    reh = (paths.TESTS / "board" / "test_networks_rehearsed.py").read_text(encoding="utf-8")
    assert "pytest.mark.rehearsal" in reh


def test_every_module_that_moves_the_board_is_a_rehearsal():
    """D-17: a module that stages network moves carries the rehearsal marker, so the
    harness proves it never touched the router; real moves are opt-in."""
    for f in ["board/test_networks_rehearsed.py", "board/test_home_network.py", "board/test_reach_by_name_live.py"]:
        assert "pytest.mark.rehearsal" in (paths.TESTS / f).read_text(encoding="utf-8"), f
    w = (paths.TESTS / "board" / "test_where_the_computer_is_hw.py").read_text(encoding="utf-8")
    assert w.count("@pytest.mark.rehearsal") == 3 and 'os.environ.get("HIDFI_REAL_MOVES")' in w
    two = (paths.TESTS.parent / "companion" / "tests" / "two_networks.py").read_text(encoding="utf-8")
    assert 'os.environ.get("HIDFI_REAL_MOVES")' in two
