import pytest
from pytest_bdd import parsers, scenarios, then, when

from support.ui import Dashboard

pytestmark = pytest.mark.ui
scenarios("features/app_shell.feature")


@when("the latency ping has had time to run")
def latency_ping(ctx):
    ctx["ui"].page.wait_for_function("() => /ms/.test(document.getElementById('trtt').textContent)", timeout=6000)


@when("I open the dashboard without signing in")
def open_without_pin(ctx, browser):
    ui = Dashboard(browser, ctx["sim"], device="phone", answers=ctx["answers"])
    ui.page.goto(ctx["sim"].URL)
    ui.page.wait_for_selector("#sheet.on .pinbox", timeout=8000)
    ctx["ui"] = ui


@then("the sidebar is collapsed")
def sidebar_collapsed(ctx):
    assert ctx["ui"].page.evaluate("document.body.classList.contains('rail-slim')")


# The dashboard ignores swipes that start on a key, a control or the pad (by
# design - see its help text), so a flick has to start on genuinely empty space.
KEEP = ("#padwrap,.kbd,.knob,.mouse,.seg,input,textarea,select,button,a,label,"
        "[data-k],[data-mb],[data-md],[contenteditable]")


def _flick(ui, dx, dy=0, step_ms=30, fingers=1, start=None):
    pt = ui.page.evaluate("""([keep, span]) => {
        const m = document.getElementById('main').getBoundingClientRect();
        for (let y = m.top + 60; y < Math.min(m.bottom, innerHeight) - 20; y += 12)
          for (let x = m.left + 20; x < m.right - 20; x += 12) {
            if (x + span > m.right - 10 || x + span < m.left + 10) continue;
            // Clear for 28px around the start: Chromium snaps a touch onto a nearby
            // control (touch adjustment), and the swipe rightly ignores those.
            const clear = (px, py) => [[0,0],[28,0],[-28,0],[0,28],[0,-28],[20,20],[-20,-20],[20,-20],[-20,20]]
              .every(([ox, oy]) => { const e = document.elementFromPoint(px + ox, py + oy); return e && !e.closest(keep) && e.closest('#main'); });
            const b = document.elementFromPoint(x + span, y);
            if (clear(x, y) && b && !b.closest(keep)) return [x, y];
          }
        return null; }""", [KEEP, dx])
    assert pt or start, "no empty space to flick across on this tab"
    x, y = start or pt
    pts = lambda f: [(x + dx * f + 40 * i, y + (dy or 3) * f, i) for i in range(fingers)]
    ui._touch("touchStart", pts(0))
    for f in (0.3, 0.7, 1.0):
        ui.page.wait_for_timeout(step_ms)
        ui._touch("touchMove", pts(f))
    ui.page.wait_for_timeout(step_ms)
    ui._touch("touchEnd", pts(1))
    ui.idle(300)


@when(parsers.parse("I flick {direction} across empty space"))
def flick(ctx, direction):
    dx, dy = {"left": (-160, 0), "right": (160, 0), "up": (0, -160), "down": (0, 160)}[direction]
    _flick(ctx["ui"], dx, dy=dy)


@when("the page is hidden")
def page_hidden(ctx):
    ctx["ui"].page.evaluate("""() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    ctx["ui"].idle(200)


@when("the page is shown again")
def page_shown(ctx):
    ctx["notes"]["http_status"] = ctx["sim"].http_status_calls
    ctx["ui"].page.evaluate("""() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    ctx["ui"].idle(400)


@then("the board was asked for its status over HTTP")
def http_status(ctx):
    assert ctx["sim"].http_status_calls > ctx["notes"]["http_status"]


@when("I flick left starting on a button")
def flick_from_button(ctx):
    ui = ctx["ui"]
    x, y = ui._center("#v-power button:visible")
    _flick(ui, -160, start=(x, y))


@when("I drag slowly left across empty space")
def slow_drag(ctx):
    _flick(ctx["ui"], -160, step_ms=300)          # 1.2 s: a drag, not a flick


@when("I flick left with two fingers")
def two_finger_flick(ctx):
    _flick(ctx["ui"], -160, fingers=2)


@when("I drag the mouse left across empty space")
def mouse_drag(ctx):
    ui = ctx["ui"]
    box = ui.page.locator("#v-power .hint").first.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    ui.page.mouse.move(x, y)
    ui.page.mouse.down()
    ui.page.mouse.move(x - 200, y, steps=5)
    ui.page.mouse.up()
    ui.idle(300)
