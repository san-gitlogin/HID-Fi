"""Input latency budgets, measured (tests/perf/latency.py does the measuring):
from a finger on the glass to the board's message leaving the page. The send is
on the input path itself - never on the next frame, never behind a redraw - so it
stays under a couple of milliseconds even with the CPU slowed 6x (a 2013 phone),
modern or with the old-browser layer on. A change that puts work, an allocation
or a frame wait in front of the send shows up here.
"""
import statistics
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "perf"))
import latency  # noqa: E402

pytestmark = [pytest.mark.ui, pytest.mark.timing]

BUDGET_MS = 3.0      # median, touch to message


@pytest.mark.parametrize("legacy", [False, True], ids=["modern", "old-browser layer"])
def test_touch_to_message_within_budget_on_a_slow_cpu(browser, legacy):
    # every frame spends 20 ms redrawing first, as a slow device does
    got = latency.run(browser, 6, legacy, n=20, busy=20)
    for action in ("pointer move -> message", "key tap -> message"):
        v = got[action]
        assert len(v) >= 10, f"{action}: only {len(v)} samples"
        assert statistics.median(v) <= BUDGET_MS, f"{action}: median {statistics.median(v):.1f} ms (budget {BUDGET_MS} ms)"
