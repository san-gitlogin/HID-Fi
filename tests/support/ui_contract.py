"""The dashboard coverage contract.

Every BDD scenario records two things about the page it drove (support/ui.py):
  - which controls a person touched: an id, or the data-* family a generated
    control belongs to (keys, shortcuts, knobs, list rows)
  - which of the dashboard's JavaScript functions ran, from Chromium's precise
    coverage

Each test process saves its part; when the whole BDD suite has run, the parts
are merged and held against two golden files:

  tests/golden/ui_untouched_allowed.json  controls no scenario has to touch, each
                                          with the reason. Normally empty.
  tests/golden/ui_functions_unrun.json    functions no scenario reaches, each with
                                          the reason. A ratchet: it may only shrink.

So a new button, tile or field without a scenario fails the run, and so does a
new function nothing exercises. A partial run (-k, -m, one file) checks nothing,
because it cannot know what the rest would have touched.
"""
import json
import os
from pathlib import Path

from support import paths

PARTS = paths.REPORTS / "ui_coverage"
MERGED = paths.REPORTS / "ui_coverage.json"
GOLDEN = Path(__file__).resolve().parent.parent / "golden"
ALLOWED = GOLDEN / "ui_untouched_allowed.json"
UNRUN = GOLDEN / "ui_functions_unrun.json"
JS_FLOOR = GOLDEN / "ui_js_coverage.json"
JS_SLACK = 0.5          # timing-dependent branches (a glide's last frame) move it a little
BDD = Path(__file__).resolve().parent.parent / "ui" / "bdd"
KEYS = ("touched", "inventory", "ran", "seen")


def reset():
    PARTS.mkdir(parents=True, exist_ok=True)
    for f in PARTS.glob("*.json"):
        f.unlink()


def _runs(cov):
    """[[start, end), ...] where cov is 1 - compact enough to store per process."""
    out, i, n = [], 0, len(cov)
    while i < n:
        if cov[i]:
            j = i
            while j < n and cov[j]:
                j += 1
            out.append([i, j])
            i = j
        else:
            i += 1
    return out


def save_part(cov):
    PARTS.mkdir(parents=True, exist_ok=True)
    name = os.environ.get("PYTEST_XDIST_WORKER", "main")
    data = {k: sorted(v) if isinstance(v, set) else v for k, v in cov.items() if k != "blocks"}
    data["blocks"] = {str(n): _runs(b) for n, b in cov.get("blocks", {}).items()}
    (PARTS / f"{name}.json").write_text(json.dumps(data))


def merge():
    out = {k: set() for k in KEYS}
    out["scenarios"], out["blocks"] = 0, {}
    for f in sorted(PARTS.glob("*.json")):
        d = json.loads(f.read_text())
        for k in KEYS:
            out[k] |= set(d.get(k, []))
        out["scenarios"] += d.get("scenarios", 0)
        for n, runs in d.get("blocks", {}).items():
            b = out["blocks"].setdefault(int(n), bytearray(int(n)))
            for a, e in runs:
                b[a:e] = b"\x01" * (e - a)
    return out


def _script_and_offset():
    """The dashboard's main <script> text, and the web_ui.h line it starts on."""
    from support import dashboard
    src = dashboard.WEB_UI.read_text(encoding="utf-8")
    html = dashboard.extract_html(src)
    html_line0 = src[:src.index(html)].count("\n")              # 0-based line of html[0]
    i = html.rfind("<script>")
    body = html[i + len("<script>"):html.index("</script>", i)]
    return body, html_line0 + html[:i + len("<script>")].count("\n")


def block_report(blocks):
    """Percent of the page script's code that ran, and the web_ui.h line ranges
    that never did (written to tests/.reports/ui_js_uncovered.txt)."""
    body, line0 = _script_and_offset()
    cov = blocks.get(len(body))
    if cov is None:
        return None, []
    code = [i for i, ch in enumerate(body) if not ch.isspace()]
    hit = sum(cov[i] for i in code)
    pct = round(100.0 * hit / max(1, len(code)), 1)
    # uncovered lines: a line with code where none of its code ran
    lines, pos = [], 0
    for n, text in enumerate(body.split("\n")):
        idx = [pos + k for k, ch in enumerate(text) if not ch.isspace()]
        s = text.strip()
        if idx and not s.startswith("//") and not any(cov[i] for i in idx):
            lines.append((line0 + n + 1, s[:110]))
        pos += len(text) + 1
    return pct, lines


def _full_run(config, ran_files):
    """True only when every BDD file ran, unfiltered."""
    opt = config.option
    if getattr(opt, "keyword", "") or getattr(opt, "lf", False) or getattr(opt, "deselect", None):
        return False, "a -k / --lf / --deselect run"
    missing = sorted({p.name for p in BDD.glob("test_*.py")} - set(ran_files))
    if missing:
        return False, f"{len(missing)} BDD file(s) did not run ({', '.join(missing[:3])})"
    return True, ""


def _load(p, default):
    return json.loads(p.read_text()) if p.exists() else default


def check(config, ran_files):
    """None when there is nothing to judge, else {"failed": bool, "lines": [...]}."""
    if not ran_files:
        return None
    full, why = _full_run(config, ran_files)
    if not full:
        return {"failed": False, "lines": [f"\nui coverage contract: not checked - {why}. Run the whole tier: python tests/run.py --tier ui"]}
    cov = merge()
    MERGED.write_text(json.dumps({k: sorted(v) if isinstance(v, set) else v
                                  for k, v in cov.items() if k != "blocks"}, indent=1))
    pct, dark = block_report(cov["blocks"])
    (paths.REPORTS / "ui_js_uncovered.txt").write_text(
        f"web_ui.h lines whose code no dashboard scenario ran ({len(dark)}):\n\n"
        + "\n".join(f"  web_ui.h:{n:<5d} {s}" for n, s in dark) + "\n")
    allowed = _load(ALLOWED, {})
    unrun_reasons = _load(UNRUN, {})
    unrun_golden = set(unrun_reasons)
    untouched = cov["inventory"] - cov["touched"]
    unrun = cov["seen"] - cov["ran"]
    update = config.getoption("--update-golden")

    problems = []
    for c in sorted(untouched - set(allowed)):
        problems.append(f"  control {c} is on the dashboard but no scenario touches it - add one to tests/ui/bdd/features/")
    for c in sorted(set(allowed) - untouched):
        problems.append(f"  {c} is listed in {ALLOWED.name} but a scenario touches it now - remove it from the list")
    new_unrun = unrun - unrun_golden
    newly_run = unrun_golden - unrun
    if update:
        UNRUN.write_text(json.dumps({f: unrun_reasons.get(f, "TODO: say why no scenario can reach this")
                                     for f in sorted(unrun)}, indent=1) + "\n")
    else:
        for f in sorted(new_unrun):
            problems.append(f"  function {f}() never runs in any scenario - cover the behaviour it implements")
        if newly_run:
            problems.append(f"  {len(newly_run)} function(s) in {UNRUN.name} run now ({', '.join(sorted(newly_run))}) - "
                            f"shrink the list: python tests/run.py --tier ui --update-golden")
    floor = _load(JS_FLOOR, {}).get("percent")
    if pct is None:
        problems.append("  could not match Chromium's coverage to the script in web_ui.h")
    elif update:
        JS_FLOOR.write_text(json.dumps({"percent": pct, "_about": "minimum share of the dashboard script's code "
                                        "that the BDD scenarios run; may only go up"}, indent=1) + "\n")
    elif floor is not None and pct < floor - JS_SLACK:
        problems.append(f"  only {pct}% of the dashboard script ran, the floor is {floor}% - new code without "
                        f"scenarios? See tests/.reports/ui_js_uncovered.txt")
    elif floor is not None and pct > floor + 1.0:
        problems.append(f"  {pct}% of the dashboard script ran, well above the {floor}% floor - raise it: "
                        f"python tests/run.py --tier ui --update-golden")
    head = (f"\nui coverage contract: {cov['scenarios']} scenarios, "
            f"{len(cov['touched'] & cov['inventory'])}/{len(cov['inventory'])} controls touched, "
            f"{len(cov['ran'])}/{len(cov['seen'])} functions ran, {pct}% of the script's code ran"
            f" (uncovered lines: tests/.reports/ui_js_uncovered.txt)")
    if not problems:
        return {"failed": False, "lines": [head + " - OK"]}
    return {"failed": True, "lines": [head + " - FAILED"] + problems}
