"""Turning the phone, the browser's bars coming and going, a window being resized.

The matrix opens the dashboard fresh at every size. People do not: they rotate
a phone with the deck open, Safari's toolbars slide in and out as they scroll,
a laptop window gets dragged narrower. Each of those must land on exactly the
layout a fresh load at the new size gets - in the first frame (the deck once
painted one frame at the old tier, with 15px keys) - and coming back must leave
no trace.
"""
import pytest

from support import responsive as R, responsive_run as RR

pytestmark = pytest.mark.ui

TOUCH = [d for d in R.devices() if d.touch and d.tags == ("portrait", "standalone")]
DESKTOP = [(1920, 1080, 800, 1080), (1440, 900, 1024, 700), (1280, 800, 640, 800), (2560, 1440, 1099, 1440)]

FINGERPRINT = """() => {
  // Layout, not scroll position: a resize may reset how far the page was scrolled.
  const m = document.getElementById('main'); if (m) m.scrollTop = 0;
  const r = s => { const e = document.querySelector(s); if (!e || !e.checkVisibility()) return null;
                   const b = e.getBoundingClientRect(); return [s, Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)]; };
  return {tiers: Object.assign({}, kbTierOf), rail: document.body.className,
          boxes: ['#padwrap', '#pad', '#padkbd', '#kbd2', '#kbd', '#tabbar', '#rail', '.topbar', '#main']
                 .map(r).filter(Boolean)};
}"""

FIRST_FRAME = """async () => {
  // What the first frame after the resize painted: a frame runs its animation
  // callbacks, then layout and ResizeObserver (where the keyboards re-tier), then
  // paints. A task queued from the frame callback runs after that paint.
  await new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
  const out = [];
  document.querySelectorAll('.kbd').forEach(k => {
    if (!k.checkVisibility()) return;
    const want = kbTierFor(k), got = kbTierOf[k.id];
    if (want !== got) out.push('#' + k.id + ' still ' + got + ' in the first frame, has room for ' + want);
  });
  const de = document.documentElement;
  if (de.scrollWidth > de.clientWidth + 1) out.push('page scrolls sideways by ' + (de.scrollWidth - de.clientWidth) + 'px');
  return out;
}"""


def _resize(page, cdp, w, h, insets=(0, 0, 0, 0)):
    page.set_viewport_size({"width": w, "height": h})
    if cdp:
        t, r, b, l = insets
        cdp.send("Emulation.setSafeAreaInsetsOverride", {"insets": {"top": t, "right": r, "bottom": b, "left": l}})
    page.evaluate("i => { window.__hidfiInsets = i; }", list(insets))     # what the probe checks against


def _same(a, b):
    diffs = []
    if a["tiers"] != b["tiers"]:
        diffs.append(f"keyboard tiers {a['tiers']} -> {b['tiers']}")
    if set(a["rail"].split()) != set(b["rail"].split()):
        diffs.append(f"body classes {a['rail']!r} -> {b['rail']!r}")
    bb = {x[0]: x for x in b["boxes"]}
    for box in a["boxes"]:
        o = bb.get(box[0])
        if not o:
            diffs.append(f"{box[0]} gone")
        elif any(abs(p - q) > 1 for p, q in zip(box[1:], o[1:])):
            diffs.append(f"{box[0]} {box[1:]} -> {o[1:]}")
    return diffs


@pytest.mark.parametrize("vp", TOUCH, ids=[v.name.replace(" ", "_") for v in TOUCH])
@pytest.mark.parametrize("start", ["tab keys", "deck", "fullscreen deck"])
def test_turning_the_phone_and_back(browser, vp, start):
    land = next(d for d in R.devices() if d.name == vp.name and d.tags == ("landscape", "standalone"))
    bars = next(d for d in R.devices() if d.name == vp.name and d.tags == ("portrait", "browser"))
    ctx, page, sim, errs = RR.open_page(browser, vp)
    cdp = ctx.new_cdp_session(page)
    try:
        enter = {n: e for n, e, _ in RR.PAGE_STATES}[start]
        page.evaluate(f"() => {{ {enter}; }}")
        RR.settle(page)
        before = page.evaluate(FINGERPRINT)
        problems = []
        for label, target in (("turned on its side", land), ("toolbars showing", bars), ("upright again", vp)):
            _resize(page, cdp, target.w, target.h, target.insets)
            problems += [f"{label} {target.w}x{target.h}: {p}" for p in page.evaluate(FIRST_FRAME)]
            RR.settle(page)
            r = page.evaluate("s => window.__hidfiProbe(s)", start)
            problems += [f"{label} {target.w}x{target.h}: {b}" for b in r["bad"]]
        after = page.evaluate(FINGERPRINT)
        problems += [f"after turning back: {d}" for d in _same(before, after)]
    finally:
        ctx.close()
    assert not errs, errs
    # Only what a fresh load at the same size does not also show: those are the
    # matrix's to report. Anything left is caused by the turn itself.
    fresh = _fresh_problems(browser, [land, bars, vp], start)
    caused = [p for p in problems if p.split(": ", 1)[1] not in fresh.get(p.split(":")[0].rsplit(" ", 1)[1], set())]
    assert not caused, "turning the device left the layout wrong:\n  " + "\n  ".join(caused)


def _fresh_problems(browser, vps, state):
    out = {}
    enter = {n: e for n, e, _ in RR.PAGE_STATES}[state]
    for v in vps:
        ctx, page, sim, _ = RR.open_page(browser, v)
        try:
            page.evaluate(f"() => {{ {enter}; }}")
            RR.settle(page)
            out[f"{v.w}x{v.h}"] = set(page.evaluate("s => window.__hidfiProbe(s)", state)["bad"])
        finally:
            ctx.close()
    return out


@pytest.mark.parametrize("w,h,w2,h2", DESKTOP, ids=[f"{a}x{b}-to-{c}x{d}" for a, b, c, d in DESKTOP])
def test_resizing_a_window_and_back(browser, w, h, w2, h2):
    vp = R.Viewport("window", w, h)
    ctx, page, sim, errs = RR.open_page(browser, vp)
    try:
        before = page.evaluate(FINGERPRINT)
        problems = []
        page.set_viewport_size({"width": w2, "height": h2})
        problems += page.evaluate(FIRST_FRAME)
        RR.settle(page)
        page.set_viewport_size({"width": w, "height": h})
        problems += page.evaluate(FIRST_FRAME)
        RR.settle(page)
        problems += [f"after resizing back: {d}" for d in _same(before, page.evaluate(FINGERPRINT))]
    finally:
        ctx.close()
    assert not errs, errs
    assert not problems, "\n  ".join(problems)
