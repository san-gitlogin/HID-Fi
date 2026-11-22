"""tools/thermal_watch.py: what it makes of hours of temperatures, on synthetic boards.

A person takes the board out of its shell and puts it back whenever they like and
tells nobody, so the tool must find those moments itself - and must not blame the
shell for what the board's own load did (the radio waking, a restart, a dashboard
left open). The curves here are a first-order heat model (rise = step x (1 - e^-t/tau))
with sensor noise, the shape a small shell gives.
"""
import math
import random
import re
import sys

import pytest

from support import paths

sys.path.insert(0, str(paths.TESTS.parent / "tools"))
import thermal_watch as W  # noqa: E402

pytestmark = pytest.mark.unit
EVERY = 30
IDLE = {"radio_awake": False, "ap_running": True, "ap_clients": 0, "ws_clients": 0,
        "power_mode": "balanced", "firmware": "hid_fi_v4.3.2"}


def board(minutes, base=46.0, steps=(), load=(), restarts=(), noise=0.3, seed=1, tau=12.0):
    """steps: (minute, delta) heat steps; load: (minute, {field: value}); restarts: minutes."""
    rnd, out, up, state = random.Random(seed), [], 600, dict(IDLE)
    for i in range(int(minutes * 60 / EVERY)):
        t = i * EVERY
        m = t / 60
        for lm, change in load:
            if abs(m - lm) < 1e-9:
                state.update(change)
        if any(abs(m - r) < 1e-9 for r in restarts):
            up = 0
        temp = base
        for sm, d in steps:
            if m >= sm:
                temp += d * (1 - math.exp(-(m - sm) / tau))
        temp += rnd.uniform(-noise, noise)
        out.append({"t": 1_700_000_000 + t, "temp_c": round(temp, 1), "uptime_sec": up, "reset_reason": "power_on", **state})
        up += EVERY
    return out


def shifts(samples):
    return [e for e in W.events(samples) if e["kind"] == "shift"]


def test_a_steady_board_has_no_events():
    assert W.events(board(240)) == []


def test_noise_and_a_slow_drift_are_not_events():
    assert shifts(board(240, noise=0.8, seed=7)) == []
    assert shifts(board(240, steps=[(30, 1.5)], tau=60)) == []


def test_putting_it_in_a_shell_is_found_without_anyone_saying_so():
    ev = shifts(board(180, steps=[(60, 8.0)]))
    assert len(ev) == 1 and ev[0]["delta"] > W.SHIFT_C
    assert ev[0]["guess"] == "likely put in a shell or covered" and "because" not in ev[0]
    assert 1_700_000_000 + 50 * 60 <= ev[0]["t"] <= 1_700_000_000 + 75 * 60, "found near when it happened"


def test_taking_it_out_is_found_the_other_way():
    ev = shifts(board(180, base=56.0, steps=[(60, -9.0)]))
    assert len(ev) == 1 and ev[0]["guess"] == "likely taken out or uncovered"


def test_in_and_out_again_are_two_events():
    ev = shifts(board(300, steps=[(60, 8.0), (180, -8.0)]))
    assert [e["guess"] for e in ev] == ["likely put in a shell or covered", "likely taken out or uncovered"]


def test_the_radio_waking_is_not_blamed_on_the_shell():
    ev = shifts(board(180, steps=[(60, 6.0)], load=[(60, {"radio_awake": True})]))
    assert len(ev) == 1 and "guess" not in ev[0] and "the radio woke" in ev[0]["because"]


def test_a_dashboard_left_open_is_named():
    ev = W.events(board(180, steps=[(60, 6.0)], load=[(60, {"ws_clients": 1})]))
    assert any(e["kind"] == "load" and e["what"] == "a dashboard opened" for e in ev)
    assert all("guess" not in e for e in ev if e["kind"] == "shift")


def test_a_restart_is_an_event_and_explains_the_cool_down():
    ev = W.events(board(180, base=58.0, steps=[(60, -10.0)], restarts=[60]))
    assert any(e["kind"] == "restart" for e in ev)
    sh = [e for e in ev if e["kind"] == "shift"]
    assert len(sh) == 1 and "restarted" in sh[0]["because"]


def test_the_boards_own_log_fills_the_time_nobody_watched():
    reply = {"reply": "temp_log", "period_s": 60, "uptime_sec": 3700, "last_s": 3660,
             "tenths": [450, None, 470]}
    pts = W.backfill(reply, read_at=10_000.0)
    # the newest point is the minute that ended 40 s before the read, the gap is skipped
    assert [p["temp_c"] for p in pts] == [45.0, 47.0]
    assert pts[-1]["t"] == pytest.approx(10_000 - 40 - 30)
    assert pts[0]["t"] == pytest.approx(10_000 - 40 - 150)
    assert all(p["source"] == "board_log" for p in pts)


def test_points_from_the_boards_log_carry_no_load_so_they_explain_nothing():
    a = {"t": 1, "temp_c": 45.0, "source": "board_log"}
    b = {"t": 2, "temp_c": 45.0, "uptime_sec": 10, **IDLE}
    assert W.signature(a) is None and W.load_change(W.signature(a), W.signature(b)) == ""


def test_a_log_line_holds_nothing_personal():
    """No network names, addresses or anything a person typed (the owner's board holds
    personal data; tests/unit/test_board_report.py has the same rule for reports)."""
    personal = {"wifi_ssid", "sta_target", "wifi_ip", "sta_last_ip", "ap_ssid", "mac", "ap_mac", "mdns", "wifi_preferred"}
    assert not personal & set(W.KEEP_KEYS)


def test_summary_reports_the_range_and_the_peak():
    s = board(60, steps=[(10, 5.0)])
    s[-1]["temp_peak_c"] = 60.2
    sm = W.summary(s)
    assert sm["samples"] == len(s) and sm["peak_c"] == 60.2 and sm["min_c"] <= sm["mean_c"] <= sm["max_c"]


QUIET = {"ws_clients": 0, "ota_step": "idle"}


@pytest.mark.parametrize("every, status, ms, cur, want", [
    (30, QUIET, 40, 30, 30),                                   # the asked-for pace
    (2, QUIET, 40, 2, W.MIN_EVERY_S),                          # never faster than the floor
    (30, {"ws_clients": 1, "ota_step": "idle"}, 40, 30, 60),   # someone is using it: a minute
    (30, {"ws_clients": 0, "ota_step": "downloading"}, 40, 30, 60),   # an update: a minute
    (30, QUIET, 900, 30, 60),                                  # answering slowly: double...
    (30, QUIET, 900, 60, 120),                                 # ...and again...
    (30, QUIET, 900, 300, 300),                                # ...up to five minutes
    (30, QUIET, 40, 300, 30),                                  # fast again: back to the asked pace
    (30, None, None, 30, 60),                                  # out of reach: back off too
])
def test_the_watcher_backs_off_whenever_the_board_has_better_things_to_do(every, status, ms, cur, want):
    assert W.next_interval(every, status, ms, cur) == want


def test_the_watcher_never_opens_a_websocket_or_sends_input():
    """A socket holds the radio awake and takes one of eight dashboard slots; the watcher
    reads /api/status over HTTP and, with a PIN, temp_log - nothing else."""
    src = (paths.TESTS.parent / "tools" / "thermal_watch.py").read_text(encoding="utf-8")
    assert "websocket" not in src.lower().replace("never a websocket", "").replace("not a websocket", "")
    assert set(re.findall(r'"cmd": "(\w+)"', src)) == {"temp_log"}


@pytest.mark.parametrize("argv", [["--hours", "0"], ["--every", "1"], ["--every", "9"]])
def test_a_watch_always_has_an_end(argv):
    assert W.main(argv) == 2
