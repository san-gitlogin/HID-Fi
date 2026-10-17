"""End to end: what the host OS actually receives from the board.

Everything lands in the focus-guarded test page (tests/support/sink.py). The
expected text comes from support/corpus.py, the same independent reference the
unit tier uses, so the board on the desk and the bench on the host are judged by
the same rules.
"""
import re

import pytest

from support import corpus
from e2e.keys import SHORTCUT_MOD, WORD_DELETE, safe_keys

pytestmark = pytest.mark.e2e

ASCII = "".join(chr(c) for c in range(0x20, 0x7F))


def typed(sink, drive, text, enter=False):
    sink.set_mode("text")
    sink.clear()
    r = drive({"cmd": "type", "text": text, "enter": enter}, "typed", timeout=180)
    got, _ = sink.settle()
    return got, r


def steps(sink, drive, cmds, mode="text"):
    sink.set_mode(mode)
    sink.clear()
    for c in cmds:
        drive(c)
    return sink.settle()


# ---- typing ---------------------------------------------------------------------------
def test_every_printable_ascii_character(sink, drive):
    got, r = typed(sink, drive, ASCII)
    assert got == ASCII and r["skipped"] == 0


def test_sentence_and_enter(sink, drive):
    got, _ = typed(sink, drive, "Hello, World! 123", enter=True)
    assert got == "Hello, World! 123\n"


def test_editing_keys(sink, drive):
    got, _ = steps(sink, drive, [{"cmd": "type", "text": "abcdef"}, {"cmd": "press", "keys": "BACKSPACE"},
                                 {"cmd": "press", "keys": "LEFT"}, {"cmd": "type", "text": "X"},
                                 {"cmd": "press", "keys": "HOME"}, {"cmd": "type", "text": ">"},
                                 {"cmd": "press", "keys": "END"}, {"cmd": "type", "text": "<"}])
    assert got == ">abcdXe<"


def test_select_all_copy_paste_undo(sink, drive, host):
    m = SHORTCUT_MOD[host]
    got, _ = steps(sink, drive, [{"cmd": "type", "text": "copy me"}, {"cmd": "press", "keys": f"{m}+A"},
                                 {"cmd": "press", "keys": f"{m}+C"}, {"cmd": "press", "keys": "END"},
                                 {"cmd": "type", "text": " "}, {"cmd": "press", "keys": f"{m}+V"}])
    assert got == "copy me copy me"
    got, _ = steps(sink, drive, [{"cmd": "type", "text": "old"}, {"cmd": "press", "keys": f"{m}+A"},
                                 {"cmd": "type", "text": "new"}])
    assert got == "new"


def test_word_delete(sink, drive, host):
    got, _ = steps(sink, drive, [{"cmd": "type", "text": "one two"}, {"cmd": "press", "keys": WORD_DELETE[host]}])
    assert got == "one "


def test_held_modifier_multi_touch_path(sink, drive):
    got, _ = steps(sink, drive, [{"cmd": "key_down", "key": "SHIFT"}, {"cmd": "key_down", "key": "x"},
                                 {"cmd": "key_up", "key": "x"}, {"cmd": "key_up", "key": "SHIFT"},
                                 {"cmd": "key_down", "key": "y"}, {"cmd": "key_up", "key": "y"}])
    assert got == "Xy"


def test_key_release_all_lets_go(sink, drive):
    got, ev = steps(sink, drive, [{"cmd": "key_down", "key": "SHIFT"}, {"cmd": "key_release_all"},
                                  {"cmd": "type", "text": "z"}])
    assert got == "z", "Shift was still held after key_release_all"


def test_macro_text_key_text(board, sink, drive):
    slot = board.free_macro_slot()
    board.cmd({"cmd": "macro_save", "slot": slot, "name": "e2e", "steps": ["text Hi there", "key ENTER", "text done!"]})
    try:
        got, _ = steps(sink, drive, [{"cmd": "macro_run", "slot": slot}])
        assert got == "Hi there\ndone!"
    finally:
        board.cmd({"cmd": "macro_delete", "slot": slot})


# ---- every key, by code -----------------------------------------------------------------
def test_every_safe_key_arrives_with_the_right_code(sink, drive, host):
    wrong = []
    for name, code in safe_keys(host).items():
        _, ev = steps(sink, drive, [{"cmd": "key_down", "key": name}, {"cmd": "key_up", "key": name}], mode="keys")
        downs = [e["code"] for e in ev if e.get("k") == "keydown"]
        ups = [e["code"] for e in ev if e.get("k") == "keyup"]
        if code not in downs or code not in ups:
            wrong.append(f"{name}: expected {code}, got down={downs} up={ups}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("combo,code,mods", [("SHIFT+A", "KeyA", "S"), ("CTRL+B", "KeyB", "C")])
def test_combos_carry_their_modifiers(sink, drive, combo, code, mods):
    _, ev = steps(sink, drive, [{"cmd": "press", "keys": combo}], mode="keys")
    hits = [e for e in ev if e.get("k") == "keydown" and e.get("code") == code]
    assert hits and all(m in hits[0]["mods"] for m in mods), f"{combo}: {hits or ev}"


# ---- every language ------------------------------------------------------------------------
LANGS = [k for k in corpus.CORPUS if k not in corpus.HOST_UNTYPEABLE]


def mac_double_space(text):
    """macOS "Add period with double-space" (System Settings -> Keyboard -> Text
    Input), which Chrome honours: two spaces after a word become ". ". The board
    typed both spaces; the OS rewrote them. Verified against real output."""
    return re.sub(r"(?<=[^\s.])  ", ". ", text)


@pytest.mark.parametrize("label", LANGS)
def test_language_with_unicode_entry_off(board, sink, drive, label, host):
    """Off: curly quotes and dashes fold to ASCII, everything else is skipped and counted."""
    st = board.status()
    for os_ in ("mac", "linux"):
        if st[f"unicode_{os_}"]:
            board.cmd({"cmd": "set_unicode", "os": os_, "on": False}, "unicode_set")
    raw = corpus.CORPUS[label].encode()
    off, osk, _, _ = corpus.expect(raw)
    got, r = typed(sink, drive, corpus.CORPUS[label])
    assert r["skipped"] == osk
    allowed = {off, mac_double_space(off)} if host == "mac" else {off}
    assert got in allowed, f"got {got!r}, expected {off!r}"


def _unicode_ready(request, board, host):
    if host == "mac":
        if not request.config.getoption("--allow-input-source-switch"):
            pytest.skip("macOS Unicode entry needs 'Unicode Hex Input' selected - rerun with --allow-input-source-switch")
    elif host == "linux":
        pass   # IBus/GTK desktops (GNOME) only - a Qt/KDE browser will fail these, and the docs say so
    else:
        pytest.skip(f"no Unicode entry on {host} by design")


@pytest.fixture(scope="module")
def unicode_on(request, _board_holder, sink):
    host = sink.host_os
    board = _board_holder.get()
    _unicode_ready(request, board, host)
    board.cmd({"cmd": "set_host_os", "os": host}, "host_os_set")
    board.cmd({"cmd": "set_unicode", "os": host, "on": True}, "unicode_set")
    if host == "mac":
        from support.macos import unicode_hex_input
        with unicode_hex_input():
            yield host
    else:
        yield host
    board.cmd({"cmd": "set_unicode", "os": host, "on": False}, "unicode_set")
    board.cmd({"cmd": "set_host_os", "os": "auto"}, "host_os_set")


@pytest.mark.parametrize("label", LANGS)
def test_language_with_unicode_entry_on(unicode_on, sink, drive, label):
    _, _, on, nsk = corpus.expect(corpus.CORPUS[label].encode())
    got, r = typed(sink, drive, corpus.CORPUS[label])
    assert (got, r["skipped"]) == (on, nsk)


def test_unicode_test_button_sample(unicode_on, sink, drive):
    sink.set_mode("text")
    sink.clear()
    r = drive({"cmd": "unicode_test", "os": unicode_on}, "unicode_test_sent")
    got, _ = sink.settle()
    assert got == "é\U0001F600" == r["sample"]


def test_pin_left_from_a_mac_cannot_fire_on_a_pc(board, sink, drive, host):
    """The board pinned to macOS with emoji on, but plugged into this PC: only plain characters."""
    if host == "mac":
        pytest.skip("this host is a Mac - the guard is for PCs")
    if board.status().get("host_os_detected") not in ("windows", "linux"):
        pytest.skip("the board has not detected this computer as a PC")
    board.cmd({"cmd": "set_host_os", "os": "mac"}, "host_os_set")
    board.cmd({"cmd": "set_unicode", "os": "mac", "on": True}, "unicode_set")
    try:
        for label in ("smart_punct", "emoji_skin", "tamil", "latin_accents"):
            off, osk, _, _ = corpus.expect(corpus.CORPUS[label].encode())
            got, r = typed(sink, drive, corpus.CORPUS[label])
            assert (got, r["skipped"]) == (off, osk), label
        sink.clear()
        t = drive({"cmd": "unicode_test", "os": "mac"})
        got, _ = sink.settle()
        assert t.get("status") == "error" and got == "", "Test on macOS must refuse on a PC and type nothing"
    finally:
        board.cmd({"cmd": "set_unicode", "os": "mac", "on": False}, "unicode_set")
        board.cmd({"cmd": "set_host_os", "os": "auto"}, "host_os_set")


# ---- the mouse -----------------------------------------------------------------------------
@pytest.fixture
def locked(sink):
    if not sink.info().get("locked"):
        pytest.skip("pointer lock not active (click Start again) - mouse tests need it")
    return sink


def _moves(ev):
    return sum(e["dx"] for e in ev if e.get("k") == "move"), sum(e["dy"] for e in ev if e.get("k") == "move")


@pytest.mark.parametrize("dx,dy", [(20, 0), (0, -15), (-12, 9)])
def test_small_move_and_its_inverse_cancel_exactly(locked, drive, dx, dy):
    _, ev = steps(locked, drive, [{"cmd": "mouse_move", "dx": dx, "dy": dy}, {"cmd": "mouse_move", "dx": -dx, "dy": -dy}], mode="keys")
    assert [e for e in ev if e.get("k") == "move"], "no pointer movement reached the page"
    assert _moves(ev) == (0, 0), f"move and its inverse should cancel exactly, net {_moves(ev)}"


@pytest.mark.parametrize("dx,dy", [(400, 300), (-999, 0)])
def test_large_move_arrives_in_the_right_direction(locked, drive, dx, dy):
    """A large move can reach the edge of the screen, where macOS and Windows stop
    the pointer, so exact pixels are checked on the board tier and on Linux
    (evdev, no screen edges). Here: both legs arrive, each the right way."""
    for leg in (1, -1):
        _, ev = steps(locked, drive, [{"cmd": "mouse_move", "dx": leg * dx, "dy": leg * dy}], mode="keys")
        sx, sy = _moves(ev)
        assert (sx, sy) != (0, 0), "no pointer movement reached the page"
        if dx:
            assert (sx > 0) == (leg * dx > 0), f"moved the wrong way horizontally: {sx}"
        if dy:
            assert (sy > 0) == (leg * dy > 0), f"moved the wrong way vertically: {sy}"


def test_wheel_and_pan_arrive_and_reverse(locked, drive):
    _, ev = steps(locked, drive, [{"cmd": "mouse_scroll", "amount": 3}, {"cmd": "mouse_scroll", "amount": -3},
                                  {"cmd": "mouse_scroll", "amount": 0, "pan": 2}, {"cmd": "mouse_scroll", "amount": 0, "pan": -2}], mode="keys")
    wy = [e["dy"] for e in ev if e.get("k") == "wheel" and e["dy"]]
    wx = [e["dx"] for e in ev if e.get("k") == "wheel" and e["dx"]]
    assert wy and any(v > 0 for v in wy) and any(v < 0 for v in wy), f"vertical wheel did not go both ways: {wy}"
    assert wx and any(v > 0 for v in wx) and any(v < 0 for v in wx), f"horizontal pan did not go both ways: {wx}"


@pytest.mark.parametrize("button,code", [("left", 0), ("middle", 1), ("right", 2)])
def test_buttons(locked, drive, button, code):
    _, ev = steps(locked, drive, [{"cmd": "mouse_click", "button": button}], mode="keys")
    downs = [e["button"] for e in ev if e.get("k") == "mousedown"]
    ups = [e["button"] for e in ev if e.get("k") == "mouseup"]
    assert code in downs and code in ups, f"{button}: down={downs} up={ups}"


def test_press_hold_release(locked, drive):
    _, ev = steps(locked, drive, [{"cmd": "mouse_press", "button": "left"}, {"cmd": "mouse_move", "dx": 30, "dy": 0},
                                  {"cmd": "mouse_move", "dx": -30, "dy": 0}, {"cmd": "mouse_release", "button": "left"}], mode="keys")
    kinds = [e["k"] for e in ev if e.get("k") in ("mousedown", "move", "mouseup")]
    # A late pointer event from the previous test may land first; what matters is
    # the drag itself: button down, then movement, then button up.
    assert "mousedown" in kinds and "mouseup" in kinds, kinds
    down, up = kinds.index("mousedown"), len(kinds) - 1 - kinds[::-1].index("mouseup")
    assert down < up and "move" in kinds[down:up], kinds


def test_wiggle_is_net_zero(locked, drive):
    _, ev = steps(locked, drive, [{"cmd": "wiggle", "mode": "single", "amplitude": 8}], mode="keys")
    assert [e for e in ev if e.get("k") == "move"], "wiggle moved nothing"
    assert _moves(ev) == (0, 0), f"wiggle left the cursor displaced by {_moves(ev)}"
