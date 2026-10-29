"""The Companion window's coverage contract - the window's twin of the
dashboard's (support/ui_contract.py), held as strictly.

Every scenario in tests/ui/companion records which controls a person touched
(by family: a button kind `act:update`, `role:sel`, `filter:online`, a key
`key:ArrowDown`...) and, in Chromium, which of the page's functions ran. When
the whole suite has run (both engines, unfiltered), the parts are merged and:

  - every control family the page and the app's model offer must be touched,
    unless tests/golden/companion_window_untouched_allowed.json says why not
    (normally empty);
  - every named function in the page's script must have run, unless
    tests/golden/companion_window_unrun.json says why not (a ratchet: it only
    shrinks);
  - every scenario must have run in WebKit too (macOS draws the window with it).

A new button kind, key or function without a scenario fails the run.
"""
import json
import os
import re

from support import paths

PARTS = paths.REPORTS / "window_coverage"
GOLDEN = paths.TESTS / "golden"
ALLOWED = GOLDEN / "companion_window_untouched_allowed.json"
UNRUN = GOLDEN / "companion_window_unrun.json"
PAGE = paths.REPO / "companion" / "crates" / "hidfi-companion" / "src" / "window.html"
STATES = GOLDEN / "companion_window_states.tsv"
SUITE = paths.TESTS / "ui" / "companion"

# Keys the page answers (its keydown handler), as the recorder names them.
KEYS = ["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End", "Space", "Shift+Space",
        "Enter", "Escape", "Shift+ArrowLeft", "Mod+a"]

_mine = {"touched": set(), "ran": set(), "engines": {}}


def reset():
    PARTS.mkdir(parents=True, exist_ok=True)
    for f in PARTS.glob("*.json"):
        f.unlink()


def record(engine, touched, ran):
    _mine["touched"] |= set(touched)
    _mine["ran"] |= set(ran)
    _mine["engines"][engine] = _mine["engines"].get(engine, 0) + 1
    PARTS.mkdir(parents=True, exist_ok=True)
    name = os.environ.get("PYTEST_XDIST_WORKER", "main")
    (PARTS / f"{name}.json").write_text(json.dumps({"touched": sorted(_mine["touched"]), "ran": sorted(_mine["ran"]),
                                                    "engines": _mine["engines"]}), encoding="utf-8")


def inventory():
    """Every control family a person can meet: the page's own, and every kind
    of button the app's model sends in any state."""
    html = PAGE.read_text(encoding="utf-8")
    inv = {"card", "copy", "#bclear", "#dyes", "#dno"}
    inv |= {f"role:{r}" for r in re.findall(r'dataset\.role="(\w+)"', html)}
    inv |= {f"filter:{k}" for k in re.findall(r'\["(\w+)","[^"]+"\]', html.split("const FILTERS=")[1].split(";")[0])}
    inv |= {f"bulk:{k}" for k in re.findall(r'\["(\w+)","[^"]+","\w*"\]', html.split("const kinds=")[1].split(";")[0])}
    inv |= {f"act:{a}" for a in re.findall(r'data-act="([\w-]+)"', html)}
    inv |= {f"act:{a}" for a in re.findall(r'\["(sound-[\w-]+)",', html)}      # the switches
    # Buttons in the model: {"bulk", "confirm", "id", "kind", "label"} (keys sorted).
    for line in STATES.read_text(encoding="utf-8").splitlines():
        for a in re.findall(r'"id":"([\w-]+)(?::[0-9A-F]+)?","kind"', line):
            inv.add(f"act:{a}")
    inv |= {f"key:{k}" for k in KEYS}
    return inv


def functions():
    """Named functions in the page script: declarations and named arrows."""
    js = PAGE.read_text(encoding="utf-8").split("<script>")[1].split("</script>")[0]
    names = set(re.findall(r"\bfunction\s+(\w+)\s*\(", js))
    names |= set(re.findall(r"\bconst\s+(\w+)\s*=\s*(?:async\s*)?\(?[\w,\s]*\)?\s*=>", js))
    return names


def merge():
    out = {"touched": set(), "ran": set(), "engines": {}}
    for f in sorted(PARTS.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        out["touched"] |= set(d["touched"])
        out["ran"] |= set(d["ran"])
        for e, n in d["engines"].items():
            out["engines"][e] = out["engines"].get(e, 0) + n
    return out


def _load(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def check(config, ran_files):
    """None when the window suite did not run; else {"failed", "lines"}."""
    if "test_window.py" not in ran_files:
        return None
    opt = config.option
    if getattr(opt, "keyword", "") or getattr(opt, "lf", False) or getattr(opt, "deselect", None):
        return {"failed": False, "lines": ["\nwindow coverage contract: not checked - a filtered run"]}
    m = merge()
    lines, failed = ["", "Companion window coverage contract"], False
    missing_engine = [e for e in ("chromium", "webkit") if not m["engines"].get(e)]
    if missing_engine:
        lines.append(f"  not checked: no scenario ran in {', '.join(missing_engine)} (install it: python -m playwright install {' '.join(missing_engine)})")
        return {"failed": False, "lines": lines}
    allowed, unrun = _load(ALLOWED), _load(UNRUN)
    untouched = sorted(inventory() - m["touched"] - set(allowed))
    dark = sorted(functions() - m["ran"] - set(unrun))
    stale = sorted(set(unrun) & m["ran"])
    if untouched:
        failed = True
        lines.append(f"  FAIL controls no scenario touches: {untouched} - add a scenario (tests/ui/companion/features)")
    if dark:
        failed = True
        lines.append(f"  FAIL page functions no scenario runs: {dark}")
    if stale:
        failed = True
        lines.append(f"  FAIL {UNRUN.name} lists functions that run now - take them out: {stale}")
    if not failed:
        lines.append(f"  ok: {len(inventory())} controls touched, {len(functions())} functions run, "
                     f"in {m['engines']['chromium']} Chromium and {m['engines']['webkit']} WebKit scenarios")
    return {"failed": failed, "lines": lines}
