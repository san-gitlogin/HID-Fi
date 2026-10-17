"""Performance results: a hard budget per metric, plus a per-host baseline ratchet.

Every result is appended to tests/.reports/perf.json. A metric fails when it
breaks its budget (a product requirement), or when it is more than
REGRESSION_FACTOR worse than the committed baseline for this host OS
(golden/perf_baseline.json). Rerun with --update-golden to move a baseline - and
say why in the PR.
"""
import json
import platform
import statistics
import time

from support import paths

REGRESSION_FACTOR = 1.5


def pct(values, p):
    v = sorted(values)
    if not v:
        return float("nan")
    k = (len(v) - 1) * p / 100
    f, c = int(k), min(int(k) + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def summary(values):
    return {"n": len(values), "p50": round(pct(values, 50), 2), "p95": round(pct(values, 95), 2),
            "p99": round(pct(values, 99), 2), "max": round(max(values), 2), "mean": round(statistics.fmean(values), 2)}


class Perf:
    def __init__(self, update):
        self.update = update
        self.host = {"Darwin": "mac", "Windows": "windows", "Linux": "linux"}.get(platform.system(), platform.system())
        self.base_file = paths.GOLDEN / "perf_baseline.json"
        self.base = json.loads(self.base_file.read_text()) if self.base_file.exists() else {}
        self.results = []

    def check(self, name, value, unit, budget, lower_is_better=True, detail=None):
        key = f"{self.host}/{name}"
        rec = {"metric": name, "host": self.host, "value": round(value, 3), "unit": unit, "budget": budget,
               "lower_is_better": lower_is_better, "detail": detail, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        self.results.append(rec)
        problems = []
        if lower_is_better and value > budget:
            problems.append(f"{name} = {value:.2f}{unit} breaks the budget of {budget}{unit}")
        if not lower_is_better and value < budget:
            problems.append(f"{name} = {value:.2f}{unit} is below the required {budget}{unit}")
        base = self.base.get(key)
        if base is None or self.update:
            self.base[key] = round(value, 3)
        else:
            worse = (value > base * REGRESSION_FACTOR) if lower_is_better else (value < base / REGRESSION_FACTOR)
            if worse:
                problems.append(f"{name} = {value:.2f}{unit} regressed from the baseline {base}{unit} "
                                f"(more than {REGRESSION_FACTOR}x worse) - investigate, or --update-golden with a reason")
        assert not problems, "; ".join(problems)

    def save(self):
        paths.REPORTS.mkdir(parents=True, exist_ok=True)
        f = paths.REPORTS / "perf.json"
        old = json.loads(f.read_text()) if f.exists() else []
        f.write_text(json.dumps(old + self.results, indent=1))
        if self.results:
            paths.GOLDEN.mkdir(parents=True, exist_ok=True)
            self.base_file.write_text(json.dumps(dict(sorted(self.base.items())), indent=1) + "\n")
