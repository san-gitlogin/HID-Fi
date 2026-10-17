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


def _flick(ui, dx):
    pt = ui.page.evaluate("""([keep, span]) => {
        const m = document.getElementById('main').getBoundingClientRect();
        for (let y = m.top + 60; y < Math.min(m.bottom, innerHeight) - 20; y += 12)
          for (let x = m.left + 20; x < m.right - 20; x += 12) {
            if (x + span > m.right - 10 || x + span < m.left + 10) continue;
            const a = document.elementFromPoint(x, y), b = document.elementFromPoint(x + span, y);
            if (a && b && !a.closest(keep) && !b.closest(keep) && a.closest('#main')) return [x, y];
          }
        return null; }""", [KEEP, dx])
    assert pt, "no empty space to flick across on this tab"
    x, y = pt
    ui._touch("touchStart", [(x, y)])
    for f in (0.3, 0.7, 1.0):
        ui.page.wait_for_timeout(30)
        ui._touch("touchMove", [(x + dx * f, y + 3 * f)])
    ui.page.wait_for_timeout(30)
    ui._touch("touchEnd", [])
    ui.idle(300)


@when(parsers.parse("I flick {direction} across empty space"))
def flick(ctx, direction):
    _flick(ctx["ui"], -160 if direction == "left" else 160)


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
