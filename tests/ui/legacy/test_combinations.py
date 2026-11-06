"""Every combination of what a browser can lack, the old-browser layer deciding by
itself (docs/LEGACY_DEVICES.md).

Five features decide whether the layer runs - dvh, aspect-ratio, color-mix(),
:has() and pointer events - and a sixth, flex gap, is measured inside it. Real
browsers lack them in any mix (Chrome 84-107 has flex gap but no dvh; iOS 12 has
none of them), so all 2^5 x 2 = 64 are tried, less the one that cannot exist
(everything present but flex gap). The browser's own feature checks answer "no"
for what is missing and pointer events are really taken away; nothing tells the
layer what to do. For each:

- the layer runs exactly when something is missing, and knows what is;
- every element of every tab sits exactly where the modern page puts it, and no
  box is kept right by the browser's own gap when flex gap is missing;
- without pointer events, touches alone move the pointer and type.
"""
import itertools

import pytest

from support import dashboard
from support.legacy_wait import settle
from support.board_sim import BoardSim

pytestmark = pytest.mark.ui

FEATURES = ["dvh", "aspect", "colormix", "has", "pointer", "flexgap"]
COMBOS = [c for c in itertools.product([True, False], repeat=6) if not (all(c[:5]) and not c[5])]
VIEWS = ["pad", "screen", "keys", "short", "media", "game", "power", "setup"]
W, H = 768, 1024

NO_POINTER = """
delete window.PointerEvent;
Element.prototype.setPointerCapture=undefined;Element.prototype.releasePointerCapture=undefined;
['pointerdown','pointermove','pointerup','pointercancel','pointerleave','pointerenter','pointerover','pointerout']
  .forEach(function(t){window.addEventListener(t,function(e){if(!e.hidfiShim)e.stopImmediatePropagation();},true);});
"""
PIN_RTT = ("document.addEventListener('DOMContentLoaded',function(){var s=document.createElement('style');"
           "s.textContent='#trtt{display:inline-block;width:44px}';document.head.appendChild(s);});")
RECTS = """() => { const out={}; let i=0;
  for (const e of document.querySelectorAll('body *')) { i++; const r=e.getBoundingClientRect(); if(!r.width&&!r.height) continue;
    out[(e.id||e.tagName)+'@'+i]=[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]; } return out; }"""
NATIVE_GAP = """() => [...document.querySelectorAll('body *')].filter(e => { if (!e.getClientRects().length) return false;
  const s = getComputedStyle(e); return /flex/.test(s.display) && (parseFloat(s.rowGap) > 0 || parseFloat(s.columnGap) > 0); })
  .slice(0, 5).map(e => e.id || e.className)"""


def lacking(combo):
    return [f for f, have in zip(FEATURES, combo) if not have]


def simulate(combo):
    """What a browser lacking these looks like to the page."""
    miss = dict(zip(FEATURES, [not h for h in combo]))
    js = ["(function(){var o=CSS.supports.bind(CSS);CSS.supports=function(a,b){var q=b===undefined?a:a+':'+b;"]
    for f, pat in (("dvh", "dvh"), ("aspect", "aspect-ratio"), ("colormix", "color-mix"), ("has", ":has\\(")):
        if miss[f]:
            js.append(f"if(/{pat}/.test(q))return false;")
    js.append("return b===undefined?o(a):o(a,b);};})();")
    # What Chromium cannot be made to lack: the layer's own test switches.
    if miss["has"]:
        js.append("window.__hidfiForceBadSel=/:has\\(/;")
    if miss["flexgap"]:
        js.append("window.__hidfiForceNoFlexGap=true;")
    if miss["pointer"]:
        js.append(NO_POINTER)
    return "".join(js)


def page_for(pages, init):
    ctx = pages(viewport={"width": W, "height": H}, has_touch=True, is_mobile=True, reduced_motion="reduce")
    page = ctx.new_page()
    page.add_init_script(PIN_RTT + init)
    sim = BoardSim()
    sim.attach(page, dashboard.extract_html())
    page.goto(sim.URL)
    page.wait_for_timeout(300)
    settle(page)
    return ctx, page, sim


_modern = {}


def modern(pages):
    if not _modern:
        ctx, page, _ = page_for(pages, "")
        for v in VIEWS:
            page.evaluate(f"go('{v}')")
            settle(page)
            _modern[v] = page.evaluate(RECTS)
        ctx.close()
    return _modern


@pytest.mark.parametrize("combo", COMBOS, ids=["+".join("no-" + f for f in lacking(c)) or "modern" for c in COMBOS])
def test_every_combination(pages, browser, combo):
    base = modern(pages)
    miss = lacking(combo)
    ctx, page, sim = page_for(pages, simulate(combo))
    layer = page.evaluate("window.__hidfiLegacy ? {pe: __hidfiLegacy.pe, fg: __hidfiLegacy.fg} : null")
    trigger = [f for f in miss if f != "flexgap"]
    assert (layer is not None) == bool(trigger), f"layer {'ran' if layer else 'did not run'} lacking {miss}"
    if layer:
        assert layer["pe"] == ("pointer" not in miss), layer
        assert layer["fg"] == ("flexgap" not in miss), layer
    bad = []
    for v in VIEWS:
        page.evaluate(f"go('{v}')")
        settle(page)
        got = page.evaluate(RECTS)
        for k, r in base[v].items():
            o = got.get(k)
            if o is None or max(abs(a - b) for a, b in zip(r, o)) > 1:
                bad.append(f"{v}: {k} {r} -> {o}")
        if "flexgap" in miss:
            left = page.evaluate(NATIVE_GAP)
            assert not left, f"{v}: the browser's own gap still in use: {left}"
    assert not bad, f"lacking {miss}: {len(bad)} differ\n  " + "\n  ".join(bad[:12])
    if "pointer" in miss:
        cdp = ctx.new_cdp_session(page)

        def touch(kind, x=0, y=0):
            cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": x, "y": y, "id": 1}]})
        page.evaluate("go('pad')")
        page.wait_for_timeout(300)
        b = page.locator("#pad").bounding_box()
        x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
        n = len(sim.log)
        touch("touchStart", x, y)
        for i in range(10):
            touch("touchMove", x + i * 8, y + i * 5)
            page.wait_for_timeout(16)
        touch("touchEnd")
        page.wait_for_timeout(200)
        assert sum(1 for e in sim.log[n:] if isinstance(e[1], tuple) and e[1][0] == "move") >= 4, "touch moved nothing"
        page.evaluate("go('keys')")
        page.wait_for_timeout(400)
        k = page.locator('#v-keys [data-k="a"]:visible').first.bounding_box()
        n = len(sim.log)
        touch("touchStart", k["x"] + k["width"] / 2, k["y"] + k["height"] / 2)
        page.wait_for_timeout(50)
        touch("touchEnd")
        page.wait_for_timeout(200)
        assert [e[1] for e in sim.log[n:] if isinstance(e[1], tuple) and e[1][0] == "key"] == [("key", True, "a"), ("key", False, "a")]
    ctx.close()
