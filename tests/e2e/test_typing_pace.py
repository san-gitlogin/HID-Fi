"""End to end: the typing pace as the computer receives it (hid_fi/typer.h).

Measured in the focus-guarded test page from the browser's own keydown timestamps:
    the board's own pace (the default) is the old one: the text exactly, ~20 ms apart
    a pace of 10 characters a second puts characters ~100 ms apart
    the rhythm option varies the gaps, within their bounds
    pause holds the text back between characters and resume finishes it exactly
    stop leaves a prefix typed and no key held down
    a shortcut sent while text is typing goes after the text, never inside it
The board's saved pace is put back afterwards.
"""
import statistics
import time

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture
def pace(board):
    st = board.status()
    before = {"cps": st.get("type_cps", 0), "rhythm": st.get("type_rhythm", False)}

    def set_(cps, rhythm=False):
        r = board.cmd({"cmd": "type_settings", "cps": cps, "rhythm": rhythm}, "type_settings")
        assert r.get("status") == "ok", r
    yield set_
    board.cmd({"cmd": "type_stop"}, "type_stop")
    board.cmd(dict({"cmd": "type_settings"}, **before), "type_settings")


def downs(events):
    return [e["ts"] for e in events if e.get("k") == "keydown"]


def gaps(ts):
    return [b - a for a, b in zip(ts, ts[1:])]


def sit_out(text, cps):
    """Wait the time the text takes, without asking the board anything meanwhile: a status
    over the serial cable (~2 KB at 115200 baud) holds the board's loop for 100+ ms and
    showed up as 250 ms gaps every third character (2026-11-25)."""
    time.sleep(len(text) / cps * 1.35 + 0.5)


def wait_typed(board, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if not board.status().get("typing"):
            return
        time.sleep(0.3)
    raise AssertionError("the board was still typing")


def test_the_boards_own_pace_is_the_old_one(sink, drive, board, pace):
    pace(0)
    sink.set_mode("text"); sink.clear()
    text = "the quick brown fox jumps over"
    r = drive({"cmd": "type", "text": text}, "typed")
    assert r.get("cps") == 0
    got, ev = sink.settle()
    assert got == text
    g = gaps(downs(ev))
    assert 12 <= statistics.median(g) <= 45, f"median {statistics.median(g):.1f} ms between characters"


def test_a_pace_of_ten_puts_characters_a_tenth_of_a_second_apart(sink, drive, board, pace):
    pace(10)
    sink.set_mode("text"); sink.clear()
    text = "slow and steady wins"
    drive({"cmd": "type", "text": text}, "typed")
    sit_out(text, 10)
    got, ev = sink.settle(quiet=0.6)
    assert got == text
    g = gaps(downs(ev))
    med = statistics.median(g)
    assert 85 <= med <= 120, f"median {med:.1f} ms between characters at 10 a second"
    assert min(g) >= 70, f"a character came early: {min(g):.1f} ms"
    assert max(g) <= 160, f"a character came late: {max(g):.1f} ms"


def test_the_rhythm_varies_the_gaps_within_their_bounds(sink, drive, board, pace):
    pace(10, rhythm=True)
    sink.set_mode("text"); sink.clear()
    text = "abcdefghijklmnopqrstuvwxyzabcd"
    drive({"cmd": "type", "text": text}, "typed")
    sit_out(text, 10)
    got, ev = sink.settle(quiet=0.6)
    assert got == text
    g = gaps(downs(ev))
    assert statistics.pstdev(g) > 8, f"the gaps do not vary: {g}"
    assert min(g) >= 55 and max(g) <= 175, f"gaps out of bounds (70-130 ms, host jitter aside): {g}"


def test_pause_holds_the_text_and_resume_finishes_it_exactly(sink, drive, board, pace):
    pace(5)
    sink.set_mode("text"); sink.clear()
    text = "paused here"
    drive({"cmd": "type", "text": text}, "typed")
    time.sleep(0.7)
    r = board.cmd({"cmd": "type_pause", "on": True}, "type_pause")
    assert r.get("paused") is True and r.get("left", 0) > 0, r
    time.sleep(0.4)                                   # the character in flight finishes
    n0 = len(downs(sink.events))
    time.sleep(1.5)
    assert len(downs(sink.events)) == n0, "a key went out while paused"
    assert board.status().get("type_paused") is True
    board.cmd({"cmd": "type_pause", "on": False}, "type_pause")
    wait_typed(board, 10)
    got, _ = sink.settle(quiet=0.6)
    assert got == text


def test_stop_leaves_a_prefix_and_no_key_held(sink, drive, board, pace):
    pace(5)
    sink.set_mode("text"); sink.clear()
    text = "0123456789abcdef"
    drive({"cmd": "type", "text": text}, "typed")
    time.sleep(0.9)
    r = board.cmd({"cmd": "type_stop"}, "type_stop")
    assert r.get("dropped", 0) > 0, r
    got, ev = sink.settle(quiet=0.8)
    assert got and text.startswith(got) and got != text, f"typed {got!r}"
    down = [e["code"] for e in ev if e.get("k") == "keydown"]
    up = [e["code"] for e in ev if e.get("k") == "keyup"]
    assert sorted(down) == sorted(up), "a key was left down"
    assert board.status().get("typing") is False


def test_a_shortcut_sent_while_typing_goes_after_the_text(sink, drive, board, pace):
    pace(8)
    sink.set_mode("text"); sink.clear()
    drive({"cmd": "type", "text": "abcdef"}, "typed")
    drive({"cmd": "press", "keys": "BACKSPACE"})
    drive({"cmd": "type", "text": "X"}, "typed")
    wait_typed(board, 10)
    got, _ = sink.settle(quiet=0.6)
    assert got == "abcdeX", f"the shortcut landed inside the text: {got!r}"
