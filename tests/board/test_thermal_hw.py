"""The chip temperature on a real board, and the owner's rule that watching it costs
nothing (DECISIONS.md D-13: "at any cost should not degrade the performance of the
board and the latency").

    the sensor reads, plausibly, and the four-hour log answers
    every read is cheap: the slowest since start stays far inside the guard's budget,
      and the guard has not tripped
    a watcher polling far harder than tools/thermal_watch.py may (status every 2 s,
      temp_log every 10 s) leaves the dashboard's ping where it was: quiet and loaded
      phases alternate so WiFi drift cannot pass for a difference
    tools/thermal_watch.py runs end to end against the board and logs temperatures

Read-only: nothing on the board is changed. The latency test needs the board on this
computer's network and HIDFI_PIN (from the environment only) when a PIN is set.
"""
import json
import os
import subprocess
import sys
import time

import pytest

from support import paths
from support.board import lan_url
from support.latency_probe import measure, pct, stats

pytestmark = pytest.mark.board
TICK_BUDGET_US = 1500        # thermal.h: a read slower than this is a strike
# The read runs in its own task, which WiFi may pause midway: that wall time costs the
# input path nothing (the ping test below judges the input path), so the worst read is
# held to the guard's own budget, not tighter.


def _status(board):
    return board.cmd({"cmd": "status"}, "status")


def _host(board, request, wait=40):
    """The board's home-network address, once it is back on the network after the open reset it."""
    end = time.time() + wait
    while time.time() < end:
        st = _status(board)
        if st.get("wifi_connected") and st.get("wifi_ip"):
            url = lan_url(request.config.getoption("--board-url"), board)
            return (url or f"ws://{st['wifi_ip']}:81").split("//")[1].split(":")[0]
        time.sleep(2)
    pytest.skip("the board is not on a home network this computer shares")


def test_the_sensor_reads_a_plausible_temperature(board):
    time.sleep(5)                                   # a couple of reads after the open reset
    st = _status(board)
    assert st.get("temp_off") == "", f"the sensor is off: {st.get('temp_off')!r}"
    t, peak = st.get("temp_c"), st.get("temp_peak_c")
    assert t is not None and 10 <= t <= 100, f"temp_c {t}"
    assert peak is not None and peak >= t - 0.5


def test_the_four_hour_log_answers(board):
    r = board.cmd({"cmd": "temp_log"}, "temp_log")
    assert r["status"] == "ok" and r["sensor"] is True and r["period_s"] == 60
    assert r["errors"] <= 1, "the sensor failed more than once since start"
    for v in r["tenths"]:
        assert v is None or 100 <= v <= 1000


def test_the_sensors_reads_stay_inside_the_guards_budget(board):
    time.sleep(12)                                  # at least five reads timed
    st = _status(board)
    assert st.get("temp_off") == "", "the latency guard tripped: reads were slower than the budget"
    assert 0 < st["temp_us_max"] <= TICK_BUDGET_US, \
        f"the slowest read took {st['temp_us_max']} us; three like it in a row switch the sensor off"


@pytest.mark.slow
def test_a_watcher_does_not_slow_the_dashboard(board, request):
    host = _host(board, request)
    pin = request.config.getoption("--board-pin") or ""
    st = _status(board)
    if st.get("auth_set") and not pin:
        pytest.skip("the board has an access PIN: set HIDFI_PIN to measure the dashboard's ping")
    r = measure(host, pin, phases=6, phase_s=25)
    q, l = stats(r["quiet"]), stats(r["loaded"])
    detail = json.dumps({"quiet": q, "loaded": l, "requests": r["load"].get("requests"), "errors": r["load"].get("errors")})
    paths.REPORTS.mkdir(parents=True, exist_ok=True)
    (paths.REPORTS / "thermal_latency.json").write_text(detail, encoding="utf-8")
    assert r["load"].get("errors", 0) == 0, f"the board failed watcher requests: {detail}"
    # WiFi tails swing between identical runs (tests/perf/test_perf.py measured 3x), so the
    # difference is judged on the median, the tail on the product budget.
    assert l["p50"] <= q["p50"] + 2.0, f"a watcher raised the dashboard's median ping: {detail}"
    # The 80 ms tail is the product budget on a usable network. Where the network's own
    # quiet tail is already over it, the tail judges the network, not the watcher: on the
    # Bangalore WiFi (2026-11-25) quiet/loaded p95 read 150/150 and then 137/163 ms with
    # the same medians - swings bigger than any margin. Then the tail is reported as not
    # judged (a skip, never a pass); the median check above has run either way.
    qp95 = pct(r["quiet"], 95)
    if qp95 > 80:
        pytest.skip(f"tail not judged: this network's quiet p95 is {qp95:.0f} ms, over the 80 ms budget "
                    f"(median quiet {q['p50']} ms, under a watcher {l['p50']} ms - checked)")
    assert pct(r["loaded"], 95) <= 80, f"the dashboard's p95 ping left its 80 ms budget under a watcher: {detail}"
    assert _status(board)["temp_off"] == ""


@pytest.mark.slow
def test_the_watcher_tool_logs_temperatures_end_to_end(board, request, tmp_path):
    host = _host(board, request)
    env = dict(os.environ, HIDFI_HOST=host, HIDFI_THERMAL_DIR=str(tmp_path))
    r = subprocess.run([sys.executable, str(paths.TESTS.parent / "tools" / "thermal_watch.py"),
                        "--hours", str(35 / 3600), "--every", "10"], env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    logs = list(tmp_path.glob("*.jsonl"))
    assert len(logs) == 1
    recs = [json.loads(x) for x in logs[0].read_text(encoding="utf-8").splitlines()]
    temps = [x["temp_c"] for x in recs if x.get("temp_c") is not None and x.get("source") != "board_log"]
    assert len(temps) >= 3, f"too few samples: {recs[-3:]}"
    assert all(10 <= t <= 100 for t in temps)
    personal = {"wifi_ssid", "wifi_ip", "mac", "ap_ssid", "sta_target"}
    assert not any(personal & set(x) for x in recs), "a log line carries a network name or address"
