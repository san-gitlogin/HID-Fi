"""The old-browser layer (docs/LEGACY_DEVICES.md), checked in a current Chromium
that is told to behave like iOS 12 Safari: no flex gap, no aspect-ratio, no :has,
no pointer events. Proven on a real iPad Air (iOS 12.5.7) by hand; this keeps it
proven on every run.

- a current browser never runs the layer;
- with every iOS 12 gap at once, every element of every tab sits exactly where
  the modern page puts it, on an iPad, an iPhone 5s and a 7" Raspberry Pi screen;
- what is left of the stylesheet is only what iOS 12 can read;
- touches alone (no pointer events) drive the pad, the keys and the buttons;
- the layer settles: on an idle page, with and without Reduce Motion, it does not
  keep running (it once ran 142 times in 3 s from its own writes).
"""
import re

import pytest

from support import dashboard
from support.legacy_wait import settle
from support.board_sim import BoardSim

pytestmark = pytest.mark.ui

IOS12 = ("window.__hidfiForceLegacy=true;window.__hidfiForceNoFlexGap=true;window.__hidfiForceNoAspect=true;"
         "window.__hidfiForceBadSel=/:has\\(|:focus-visible/;")
# Touch only, as on iOS 12: no PointerEvent, no capture, and the browser's own
# pointer events never reach the page - only the layer's.
NO_POINTER = """
delete window.PointerEvent;
Element.prototype.setPointerCapture=undefined;Element.prototype.releasePointerCapture=undefined;
['pointerdown','pointermove','pointerup','pointercancel','pointerleave','pointerenter','pointerover','pointerout']
  .forEach(function(t){window.addEventListener(t,function(e){if(!e.hidfiShim)e.stopImmediatePropagation();},true);});
"""
# The latency pill's text changes width as the number changes: pinned so it is not counted.
PIN_RTT = ("document.addEventListener('DOMContentLoaded',function(){var s=document.createElement('style');"
           "s.textContent='#trtt{display:inline-block;width:44px}';document.head.appendChild(s);});")
RECTS = """() => { const out={}; let i=0;
  for (const e of document.querySelectorAll('body *')) { i++; const r=e.getBoundingClientRect(); if(!r.width&&!r.height) continue;
    out[(e.id||e.tagName)+'@'+i]=[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]; } return out; }"""
# In the iOS 12 run, the layer switches Chromium's own flex gap off box by box
# (a real iOS 12 has none). If the layer did not run, the native gap would do
# the work and the layout would match anyway - so none may be left on screen.
NATIVE_GAP = """() => [...document.querySelectorAll('body *')].filter(e => { if (!e.getClientRects().length) return false;
  const s = getComputedStyle(e); if (!/flex/.test(s.display) || !(parseFloat(s.rowGap) > 0 || parseFloat(s.columnGap) > 0)) return false;
  // Gap only does anything between two laid-out items (a slim rail hides its buttons' labels,
  // leaving one: the layer rightly leaves those alone, and this once flagged them on CI).
  let n = 0;
  for (const k of e.childNodes) {
    if (k.nodeType === 3) { if (/\\S/.test(k.nodeValue)) n++; continue; }
    if (k.nodeType !== 1) continue;
    const c = getComputedStyle(k);
    if (c.display !== 'none' && c.position !== 'absolute' && c.position !== 'fixed') n++;
  }
  e.__items = n; return n > 1; }).slice(0, 5).map(e => ({box: e.id || e.className, items: e.__items,
    seenByLayer: !!e.__lgb, style: e.getAttribute('style'), body: document.body.className,
    layerRuns: window.__hidfiLegacy && window.__hidfiLegacy.runs, queued: !!(window.__hidfiLegacy && window.__hidfiLegacy.busy())}))"""
VIEWS = ["pad", "screen", "keys", "short", "media", "game", "power", "setup"]
SCREENS = [("ipad air", 768, 1024), ("iphone 5s", 320, 568), ("pi 7in", 800, 480)]


def open_page(pages, w, h, init="", motion="reduce"):
    ctx = pages(viewport={"width": w, "height": h}, has_touch=True, is_mobile=w < 900, reduced_motion=motion)
    page = ctx.new_page()
    page.add_init_script(PIN_RTT + init)
    sim = BoardSim()
    sim.attach(page, dashboard.extract_html())
    page.goto(sim.URL)
    page.wait_for_timeout(300)
    settle(page)
    return ctx, page, sim


def test_a_current_browser_never_runs_the_layer(pages, browser):
    ctx, page, _ = open_page(pages, 390, 844)
    assert page.evaluate("window.__hidfiLegacy === undefined")
    ctx.close()


@pytest.mark.parametrize("name,w,h", SCREENS, ids=[s[0] for s in SCREENS])
def test_every_tab_lays_out_exactly_as_the_modern_page(pages, browser, name, w, h):
    got = {}
    for init in ("", IOS12):
        ctx, page, _ = open_page(pages, w, h, init)
        for v in VIEWS:
            page.evaluate(f"go('{v}')")
            settle(page)
            got.setdefault(v, []).append(page.evaluate(RECTS))
            if init:
                left = page.evaluate(NATIVE_GAP)
                assert not left, f"{v}: flex boxes still using the browser's own gap (the layer did not run): {left}"
        ctx.close()
    bad = []
    for v, (modern, old) in got.items():
        for k, r in modern.items():
            o = old.get(k)
            if o is None or max(abs(a - b) for a, b in zip(r, o)) > 1:
                bad.append(f"{v}: {k} {r} -> {o}")
        bad += [f"{v}: {k} only in the old-browser page" for k in old if k not in modern]
    assert not bad, f"{len(bad)} elements differ on {name}:\n  " + "\n  ".join(bad[:15])


def test_what_is_left_of_the_stylesheet_ios12_can_read(pages):
    ctx, page, _ = open_page(pages, 768, 1024, IOS12)
    css = page.evaluate("__hidfiLegacy.css")
    ctx.close()
    for token in ("dvh", "svh", "clamp(", "color-mix(", ":is(", ":where("):
        assert token not in css, token
    assert not re.search(r"(?<![-\w])inset\s*:", css)
    assert not re.search(r"(?<![-\w(])aspect-ratio\s*:", css), "aspect-ratio left as a property"
    # a selector list mixing :has with others would be dropped whole by iOS 12
    assert not [s for s in re.findall(r"([^{}]+)\{", css) if ":has(" in s and "," in s]


def test_touches_alone_drive_the_pad_keys_and_buttons(pages, browser):
    ctx, page, sim = open_page(pages, 768, 1024, IOS12 + NO_POINTER)
    assert page.evaluate("typeof PointerEvent") == "undefined"
    cdp = ctx.new_cdp_session(page)

    def touch(kind, x=0, y=0):
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": x, "y": y, "id": 1}]})

    def mid(sel):
        b = page.locator(sel).first.bounding_box()
        return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    n = len(sim.log)
    x, y = mid("#padarea")
    touch("touchStart", x, y)
    for i in range(12):
        touch("touchMove", x + i * 8, y + i * 5)
        page.wait_for_timeout(16)
    touch("touchEnd")
    page.wait_for_timeout(300)
    moves = [e for e in sim.log[n:] if e[0] == "ws" and isinstance(e[1], tuple) and e[1][0] == "move"]
    assert len(moves) >= 5, "a finger on the pad moved the pointer"
    page.evaluate("go('keys')")
    page.wait_for_timeout(500)
    n = len(sim.log)
    kx, ky = mid('#v-keys [data-k="a"]:visible')
    touch("touchStart", kx, ky)
    page.wait_for_timeout(50)
    touch("touchEnd")
    page.wait_for_timeout(200)
    keys = [e[1] for e in sim.log[n:] if e[0] == "ws" and isinstance(e[1], tuple) and e[1][0] == "key"]
    assert keys == [("key", True, "a"), ("key", False, "a")], keys
    ctx.close()


@pytest.mark.parametrize("motion", ["reduce", "no-preference"])
def test_the_layer_settles_on_an_idle_page(pages, browser, motion):
    ctx, page, _ = open_page(pages, 800, 480, IOS12, motion)
    for v in VIEWS[:4]:
        page.evaluate(f"go('{v}')")
        settle(page)
    before = page.evaluate("__hidfiLegacy.runs")
    page.wait_for_timeout(3000)
    runs = page.evaluate("__hidfiLegacy.runs") - before
    ctx.close()
    # the latency pill updating is a style change it looks at, about once a second
    assert runs <= 4, f"{runs} passes in 3 s with nothing happening"


@pytest.mark.parametrize("slower", [6, 20])
def test_a_slow_cpu_still_gets_every_box(pages, slower):
    """On a slow CPU (an iPad Air, a busy CI runner) a frame came before the page had
    loaded; a pass then used up 'everything' on half a page and the rail, the top bar
    and the tabs were never done (5 of 6 loads at 6x slower, 2026-11-06)."""
    ctx = pages(viewport={"width": 768, "height": 1024}, has_touch=True, is_mobile=True, reduced_motion="reduce")
    page = ctx.new_page()
    ctx.new_cdp_session(page).send("Emulation.setCPUThrottlingRate", {"rate": slower})
    page.add_init_script(PIN_RTT + IOS12 + NO_POINTER)
    sim = BoardSim()
    sim.attach(page, dashboard.extract_html())
    page.goto(sim.URL)
    page.wait_for_timeout(300)
    settle(page)
    for v in ("pad", "keys"):
        page.evaluate(f"go('{v}')")
        settle(page)
        left = page.evaluate(NATIVE_GAP)
        assert not left, f"{v}: boxes the layer never reached on a {slower}x slower CPU: {left}"
