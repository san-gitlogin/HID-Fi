"""The responsive matrix: every screen the dashboard can find itself on.

"Every combination" is finite. The layout only changes where a rule in the CSS
or the script says so, so the matrix is read from web_ui.h itself:

  regions     one viewport inside every combination of width band and height
              band the media queries carve out - so every pairing of rules that
              can be active together is drawn at least once
  boundaries  both sides of every breakpoint (359 and 360, 2399 and 2400 ...),
              where an off-by-one or a rule that only half applies shows up
  devices     real phones, foldables, tablets, laptops and displays, upright
              and on their side, with the browser's toolbars showing and
              hidden, and with the notch insets a real device reports

Add a breakpoint to the CSS and the matrix grows to cover it on the next run.

Each viewport carries how the device behaves: touch (the page sees
hover:none and a coarse pointer), mobile (the viewport meta applies), its
pixel ratio, and safe-area insets.
"""
import re
from dataclasses import dataclass, field

from support import dashboard


@dataclass(frozen=True)
class Viewport:
    name: str
    w: int
    h: int
    touch: bool = False
    dpr: float = 1.0
    insets: tuple = (0, 0, 0, 0)        # top, right, bottom, left
    kind: str = "region"                # region | boundary | device
    tags: tuple = field(default_factory=tuple)

    @property
    def id(self):
        t = "touch" if self.touch else "mouse"
        ins = "" if not any(self.insets) else " notch"
        return f"{self.name} {self.w}x{self.h} {t}{ins}"

    @property
    def mobile(self):
        return self.touch and min(self.w, self.h) < 900


def breakpoints(src=None):
    """Width and height cut points: (last value of one band, first of the next)."""
    src = src or dashboard.WEB_UI.read_text(encoding="utf-8")
    html = dashboard.extract_html(src)
    wcut, hcut, coupled = set(), set(), []
    for q in re.findall(r"@media\s*([^{]+)\{", html):
        mins = {}
        for kind, dim, n in re.findall(r"(min|max)-(width|height)\s*:\s*(\d+)px", q):
            n = int(n)
            cut = n if kind == "max" else n - 1     # the band changes between cut and cut+1
            (wcut if dim == "width" else hcut).add(cut)
            if kind == "min":
                mins[dim] = n
        if "width" in mins and "height" in mins:
            coupled.append((mins["width"], mins["height"]))
    for n in re.findall(r"innerWidth\s*<\s*(\d+)", html):
        wcut.add(int(n) - 1)
    for n in re.findall(r"innerHeight\s*<\s*(\d+)", html):
        hcut.add(int(n) - 1)
    return sorted(wcut), sorted(hcut), sorted(set(coupled))


FLOOR_W, FLOOR_H = 280, 300          # below this no browser ships; still measured
TOP_W, TOP_H = 8192, 4320
TOUCH_MAX = 1400                     # wider than this, nobody is touching it


def _bands(cuts, lo, hi):
    """(lo..hi) split at the cuts, as (first, last) pairs."""
    edges = [lo - 1] + [c for c in cuts if lo <= c < hi] + [hi]
    return [(edges[i] + 1, edges[i + 1]) for i in range(len(edges) - 1)]


def _mid(band):
    a, b = band
    return (a + b) // 2 if b - a < 400 else a + min(200, (b - a) // 3)


def _plausible(w, h):
    return 0.15 <= w / h <= 6.7


def regions():
    wcut, hcut, _ = breakpoints()
    out = []
    for wb in _bands(wcut, FLOOR_W, TOP_W):
        for hb in _bands(hcut, FLOOR_H, TOP_H):
            w, h = _mid(wb), _mid(hb)
            if not _plausible(w, h):
                continue
            out.append(Viewport("region", w, h))
            if w <= TOUCH_MAX and h <= TOUCH_MAX:
                out.append(Viewport("region", w, h, touch=True, dpr=2))
    return out


def boundaries():
    wcut, hcut, coupled = breakpoints()
    out = []
    for c in wcut:
        for h in (700, 400, 1400):
            for w in (c, c + 1):
                if _plausible(w, h):
                    out.append(Viewport("edge w", w, h, touch=w <= TOUCH_MAX and h <= TOUCH_MAX, dpr=2 if w <= TOUCH_MAX else 1,
                                        kind="boundary"))
    for c in hcut:
        for w in (390, 900, 1600):
            for h in (c, c + 1):
                if _plausible(w, h):
                    out.append(Viewport("edge h", w, h, touch=w <= TOUCH_MAX and h <= TOUCH_MAX, dpr=2 if w <= TOUCH_MAX else 1,
                                        kind="boundary"))
    # Rules that need both a width and a height (the large-display zoom steps):
    # the corner, and one pixel short on each side.
    for mw, mh in coupled:
        for w, h in ((mw, mh), (mw - 1, mh), (mw, mh - 1)):
            out.append(Viewport("edge zoom", w, h, kind="boundary"))
    return out


# Real devices. (name, portrait w, portrait h, dpr, touch, notch insets portrait,
# browser toolbars in CSS px: portrait, landscape). Toolbars are what the browser
# takes from the screen when its UI is showing; 0 means a desktop window.
IPHONE_NOTCH = (47, 0, 34, 0)
IPHONE_ISLAND = (59, 0, 34, 0)
DEVICES = [
    ("iPhone SE", 375, 667, 2, True, (0, 0, 0, 0), 115, 50),
    ("iPhone 13 mini", 375, 812, 3, True, IPHONE_NOTCH, 150, 50),
    ("iPhone 14", 390, 844, 3, True, IPHONE_NOTCH, 150, 50),
    ("iPhone 15", 393, 852, 3, True, IPHONE_ISLAND, 150, 50),
    ("iPhone 15 Pro Max", 430, 932, 3, True, IPHONE_ISLAND, 150, 50),
    ("iPhone 17 Pro Max", 440, 956, 3, True, IPHONE_ISLAND, 150, 50),
    ("Galaxy S8", 360, 740, 4, True, (24, 0, 0, 0), 112, 56),
    ("Galaxy A51", 412, 914, 2.6, True, (24, 0, 0, 0), 112, 56),
    ("Pixel 7", 412, 915, 2.6, True, (24, 0, 0, 0), 112, 56),
    ("Galaxy Fold cover", 280, 653, 3, True, (24, 0, 0, 0), 112, 56),
    ("Galaxy Fold open", 673, 841, 2.6, True, (24, 0, 0, 0), 112, 56),
    ("Pixel Fold open", 841, 701, 2.6, True, (24, 0, 0, 0), 112, 56),
    ("Surface Duo", 540, 720, 2.5, True, (0, 0, 0, 0), 112, 56),
    ("iPad mini", 744, 1133, 2, True, (24, 0, 20, 0), 74, 74),
    ("iPad Air", 820, 1180, 2, True, (24, 0, 20, 0), 74, 74),
    ("iPad Pro 11", 834, 1194, 2, True, (24, 0, 20, 0), 74, 74),
    ("iPad Pro 12.9", 1024, 1366, 2, True, (24, 0, 20, 0), 74, 74),
    ("Galaxy Tab S8", 800, 1280, 2, True, (24, 0, 0, 0), 112, 56),
    ("laptop 768", 1366, 768, 1, False, (0, 0, 0, 0), 0, 0),
    ("MacBook Air", 1440, 900, 2, False, (0, 0, 0, 0), 0, 0),
    ("MacBook Pro 16", 1728, 1117, 2, False, (0, 0, 0, 0), 0, 0),
    ("1080p", 1920, 1080, 1, False, (0, 0, 0, 0), 0, 0),
    ("QHD", 2560, 1440, 1, False, (0, 0, 0, 0), 0, 0),
    ("ultrawide", 3440, 1440, 1, False, (0, 0, 0, 0), 0, 0),
    ("4K at 100%", 3840, 2160, 1, False, (0, 0, 0, 0), 0, 0),
    ("5K at 100%", 5120, 2880, 1, False, (0, 0, 0, 0), 0, 0),
    ("8K at 100%", 7680, 4320, 1, False, (0, 0, 0, 0), 0, 0),
]
DESKTOP_WINDOWS = [(800, 600), (1024, 700), (1280, 720)]   # a browser window, not maximised


def _rotate(ins):
    t, r, b, l = ins
    # Upright notch at the top becomes a side notch; the home indicator stays at the bottom.
    return (0, t if t > 30 else 0, min(b, 21), t if t > 30 else 0)


def devices():
    out = []
    for name, w, h, dpr, touch, ins, bar_p, bar_l in DEVICES:
        if touch:
            out.append(Viewport(name, w, h, True, dpr, ins, "device", ("portrait", "standalone")))
            out.append(Viewport(name, w, h - bar_p, True, dpr, (0, 0, ins[2] if bar_p < 100 else 0, 0), "device",
                                ("portrait", "browser")))
            out.append(Viewport(name, h, w, True, dpr, _rotate(ins), "device", ("landscape", "standalone")))
            out.append(Viewport(name, h, w - bar_l, True, dpr, _rotate(ins), "device", ("landscape", "browser")))
        else:
            out.append(Viewport(name, w, h, False, dpr, kind="device", tags=("desktop",)))
    for w, h in DESKTOP_WINDOWS:
        out.append(Viewport("window", w, h, False, 1, kind="device", tags=("desktop", "window")))
    return out


def matrix():
    seen, out = set(), []
    for v in devices() + boundaries() + regions():
        key = (v.w, v.h, v.touch, v.insets)
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


# ---- results: one part per test process, merged at the end of the session -----------
import json as _json
import os as _os
import platform as _platform

from support import paths as _paths

PARTS = _paths.REPORTS / "responsive"
KNOWN = _paths.GOLDEN / f"ui_responsive_known.{_platform.system().lower()}.json"


def reset():
    PARTS.mkdir(parents=True, exist_ok=True)
    for f in PARTS.glob("*.jsonl"):
        f.unlink()


def save_part(vp, res):
    PARTS.mkdir(parents=True, exist_ok=True)
    rec = {"id": vp.id, "kind": vp.kind, "w": vp.w, "h": vp.h, "touch": vp.touch, "tags": list(vp.tags),
           "states": {s: r for s, r in res.items()}}
    with open(PARTS / f"{_os.environ.get('PYTEST_XDIST_WORKER', 'main')}.jsonl", "a") as f:
        f.write(_json.dumps(rec) + "\n")


def merged():
    out = {}
    for f in PARTS.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            r = _json.loads(line)
            out[r["id"]] = r
    return out


def finish(config):
    """Session end: ratchet against the golden file and write the report."""
    res = merged()
    if not res:
        return None
    full = len(res) == len(matrix())
    found = {v: sorted({b for s in r["states"].values() for b in s["bad"]}) for v, r in res.items()}
    lines = [f"\nresponsive matrix: {len(res)}/{len(matrix())} viewports, "
             f"{sum(len(r['states']) for r in res.values())} screens measured"]
    failed = False
    if config.getoption("--update-golden") and full:
        KNOWN.write_text(_json.dumps({v: p for v, p in sorted(found.items()) if p}, indent=1) + "\n", encoding="utf-8")
        lines.append(f"  wrote {KNOWN.name}: {sum(map(len, found.values()))} known problem(s)")
    elif KNOWN.exists():
        known = _json.loads(KNOWN.read_text(encoding="utf-8"))
        fixed = [f"{v} :: {p}" for v, ps in known.items() if v in res for p in ps if p not in found.get(v, [])]
        if fixed:
            failed = True
            lines.append(f"  {len(fixed)} known problem(s) are FIXED - take them off {KNOWN.name} "
                         f"(--update-golden) so they cannot come back:")
            lines += ["    " + x for x in fixed[:20]]
    write_report(res, found)
    lines.append("  report: tests/.reports/responsive.md")
    return {"failed": failed, "lines": lines}


def write_report(res, found):
    from collections import Counter, defaultdict
    by_problem = defaultdict(set)
    for v, ps in found.items():
        for p in ps:
            st, _, what = p.partition(": ")
            kind = re.sub(r"\d+(\.\d+)?", "N", what)
            by_problem[kind].add(v)
    kinds = Counter(r["kind"] for r in res.values())
    md = ["# Responsive matrix", "",
          f"{len(res)} viewports ({', '.join(f'{n} {k}' for k, n in kinds.items())}), "
          f"{sum(1 for r in res.values() if r['touch'])} of them touch screens; "
          f"{sum(len(r['states']) for r in res.values())} screens measured.", "",
          "## Problems, by kind", "", "| Problem | Viewports |", "|---|---|"]
    for k, vs in sorted(by_problem.items(), key=lambda kv: -len(kv[1])):
        md.append(f"| {k} | {len(vs)} |")
    md += ["", "## Every viewport", "", "| Viewport | Kind | Problems |", "|---|---|---|"]
    for v, r in sorted(res.items(), key=lambda kv: (kv[1]["kind"], kv[1]["w"], kv[1]["h"])):
        md.append(f"| {v} | {r['kind']} | {len(found[v])} |")
    (_paths.REPORTS / "responsive.md").write_text("\n".join(md) + "\n", encoding="utf-8")
