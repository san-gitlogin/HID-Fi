"""Input latency of the dashboard, measured: from a finger touching the glass to
the board's message leaving the page, and to the screen showing the change.
Chromium with its CPU slowed (6x stands in for a 2013 phone chip), real touches
through the browser's input pipeline, timestamps taken inside the page.

    python3 tests/perf/latency.py              # modern path, 1x and 6x
    python3 tests/perf/latency.py --legacy     # the old-browser layer forced on

Prints median and worst per action. docs/LEGACY_DEVICES.md has the budgets.
"""
import argparse
import statistics
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright  # noqa: E402
from support.board_sim import BoardSim  # noqa: E402
from support import dashboard  # noqa: E402

LEGACY = ("window.__hidfiForceLegacy=true;window.__hidfiForceNoFlexGap=true;window.__hidfiForceNoAspect=true;"
          "window.__hidfiForceBadSel=/:has\\(|:focus-visible/")
# In the page: when the finger landed (first touch handler, capture phase), when
# the next binary/text message left, and when a frame after it was painted.
PROBE = """
window.__lat={t0:0,send:[],paint:[]};
// Browsers fire pointerdown before touchstart (and pointermove before
// touchmove): the clock starts at whichever comes first.
function __latStart(paint){if(!__lat.t0){__lat.t0=performance.now();if(paint){var t=__lat.t0;
  requestAnimationFrame(function(){requestAnimationFrame(function(){__lat.paint.push(performance.now()-t);});});}}}
['pointerdown','touchstart'].forEach(function(k){document.addEventListener(k,function(){__latStart(true);},true);});
// A stream of moves: each message is timed from the latest move before it.
['pointermove','touchmove'].forEach(function(k){document.addEventListener(k,function(e){if(!__lat.mv||e.type==='pointermove')__lat.t0=performance.now();__lat.mv=1;},true);});
['pointerup','touchend'].forEach(function(k){document.addEventListener(k,function(){__lat.t0=0;},true);});
window.__latHook=function(sock){if(!sock||sock.__lat)return;var s=sock.send.bind(sock);sock.__lat=1;
  sock.send=function(d){if(__lat.t0){__lat.send.push(performance.now()-__lat.t0);__lat.t0=0;}return s(d);};};
"""


def touch(cdp, kind, x, y):
    cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": [] if kind == "touchEnd" else [{"x": x, "y": y, "id": 1}]})


def center(page, sel):
    b = page.locator(sel).first.bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def run(browser, rate, legacy, n=25, busy=0):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    try:
        return _run(ctx, rate, legacy, n, busy)
    finally:
        ctx.close()      # a failed measurement must not leave its page open (CI ran out of memory that way)


def _run(ctx, rate, legacy, n, busy):
    page = ctx.new_page()
    if legacy:
        page.add_init_script(LEGACY)
    page.add_init_script(PROBE)
    if busy:
        # A page that is slow to redraw (an old phone): every frame spends
        # `busy` ms first. Input sent on the input path does not wait for it;
        # input sent on the next frame does.
        page.add_init_script("(function f(){requestAnimationFrame(function(){var t=performance.now();while(performance.now()-t<%d){}f();});})();" % busy)
    sim = BoardSim()
    sim.attach(page, dashboard.extract_html())
    page.goto(sim.URL)
    page.wait_for_timeout(1200)
    # The tests' socket is Playwright's own class, so the page's live socket is
    # hooked, not the browser's WebSocket.
    page.evaluate("__latHook(ws)")
    cdp = ctx.new_cdp_session(page)
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": rate})
    out = {}

    def take(key):
        v = page.evaluate("[__lat.send.splice(0), __lat.paint.splice(0)]")
        out[key] = v

    # trackpad: finger down, then moves
    page.evaluate("go('pad')"); page.wait_for_timeout(600)
    x, y = center(page, "#pad")
    page.evaluate("__lat.send.length=0;__lat.paint.length=0")
    touch(cdp, "touchStart", x, y)
    for i in range(n):
        touch(cdp, "touchMove", x + (i % 10) * 6, y + (i % 7) * 5)
        page.wait_for_timeout(16)
    touch(cdp, "touchEnd", 0, 0)
    page.wait_for_timeout(300)
    out["pointer move -> message"] = page.evaluate("__lat.send.splice(0)")
    page.evaluate("__lat.paint.length=0")
    # keyboard: tap a letter
    page.evaluate("go('keys')"); page.wait_for_timeout(800)
    kx, ky = center(page, '#v-keys [data-k="a"]:visible')
    for i in range(n):
        touch(cdp, "touchStart", kx, ky); page.wait_for_timeout(40); touch(cdp, "touchEnd", 0, 0); page.wait_for_timeout(120)
    s, p = page.evaluate("[__lat.send.splice(0), __lat.paint.splice(0)]")
    out["key tap -> message"] = s
    out["key tap -> painted"] = p
    # tab switch: tap the nav button for a tab, time to a painted frame
    tabs = ["media", "game", "power", "pad"]
    paints = []
    for t in tabs * 3:
        bx, by = center(page, f'[data-v="{t}"]:visible')
        touch(cdp, "touchStart", bx, by); page.wait_for_timeout(30); touch(cdp, "touchEnd", 0, 0); page.wait_for_timeout(700)
        paints += page.evaluate("__lat.paint.splice(0)")
        page.evaluate("__lat.send.length=0")
    out["tab tap -> painted"] = paints
    return out


def fmt(v):
    if not v:
        return "no samples"
    return f"median {statistics.median(v):6.1f} ms   worst {max(v):6.1f} ms   (n={len(v)})"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--rates", default="1,6")
    ap.add_argument("--busy", type=int, default=0, help="ms every frame spends before anything else (a slow redraw)")
    a = ap.parse_args()
    with sync_playwright() as p:
        b = p.chromium.launch()
        for rate in [float(r) for r in a.rates.split(",")]:
            print(f"--- CPU {rate:g}x{' legacy' if a.legacy else ''}")
            for k, v in run(b, rate, a.legacy, busy=a.busy).items():
                print(f"  {k:26s} {fmt(v)}")
        b.close()
