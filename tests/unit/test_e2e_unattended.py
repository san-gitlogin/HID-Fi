"""The end-to-end page runs with nobody at the computer (tests/support/sink_auto.py),
and must stay as safe as the click it replaced: the board types into whatever has the
keyboard, so nothing is sent unless the page reports its box focused AND macOS reports
the test browser in front - and a run whose canary did not arrive never goes on.
"""
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
