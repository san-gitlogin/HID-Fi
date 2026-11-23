"""A key combination pressed without stopping the board (hid_fi/combo.h).

`press` - the dashboard's shortcut buttons, quick actions, knobs and the text tab's
Enter - used to hold loop() for 150 ms, so every pointer frame and ping waited:
measured on 09F7C8 (2026-11-23) the dashboard's ping went from 8 ms to a 96 ms
median with a shortcut five times a second. Now each combination waits its turn in
a queue that loop() advances. The host must see exactly what it saw before:
modifiers alone in a report, 30 ms later the keys, 120 ms later the release - and
combinations in the order they were asked for. Every rule has a planted bug in
firmware/combo_mutations.json that this file must catch.
"""
import json
import subprocess

import pytest

from support import firmware, paths

pytestmark = pytest.mark.unit
HERE = paths.TESTS / "unit" / "firmware"
LOGIC = paths.TESTS.parent / "hid_fi" / "combo.h"


def build(header, tag="combo_bench"):
    return firmware.build_host(tag, HERE / "combo_bench.cpp", extra_inputs=[header],
                               flags=("-std=c++17", "-O2", f'-DCOMBO_H="{header}"'))


@pytest.fixture(scope="module")
def bench():
    try:
        return build(LOGIC)
    except RuntimeError as e:
        pytest.skip(str(e))


def ask(exe, lines):
    r = subprocess.run([str(exe)], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"bench exited {r.returncode}")
    return r.stdout.splitlines()


def test_a_combination_with_modifiers_goes_out_as_it_always_did(bench):
    out = ask(bench, ["push 1 CTRL+C", "tick 1000", "tick 1010", "tick 1029", "tick 1030", "tick 1100", "tick 1149", "tick 1150", "tick 1151"])
    assert out == ["1", "PRESS_MODS CTRL+C", "NONE", "NONE", "PRESS_KEYS CTRL+C", "NONE", "NONE", "RELEASE CTRL+C", "NONE"]


def test_a_single_key_does_not_wait_for_modifiers(bench):
    out = ask(bench, ["push 0 ENTER", "tick 0", "tick 0", "tick 119", "tick 120"])
    assert out == ["1", "PRESS_MODS ENTER", "PRESS_KEYS ENTER", "NONE", "RELEASE ENTER"]


def test_combinations_go_out_in_the_order_asked_one_at_a_time(bench):
    out = ask(bench, ["push 1 CTRL+C", "push 1 CTRL+V", "tick 0", "tick 30", "tick 100", "tick 150", "tick 151", "tick 181", "tick 301"])
    assert out[2:] == ["PRESS_MODS CTRL+C", "PRESS_KEYS CTRL+C", "NONE", "RELEASE CTRL+C",
                       "PRESS_MODS CTRL+V", "PRESS_KEYS CTRL+V", "RELEASE CTRL+V"]


def test_a_full_queue_refuses_so_the_caller_finishes_the_oldest(bench):
    out = ask(bench, [f"push 0 K{i}" for i in range(9)])
    assert out == ["1"] * 8 + ["0"]


def test_text_too_long_is_refused_not_cut(bench):
    assert ask(bench, ["push 1 " + "A+" * 30 + "B"]) == ["0"]


def test_nothing_queued_does_nothing(bench):
    assert ask(bench, ["tick 0", "tick 5000"]) == ["NONE", "NONE"]


def test_the_millisecond_clock_wrapping_round_does_not_stick_a_key(bench):
    out = ask(bench, ["push 1 CTRL+A", "tick 4294967290", "tick 4294967295", "tick 24", "tick 144"])
    assert out[1:] == ["PRESS_MODS CTRL+A", "NONE", "PRESS_KEYS CTRL+A", "RELEASE CTRL+A"]


def test_a_hold_that_crosses_the_clock_wrap_lasts_its_full_time(bench):
    out = ask(bench, ["push 0 ENTER", "tick 4294967290", "tick 4294967290", "tick 4294967295", "tick 113", "tick 114"])
    assert out[1:] == ["PRESS_MODS ENTER", "PRESS_KEYS ENTER", "NONE", "NONE", "RELEASE ENTER"]


def test_the_firmware_queues_press_and_never_waits_in_it():
    """The glue in hid_fi.ino: press is queued, loop() advances it, and anything that
    could type finishes a queued shortcut first."""
    ino = firmware.ino_text()
    assert '#include "combo.h"' in ino
    hp = ino[ino.index("void handlePress(const JsonDocument& inDoc) {"):]
    hp = hp[:hp.index("\n}\n")]
    assert "pressQueued(keys)" in hp and "delay(" not in hp and "pressKeyCombo(" not in hp
    loop = ino[ino.index("void loop() {"):]
    loop = loop[:loop.index("\n}\n")]
    assert "pressTick();" in loop
    pc = ino[ino.index("void processCommand(const String& line, CmdSource src) {"):]
    pc = pc[:pc.index('    if (strcmp(cmd, "ping") == 0) {')]
    assert "if (typing) pressDrain();" in pc and '"press"' in pc
    for typer in ('"type"', '"key_down"', '"macro_run"', '"steps"', '"do"'):
        assert typer not in pc[pc.index("NO_TYPING"):pc.index("};", pc.index("NO_TYPING"))], \
            f"{typer} types: it must wait for a queued shortcut"
    kc = ino[ino.index("void pressKeyCombo(const char* comboStr) {"):]
    kc = kc[:kc.index("\n}\n")]
    assert kc.index("pressDrain();") < kc.index("comboKeys("), "macros and steps go after a queued shortcut"


@pytest.mark.slow
def test_every_planted_bug_is_caught(tmp_path):
    src = LOGIC.read_text(encoding="utf-8")
    muts = json.loads((HERE / "combo_mutations.json").read_text(encoding="utf-8"))["mutations"]
    script = (["push 1 CTRL+C", "push 0 ENTER", "tick 0", "tick 29", "tick 30", "tick 149", "tick 150", "tick 150", "tick 150",
               "tick 269", "tick 270", "tick 271", "reset"]
              + [f"push 0 K{i}" for i in range(9)] + ["reset", "push 1 " + "A+" * 30 + "B", "push 1 " + "A" * 47, "reset",
                 "push 1 CTRL+A", "tick 4294967290", "tick 4294967295", "tick 24", "tick 144", "tick 145", "reset",
                 "push 0 ENTER", "tick 4294967290", "tick 4294967290", "tick 4294967295", "tick 113", "tick 114"])
    want = ask(build(LOGIC), script)
    survived = []
    for i, m in enumerate(muts):
        assert src.count(m["find"]) == 1, f"mutation {m['name']!r}: 'find' must match combo.h exactly once"
        mutant = tmp_path / f"m{i}" / "combo.h"
        mutant.parent.mkdir()
        mutant.write_text(src.replace(m["find"], m["replace"]), encoding="utf-8")
        try:
            out = ask(build(mutant, tag=f"combo_mutant{i}"), script)
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
        if out == want:
            survived.append(m["name"])
    assert not survived, f"planted bugs the tests did NOT catch: {survived}"
