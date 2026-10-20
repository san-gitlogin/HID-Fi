"""How fast the dashboard lays itself out, on the screens people use.

Measured in the page with Chromium's clock, on four profiles - a phone with its
CPU slowed four times (a mid-range Android, roughly), a tablet slowed twice, a
1080p desktop and a 4K display. Each number is the median of several tries.

Two limits apply to every number:
  - a budget a person would notice (100 ms feels instant; a rotation may take
    a little longer; load includes connecting to the board), and
  - a baseline per OS in tests/golden/ui_responsive_perf.<os>.json: slower than
    REGRESSION x baseline (and by more than NOISE_MS) fails, so a change that
    makes the layout twice as expensive is caught long before it is noticed.

The table is written to tests/.reports/responsive_perf.md.
"""
import json
import platform
import statistics

import pytest

from support import paths, responsive as R, responsive_run as RR

# One profile at a time, never alongside each other: they share the GPU.
# timing: run.py runs these after the parallel phase, alone - with three other
# workers driving Chromium they measured 2-2.6x slower and failed on load alone.
pytestmark = [pytest.mark.ui, pytest.mark.timing, pytest.mark.xdist_group("ui-perf")]

PROFILES = {
    "phone (4x slower CPU)": (R.Viewport("iPhone 14", 390, 844, True, 3, (47, 0, 34, 0)), 4),
    "tablet (2x slower CPU)": (R.Viewport("iPad Air", 820, 1180, True, 2), 2),
    "desktop 1080p": (R.Viewport("1080p", 1920, 1080), 1),
    "display 4K": (R.Viewport("4K", 3840, 2160), 1),
}
# Main-thread work (script + style + layout, as Chromium accounts it), in ms. It
# barely moves when other tests share the machine, where wall time can triple.
BUDGET_MS = {"load (work)": 400, "tab switch, slowest (work)": 60, "keyboard deck (work)": 60,
             "fullscreen pad (work)": 80, "sheet (work)": 60, "rotate (work)": 120, "all tabs (work)": 300}
CLS_BUDGET = 0.1
REGRESSION, NOISE_MS = 2.0, 20
TRIES = 5
BASE = paths.GOLDEN / f"ui_responsive_perf.{platform.system().lower()}.json"

PAINTED = "() => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)))"
TIME = """async (code) => {
  const t = performance.now();
  (new Function(code))();
  await new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
  return performance.now() - t;
}"""


WORK = ("ScriptDuration", "LayoutDuration", "RecalcStyleDuration")


def _metrics(cdp):
    return {x["name"]: x["value"] for x in cdp.send("Performance.getMetrics")["metrics"]}


def _work(m0, m1):
    return 1000 * sum(m1[k] - m0[k] for k in WORK)


def _measure(browser, vp, slow):
    runs, wall, cls, fcp = {k: [] for k in BUDGET_MS}, {}, [], []
    for _ in range(TRIES):
        ctx, page, sim, errs = RR.open_page(browser, vp)
        cdp = ctx.new_cdp_session(page)
        cdp.send("Performance.enable")
        try:
            cdp.send("Emulation.setCPUThrottlingRate", {"rate": slow})     # as slow as the device, from here on

            def act(key, code):
                m0 = _metrics(cdp)
                w = page.evaluate(TIME, code)
                runs[key].append(_work(m0, _metrics(cdp)))
                wall.setdefault(key.replace("(work)", "(wall)"), []).append(w)

            m0 = _metrics(cdp)
            page.reload()
            page.wait_for_function("() => document.getElementById('ltext').textContent === 'live'", timeout=15000)
            page.evaluate(PAINTED)
            runs["load (work)"].append(_work(m0, _metrics(cdp)))
            wall.setdefault("load to connected (wall)", []).append(page.evaluate("performance.now()"))
            p = page.evaluate("() => (performance.getEntriesByName('first-contentful-paint')[0] || {}).startTime ?? null")
            if p:
                fcp.append(p)
            cls.append(page.evaluate("""() => new Promise(r => { let s = 0;
                new PerformanceObserver(l => l.getEntries().forEach(e => { if (!e.hadRecentInput) s += e.value; }))
                  .observe({type: 'layout-shift', buffered: true});
                setTimeout(() => r(s), 50); })"""))

            m0 = _metrics(cdp)
            per = []
            for v in RR.VIEWS:
                a = _metrics(cdp)
                page.evaluate(TIME, f"go('{v}')")
                per.append(_work(a, _metrics(cdp)))
            runs["tab switch, slowest (work)"].append(max(per))
            runs["all tabs (work)"].append(_work(m0, _metrics(cdp)))
            page.evaluate("go('pad')")
            act("keyboard deck (work)", "$('btnDeck').click()")
            page.evaluate(TIME, "$('btnDeck').click()")
            act("fullscreen pad (work)", "$('btnFs').click()")
            page.evaluate(TIME, "$('btnFsExitTop').click()")
            act("sheet (work)", "showHelp()")
            page.evaluate("closeSheet()")
            m0 = _metrics(cdp)
            page.set_viewport_size({"width": vp.h, "height": vp.w})
            page.evaluate(PAINTED)
            runs["rotate (work)"].append(_work(m0, _metrics(cdp)))
            page.set_viewport_size({"width": vp.w, "height": vp.h})
        finally:
            ctx.close()
    out = {k: round(statistics.median(v), 1) for k, v in runs.items()}
    out.update({k: round(statistics.median(v), 1) for k, v in wall.items()})
    out["first paint (wall)"] = round(statistics.median(fcp), 1) if fcp else "n/a"
    out["layout shift on load"] = round(max(cls), 3)
    return out


def _report(results):
    keys = list(BUDGET_MS) + sorted({k for r in results.values() for k in r if "(wall)" in k}) + ["layout shift on load"]
    md = ["# Dashboard layout performance", "",
          f"Median of {TRIES} tries. Budgets apply to main-thread work: " +
          ", ".join(f"{k} {v} ms" for k, v in BUDGET_MS.items()) + f", layout shift {CLS_BUDGET}. "
          "Wall times are shown for reference; they move with whatever else the machine is doing.", "",
          "| | " + " | ".join(results) + " |", "|---|" + "---|" * len(results)]
    for k in keys:
        md.append(f"| {k} | " + " | ".join(str(r.get(k, "")) for r in results.values()) + " |")
    (paths.REPORTS / "responsive_perf.md").write_text("\n".join(md) + "\n")


@pytest.mark.parametrize("profile", list(PROFILES))
def test_layout_is_fast_enough(browser, profile, request):
    vp, slow = PROFILES[profile]
    got = _measure(browser, vp, slow)
    part = paths.REPORTS / "responsive_perf"
    part.mkdir(parents=True, exist_ok=True)
    (part / f"{profile.split()[0]}.json").write_text(json.dumps({profile: got}))
    _report({k: v for f in sorted(part.glob("*.json")) for k, v in json.loads(f.read_text()).items()})

    problems = [f"{k}: {got[k]} ms, budget {b} ms" for k, b in BUDGET_MS.items() if got[k] > b]
    if got["layout shift on load"] > CLS_BUDGET:
        problems.append(f"layout shift on load {got['layout shift on load']}, budget {CLS_BUDGET}")
    base = json.loads(BASE.read_text()) if BASE.exists() else {}
    if request.config.getoption("--update-golden") or profile not in base:
        base[profile] = got
        BASE.write_text(json.dumps(base, indent=1, sort_keys=True) + "\n")
    else:
        for k, b in base[profile].items():
            if k in BUDGET_MS and got[k] > b * REGRESSION and got[k] - b > NOISE_MS:
                problems.append(f"{k}: {got[k]} ms, was {b} ms - more than {REGRESSION}x slower")
    assert not problems, f"{profile}:\n  " + "\n  ".join(problems)
