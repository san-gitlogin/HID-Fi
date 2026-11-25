"""Text typed at a chosen pace, without stopping the board (hid_fi/typer.h).

A viewer of the demo video: "the typing speed is much fast, can't we control it". The
board typed at a fixed pace (a press, 10 ms, a release, 10 ms - about 50 characters a
second) inside one call that held the pointer and the dashboard until the text was
done. Now the text waits in typer.h and loop() plays one character at a time:

    the board's own pace (0, the default) is exactly the old one, key for key, ms for ms
    a pace of N characters a second makes each character take 1000/N ms - never less
      than the character's own key actions take (a Unicode entry is longer)
    the rhythm option varies each gap by up to 30%, the same way every time
    pause and stop act between characters, never between a key's press and release
    more text while typing waits behind it, in order; Enter is a newline at its end
    too much text is refused, never cut; the clock wrapping round changes nothing

Every rule has a planted bug in firmware/typer_mutations.json that this file catches.
"""
import json
import statistics
import subprocess

import pytest

from support import firmware, paths

pytestmark = pytest.mark.unit
HERE = paths.TESTS / "unit" / "firmware"
LOGIC = paths.TESTS.parent / "hid_fi" / "typer.h"


def build(header, tag="typer_bench"):
    return firmware.build_host(tag, HERE / "typer_bench.cpp", extra_inputs=[header],
                               flags=("-std=c++17", "-O2", f'-DTYPER_H="{header}"'))


@pytest.fixture(scope="module")
def bench():
    try:
        return build(LOGIC)
    except RuntimeError as e:
        pytest.skip(str(e))


def run(exe, scripts):
    r = subprocess.run([str(exe)], input="\n".join(scripts) + "\n", capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"bench exited {r.returncode}")
    return [json.loads(l) for l in r.stdout.splitlines()]


def hx(s):
    return s.encode().hex()


def script(steps, cps=0, rhythm=0, seed=7, start=1000):
    return f"{start} {cps} {rhythm} {seed} " + " ".join(steps)


def presses(r):
    return [(t, k) for t, kind, k in r["ev"] if kind == "P"]


def test_the_boards_own_pace_is_exactly_the_old_one(bench):
    r = run(bench, [script([f"add:0:{hx('abc')}:0", "end:2000"])])[0]
    a, b, c = ord("a"), ord("b"), ord("c")
    assert r["ev"] == [[0, "P", a], [10, "R", a], [20, "P", b], [30, "R", b], [40, "P", c], [50, "R", c]]
    assert r["done"] == 60 and r["typed"] == 3 and r["total"] == 3 and not r["busy"]


def test_a_slower_pace_spaces_each_character(bench):
    r = run(bench, [script([f"add:0:{hx('hello')}:0", "end:5000"], cps=5)])[0]
    starts = [t for t, _ in presses(r)]
    assert starts == [0, 200, 400, 600, 800], starts
    assert all(x[0] - p[0] == 10 for p, x in zip(r["ev"][::2], r["ev"][1::2])), "a key is held as long as before"
    assert r["done"] == 1000


def test_a_character_is_never_faster_than_its_own_keys(bench):
    # 60 cps (16 ms) is faster than a press+release (20 ms): no gap, never shorter
    r = run(bench, [script([f"add:0:{hx('xyz')}:0", "end:2000"], cps=60)])[0]
    assert [t for t, _ in presses(r)] == [0, 20, 40]
    # a Unicode character takes 60 ms of its own; at 20 cps (50 ms) it still takes 60
    r = run(bench, [script([f"add:0:{hx('éa')}:0", "end:2000"], cps=20)])[0]
    assert [t for t, _ in presses(r)] == [0, 60], r["ev"]


def test_the_rhythm_varies_each_gap_within_30_percent_and_the_same_every_time(bench):
    s = script([f"add:0:{hx('a' * 60)}:0", "end:60000"], cps=10, rhythm=1, seed=42)
    r1, r2 = run(bench, [s, s])
    starts = [t for t, _ in presses(r1)]
    periods = [b - a for a, b in zip(starts, starts[1:])]
    assert all(70 <= p <= 130 for p in periods), periods
    assert len(set(periods)) > 10, "it does vary"
    assert 90 <= statistics.mean(periods) <= 110
    assert presses(r1) == presses(r2), "seeded: the same rhythm every time"
    other = run(bench, [script([f"add:0:{hx('a' * 60)}:0", "end:60000"], cps=10, rhythm=1, seed=43)])[0]
    assert presses(other) != presses(r1)


def test_pause_waits_for_the_character_to_finish_and_resume_goes_on(bench):
    r = run(bench, [script([f"add:0:{hx('ab')}:0", "pause:5", "resume:500", "end:2000"])])[0]
    a, b = ord("a"), ord("b")
    assert r["ev"] == [[0, "P", a], [10, "R", a], [500, "P", b], [510, "R", b]], "never left a key down while paused"


def test_a_pause_left_on_never_holds_back_the_next_text(bench):
    """Paused, then everything typed or stopped: the next text starts at once."""
    r = run(bench, [script([f"add:0:{hx('a')}:0", "pause:1", "end:100"])])[0]
    assert [chr(k) for _, k in presses(r)] == ["a"]
    r = run(bench, [script([f"add:0:{hx('ab')}:0", "pause:5", "stop:50", f"add:60:{hx('c')}:0", "end:200"])])[0]
    assert [chr(k) for _, k in presses(r)] == ["a", "c"], "stop clears the pause; a new text is not held"
    r = run(bench, [script([f"add:0:{hx('a')}:0", "pause:30", f"add:40:{hx('b')}:0", "end:200"])])[0]
    assert [chr(k) for _, k in presses(r)] == ["a", "b"], "done, then a new text: not paused"


def test_stop_forgets_the_rest_and_never_reports_done(bench):
    r = run(bench, [script([f"add:0:{hx('abcdef')}:0", "stop:45", "end:2000"])])[0]
    assert [chr(k) for _, k in presses(r)] == ["a", "b", "c"]
    assert r["done"] == -1 and not r["busy"]


def test_more_text_waits_behind_and_enter_is_a_newline_at_its_end(bench):
    r = run(bench, [script([f"add:0:{hx('ab')}:1", f"add:15:{hx('cd')}:0", "end:2000"])])[0]
    assert [chr(k) for _, k in presses(r)] == ["a", "b", "\n", "c", "d"]
    assert r["total"] == 5 and r["typed"] == 5


def test_too_much_text_is_refused_not_cut(bench):
    big = "x" * 4096
    r = run(bench, [script([f"add:0:{hx(big)}:0", f"add:0:{hx('y')}:0", "end:3"])])[0]
    assert [0, "F", 0] in r["ev"], "the second does not fit"
    assert r["total"] == 4096, "and the first is whole"
    # once part is typed, its room is reused
    r = run(bench, [script([f"add:0:{hx(big)}:0", f"add:100:{hx('yz')}:0", "end:200"])])[0]
    assert [100, "F", 0] not in r["ev"]


def test_the_shortcut_marker_never_comes_from_text(bench):
    """The firmware marks a shortcut in the stream with 0x01: text never brings one."""
    r = run(bench, [script([f"adddrop:0:{hx(chr(1) + 'a' + chr(1) + 'b')}:0", "end:500"])])[0]
    assert [chr(k) for _, k in presses(r)] == ["a", "b"] and r["total"] == 2


def test_a_new_pace_applies_to_what_is_still_waiting(bench):
    r = run(bench, [script([f"add:0:{hx('abcd')}:0", "pace:30:2", "end:5000"], cps=0)])[0]
    starts = [t for t, _ in presses(r)]
    assert starts[:2] == [0, 20] and starts[3] - starts[2] == 500, starts


def test_the_clock_wrapping_round_changes_nothing(bench):
    near = 2 ** 32 - 300
    r = run(bench, [script([f"add:0:{hx('abcdef')}:0", "end:3000"], cps=5, start=near)])[0]
    assert [t for t, _ in presses(r)] == [0, 200, 400, 600, 800, 1000]


def test_nothing_queued_does_nothing(bench):
    r = run(bench, [script(["end:500"])])[0]
    assert r["ev"] == [] and r["done"] == -1 and not r["busy"]


@pytest.mark.slow
def test_every_planted_bug_is_caught(tmp_path):
    src = LOGIC.read_text(encoding="utf-8")
    muts = json.loads((HERE / "typer_mutations.json").read_text(encoding="utf-8"))["mutations"]
    scripts = [
        script([f"add:0:{hx('abc')}:0", "end:2000"]),
        script([f"add:0:{hx('hello')}:0", "end:5000"], cps=5),
        script([f"add:0:{hx('éa')}:0", "end:2000"], cps=20),
        script([f"add:0:{hx('a' * 40)}:0", "end:60000"], cps=10, rhythm=1, seed=42),
        script([f"add:0:{hx('ab')}:0", "pause:5", "resume:500", "end:2000"]),
        script([f"add:0:{hx('abcdef')}:0", "stop:45", "end:2000"]),
        script([f"add:0:{hx('ab')}:1", f"add:15:{hx('cd')}:0", "end:2000"]),
        script([f"add:0:{hx('x' * 4096)}:0", f"add:0:{hx('y')}:0", f"add:100:{hx('yz')}:0", "end:300"]),
        script([f"add:0:{hx('x' * 4095)}:0", f"add:0:{hx('yz')}:1", "end:3"]),
        script([f"add:0:{hx('abcd')}:0", "pace:30:2", "end:5000"]),
        script([f"add:0:{hx('abcdef')}:0", "end:3000"], cps=5, start=2 ** 32 - 300),
        script([f"add:0:{hx('xyz')}:0", "end:2000"], cps=60),
        script([f"add:0:{hx('a')}:0", "pause:30", f"add:40:{hx('b')}:0", "end:200"]),
        script([f"adddrop:0:{hx(chr(1) + 'a' + chr(1) + 'b')}:0", "end:500"]),
    ]
    want = run(build(LOGIC), scripts)
    survived = []
    for i, m in enumerate(muts):
        assert src.count(m["find"]) == 1, f"mutation {m['name']!r}: 'find' must match typer.h exactly once"
        mutant = tmp_path / f"m{i}" / "typer.h"
        mutant.parent.mkdir()
        mutant.write_text(src.replace(m["find"], m["replace"]), encoding="utf-8")
        try:
            out = run(build(mutant, tag=f"typer_mutant{i}"), scripts)
        except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError):
            continue
        if out == want:
            survived.append(m["name"])
    assert not survived, f"planted bugs the tests did NOT catch: {survived}"
