"""How hot the chip runs (hid_fi/thermal.h), compiled on this computer and run: the same
header the firmware's thermalTick() feeds from the ESP32-S3's die sensor. It reports
`temp_c` and `temp_peak_c` in status and a four-hour, one-point-a-minute log through
`temp_log`, so a person can see whether a shell runs hot and a tool can read back what
happened while nobody was watching (the first printed Core ran hot after 4-5 idle hours
and nothing on the board could say how hot). Every rule has a planted bug in
firmware/thermal_mutations.json that this file must catch.
"""
import json
import re
import subprocess

import pytest

from support import firmware, paths

pytestmark = pytest.mark.unit
HERE = paths.TESTS / "unit" / "firmware"
LOGIC = paths.TESTS.parent / "hid_fi" / "thermal.h"
LOG_LEN, PERIOD = 240, 60


def build(header, tag="thermal_bench"):
    return firmware.build_host(tag, HERE / "thermal_bench.cpp", extra_inputs=[header],
                               flags=("-std=c++17", "-O2", f'-DTHERMAL_H="{header}"'))


@pytest.fixture(scope="module")
def bench():
    try:
        return build(LOGIC)
    except RuntimeError as e:
        pytest.skip(str(e))


def ask(exe, lines):
    r = subprocess.run([str(exe)], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"bench exited {r.returncode}")
    return r.stdout.splitlines()


def state(line):
    now, peak, errors = line.split()
    num = lambda v: None if v == "-" else int(v)
    return num(now), num(peak), int(errors)


def log_of(line):
    parts = line.split()
    return int(parts[0]), int(parts[1]), [None if v == "-" else int(v) for v in parts[2:]]


def r(c, t, ok=1):
    return f"r {c} {ok} {t}"


def test_a_reading_is_kept_in_tenths_rounded_not_cut(bench):
    out = ask(bench, [r(47.26, 0), r(47.24, 2), r(-3.26, 4)])
    assert [state(o)[0] for o in out] == [473, 472, -33]


def test_the_peak_is_the_hottest_reading_since_start(bench):
    out = ask(bench, [r(50.0, 0), r(61.5, 2), r(55.0, 4), r(49.0, 6)])
    assert [state(o)[:2] for o in out] == [(500, 500), (615, 615), (550, 615), (490, 615)]


def test_each_minute_logs_the_mean_of_its_readings(bench):
    # minute 0: 30 readings of 40.0 and 50.0 alternating -> 45.0; minute 1: all 60.0
    lines = [r(40.0 if i % 2 == 0 else 50.0, 2 * i) for i in range(30)]
    lines += [r(60.0, PERIOD + 2 * i) for i in range(30)] + [r(60.0, 2 * PERIOD), "log"]
    n, last, vals = log_of(ask(bench, lines)[-1])
    assert (n, vals) == (2, [450, 600])
    assert last == 2 * PERIOD, "the newest point ended when its minute closed"


def test_a_minute_the_sensor_failed_is_a_gap_not_the_last_value(bench):
    lines = [r(45.0, 0), r(45.0, 30)]
    lines += [r("nan", PERIOD + 2 * i, ok=0) for i in range(30)]           # a whole minute of failed reads
    lines += [r(47.0, 2 * PERIOD), r(47.0, 3 * PERIOD), "log"]
    _, _, vals = log_of(ask(bench, lines)[-1])
    assert vals == [450, None, 470]


def test_a_minute_with_only_a_failed_read_is_a_gap(bench):
    # one failed read is not enough to stop showing "now", but that minute measured nothing
    _, _, vals = log_of(ask(bench, [r(45.0, 0), r(0, 62, ok=0), r(46.0, 121), "log"])[-1])
    assert vals == [450, None]


def test_three_failed_reads_in_a_row_stop_showing_now_but_keep_the_peak(bench):
    out = ask(bench, [r(52.0, 0), r(0, 2, ok=0), r(0, 4, ok=0)])
    assert state(out[-1]) == (520, 520, 2), "two failures in a row still show the last good reading"
    out = ask(bench, [r(52.0, 0), r(0, 2, ok=0), r(0, 4, ok=0), r(0, 6, ok=0)])
    assert state(out[-1]) == (None, 520, 3)


def test_a_good_read_between_failures_starts_the_count_again(bench):
    out = ask(bench, [r(52.0, 0), r(0, 2, ok=0), r(53.0, 4), r(0, 6, ok=0), r(0, 8, ok=0)])
    assert state(out[-1]) == (530, 530, 3)


@pytest.mark.parametrize("bad", ["nan", "200", "-55", "125.1"])
def test_an_impossible_value_is_a_failed_read_never_a_peak(bench, bad):
    out = ask(bench, [r(44.0, 0), r(bad, 2, ok=1)])
    assert state(out[-1]) == (440, 440, 1)


def test_the_driver_saying_no_is_believed_even_with_a_plausible_value(bench):
    out = ask(bench, [r(44.0, 0), r(70.0, 2, ok=0)])
    assert state(out[-1]) == (440, 440, 1)


def test_the_log_keeps_the_last_four_hours(bench):
    lines = [r(20.0 + m / 10, m * PERIOD) for m in range(LOG_LEN + 30 + 1)] + ["log"]
    n, last, vals = log_of(ask(bench, lines)[-1])
    assert n == LOG_LEN
    assert vals[0] == round((20.0 + 30 / 10) * 10) and vals[-1] == round((20.0 + (LOG_LEN + 29) / 10) * 10), \
        "oldest first, the 30 oldest minutes dropped"
    assert last == (LOG_LEN + 30) * PERIOD


def test_a_long_stall_logs_one_gap_per_minute_and_carries_on(bench):
    lines = [r(45.0, 0), r(46.0, 10 * PERIOD + 5), r(46.0, 11 * PERIOD), "log"]
    n, _, vals = log_of(ask(bench, lines)[-1])
    assert vals == [450] + [None] * 9 + [460]


def test_a_stall_longer_than_the_log_fills_it_once(bench):
    lines = [r(45.0, 0), r(46.0, (LOG_LEN * 3) * PERIOD), "log"]
    n, _, vals = log_of(ask(bench, lines)[-1])
    assert n == LOG_LEN and all(v is None for v in vals)


@pytest.mark.parametrize("band, deci, want", [
    (0, 450, 0), (0, 749, 0), (0, 750, 1), (0, 900, 1),      # low band: up only near its top (80 °C)
    (1, 900, 1), (1, 551, 1), (1, 550, 0), (1, 300, 0),      # high band: down only well inside the low one
    (0, -32768, 0), (1, -32768, 1),                          # no reading: stay where it is
])
def test_a_hot_chip_is_read_in_the_high_band_and_a_cool_one_comes_back(bench, band, deci, want):
    """The sensor saturates outside its band, so a die near 80 °C must move to 50..125 °C
    or it reads cooler than it is; the 20 °C gap between the switch points stops flapping."""
    assert ask(bench, [f"band {band} {deci}"]) == [str(want)]


def test_fast_reads_keep_the_sensor_on_and_the_worst_is_kept(bench):
    out = ask(bench, ["tick 40", "tick 1500", "tick 900", "tick 35"])
    assert out == ["1 0 40", "1 0 1500", "1 0 1500", "1 0 1500"], "the budget itself is not a strike"


def test_a_read_paused_now_and_then_is_not_a_fault(bench):
    """The task can be paused by WiFi mid-read: sporadic slow reads never switch it off."""
    out = ask(bench, ["tick 2000", "tick 40", "tick 1600", "tick 2400", "tick 40"] * 5)
    assert all(o.split()[0] == "1" for o in out)
    assert out[-1].split() == ["1", "0", "2400"]


def test_three_slow_reads_in_a_row_switch_the_sensor_off_for_good(bench):
    out = ask(bench, ["tick 40", "tick 2000", "tick 1600", "tick 5000", "tick 40", "tick 40"])
    assert [o.split()[0] for o in out] == ["1", "1", "1", "0", "0", "0"], "off stays off"
    assert out[-1].split()[2] == "5000"


def test_the_firmware_feeds_the_sensor_into_this_header():
    """The glue in hid_fi.ino: the IDF driver read by its own task on core 0, never from
    loop() (core 1, the input path), timed and guarded, and copied out under a lock."""
    ino = firmware.ino_text()
    assert '#include "thermal.h"' in ino and '#include "driver/temperature_sensor.h"' in ino
    assert "temperature_sensor_install(" in ino and "temperature_sensor_get_celsius(" in ino
    task = ino[ino.index("static void thermalTask(void*)"):]
    task = task[:task.index("\n}\n")]
    assert "thermal::reading(" in task and "vTaskDelay(pdMS_TO_TICKS(thermal::SAMPLE_MS))" in task
    assert "thermal::guardTick(thermalGuard, us)" in task and "micros() - t0" in task, \
        "every read is timed, and the guard can stop it for good (no latency at any cost)"
    assert "thermal::nextBand(" in task and "TEMPERATURE_SENSOR_CONFIG_DEFAULT(50, 125)" in ino, \
        "a hot chip must be read in the high band (thermal::nextBand)"
    assert 'xTaskCreatePinnedToCore(thermalTask, "thermal",' in ino and ", 1, nullptr, 0);" in ino, \
        "its own task, lowest working priority, on core 0 - away from loop() on core 1"
    loop = ino[ino.index("void loop() {"):]
    loop = loop[:loop.index("\n}\n")]
    assert "temperature_sensor" not in loop and "thermal" not in loop, "loop() - the input path - never reads the sensor"
    # whatever reads the task's state copies it under the lock, briefly
    for fn in ("static void thermalSnapshot(", "void handleTempLog() {"):
        body = ino[ino.index(fn):]
        body = body[:body.index("\n}\n")]
        assert "portENTER_CRITICAL(&thermalMux)" in body and "portEXIT_CRITICAL(&thermalMux)" in body
        crit = body[body.index("portENTER_CRITICAL"):body.index("portEXIT_CRITICAL")]
        assert "sendJson" not in crit and "temperature_sensor" not in crit, "nothing slow inside the lock"
    for f in ('doc["temp_c"]', 'doc["temp_peak_c"]', 'doc["temp_us_max"]', 'doc["temp_off"]'):
        assert f in ino, f"status must carry {f}"
    assert 'strcmp(cmd, "temp_log")' in ino


@pytest.mark.slow
def test_every_planted_bug_is_caught(tmp_path):
    """Mutation testing over thermal.h: each deliberate bug must change what this file sees."""
    src = LOGIC.read_text(encoding="utf-8")
    muts = json.loads((HERE / "thermal_mutations.json").read_text(encoding="utf-8"))["mutations"]
    scenarios = [
        [r(47.26, 0), r(61.5, 2), r(40.0, 4), r(0, 6, ok=0), r(55.0, 8), r(0, 10, ok=0), r(0, 12, ok=0),
         r(0, 14, ok=0), r("nan", 16, ok=1), r(200, 18), r(70.0, 20, ok=0), "log"],
        [r(45.0, 0), r(45.0, 30)] + [r("nan", PERIOD + 2 * i, ok=0) for i in range(30)]
        + [r(40.0 if i % 2 else 50.0, 2 * PERIOD + 2 * i) for i in range(30)] + [r(47.0, 3 * PERIOD), "log"],
        [r(45.0, 0), r(0, 62, ok=0), r(46.0, 121), "log"],
        [r(52.0, 0), r(0, 2, ok=0), r(53.0, 4), r(0, 6, ok=0), r(0, 8, ok=0)],
        [r(20.0 + m / 10, m * PERIOD) for m in range(LOG_LEN + 6)] + ["log"],
        [r(45.0, 0), r(46.0, 10 * PERIOD + 5), r(46.0, 11 * PERIOD), "log"],
        [r(45.0, 0), r(46.0, (LOG_LEN * 3) * PERIOD), "log"],
    ]
    scenarios.append([f"tick {u}" for u in (40, 1500, 1501, 40, 1600, 1700, 40, 1600, 1700, 9000, 40)])
    scenarios.append([f"band {b} {d}" for b, d in [(0, 749), (0, 750), (1, 551), (1, 550), (0, -32768), (1, -32768)]])
    script = [line for sc in scenarios for line in [*sc, "reset"]]
    want = ask(build(LOGIC), script)
    finds = [m["find"] for m in muts]
    assert len(set(finds)) == len(finds), "two planted bugs edit the same text - one of them is not tested"
    survived = []
    for i, m in enumerate(muts):
        assert m["find"] != m["replace"], f"{m['name']!r} changes nothing"
        assert src.count(m["find"]) == 1, f"mutation {m['name']!r}: 'find' must match thermal.h exactly once"
        mutant = tmp_path / f"m{i}" / "thermal.h"
        mutant.parent.mkdir()
        mutant.write_text(src.replace(m["find"], m["replace"]), encoding="utf-8")
        try:
            out = ask(build(mutant, tag=f"thermal_mutant{i}"), script)
        except (RuntimeError, subprocess.TimeoutExpired):
            continue                      # does not build, crashes or hangs: caught
        if out == want:
            survived.append(m["name"])
    assert not survived, f"planted bugs the tests did NOT catch: {survived}"
