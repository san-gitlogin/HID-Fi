"""The input path, on every device (docs/LEGACY_DEVICES.md, "Input latency"):

- nothing runs while nobody is touching: no frame loop on an idle page (the
  trackpad and the gamepad each ran one, every frame, for as long as the page
  was open);
- a gamepad button tapped faster than a frame still reaches the computer as a
  press and a release (sent once a frame, the press was lost).
"""
import pytest

from support import dashboard
from support.legacy_wait import settle
from support.board_sim import BoardSim

pytestmark = pytest.mark.ui

COUNT_FRAMES = """window.__raf=0;(function(){var r=window.requestAnimationFrame;
  window.requestAnimationFrame=function(f){window.__raf++;return r.call(window,f);};})();"""


def open_page(pages, init="", gamepad=False):
    ctx = pages(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    page = ctx.new_page()
    page.add_init_script(init)
    sim = BoardSim()
    sim.gamepad = gamepad          # off until switched on (the board re-enumerates)
    sim.attach(page, dashboard.extract_html())
    page.goto(sim.URL)
    page.wait_for_timeout(300)
    settle(page)
    return ctx, page, sim


@pytest.mark.parametrize("tab", ["pad", "game", "keys"])
def test_nothing_runs_while_nobody_touches(pages, browser, tab):
    ctx, page, _ = open_page(pages, COUNT_FRAMES)
    page.evaluate(f"go('{tab}')")
    page.wait_for_timeout(800)
    before = page.evaluate("__raf")
    page.wait_for_timeout(2000)
    frames = page.evaluate("__raf") - before
    ctx.close()
    assert frames == 0, f"{frames} frame callbacks in 2 s on an idle {tab} tab"


def test_a_gamepad_tap_faster_than_a_frame_reaches_the_computer(pages, browser):
    ctx, page, sim = open_page(pages, gamepad=True)
    page.evaluate("go('game')")
    page.wait_for_timeout(500)
    n = len(sim.log)
    # press and release in one task - well inside one frame
    page.evaluate("""() => { const b = document.querySelector('#v-game [data-gb="4"]');
      const o = {bubbles: true, cancelable: true, pointerId: 7, pointerType: 'touch'};
      b.dispatchEvent(new PointerEvent('pointerdown', o)); b.dispatchEvent(new PointerEvent('pointerup', o)); }""")
    page.wait_for_timeout(200)
    pads = [e[1] for e in sim.log[n:] if isinstance(e[1], tuple) and e[1][0] == "gamepad"]
    ctx.close()
    assert len(pads) >= 2, f"{len(pads)} gamepad reports for a tap"
    btns = [int.from_bytes(bytes(p[6:10]), "little") for p in pads]     # [op][lx ly rx ry hat][u32 buttons]
    assert btns[0] == 1 << 4 and btns[-1] == 0, btns
