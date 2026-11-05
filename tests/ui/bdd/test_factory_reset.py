"""Factory reset on the dashboard (features/factory_reset.feature)."""
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from support import companion_sim as cs
from support.ui import Dashboard

pytestmark = pytest.mark.ui
scenarios("features/factory_reset.feature")


@given("the page's clock can be fast-forwarded")
def page_clock(ctx):
    ctx["clock"] = True


@when(parsers.parse("{n:d} seconds pass on the page"))
def page_seconds(ctx, n):
    ctx["ui"].page.clock.run_for(n * 1000)
    ctx["ui"].page.wait_for_timeout(150)          # the board's reply, if one was sent


@when(parsers.parse('I tick "{label}"'))
def tick(ctx, label):
    ctx["ui"].page.get_by_label(label).check()


@then(parsers.parse('the factory-reset warning lists "{item}"'))
def warning_lists(ctx, item):
    assert item in ctx["ui"].page.inner_text("#sheetin .warnbox")


@then(parsers.parse('the factory-reset sheet says it keeps "{text}"'))
def keeps(ctx, text):
    assert text in ctx["ui"].page.inner_text("#sheetin")


@then("the factory-reset button is disabled")
def go_disabled(ctx):
    assert ctx["ui"].page.is_disabled("#frGo")


@then("the factory-reset button is enabled")
def go_enabled(ctx):
    assert ctx["ui"].page.is_enabled("#frGo")


def _ask(ctx, name):
    app = cs.FakeApp(ctx["sim"].comp, name=name, ip="192.168.1.40")
    ctx["laptop"] = app
    ctx["request"] = app.reset_request()["request"]


@when(parsers.parse('the computer "{name}" asks to factory reset the board'))
def asks(ctx, name):
    _ask(ctx, name)
    ctx["ui"].page.wait_for_selector("#sheet.on", timeout=5000)


@given(parsers.parse('the computer "{name}" has asked to factory reset the board'))
def has_asked(ctx, name):
    _ask(ctx, name)


@then("the board receives the approval of that request")
def approval(ctx):
    sent = [c for c in ctx["sim"].received("factory_reset")]
    assert any(c.get("request") == ctx["request"] and c.get("confirm") is True for c in sent), sent


@then(parsers.parse('the request from "{name}" was {state}'))
def request_state(ctx, name, state):
    assert ctx["laptop"].name == name
    assert ctx["laptop"].reset_request_status(ctx["request"])["state"] == state


@when("a paired computer opens the board's USB link for a factory reset")
def paired_opens(ctx):
    ctx["sim"].open_reset_link("a" * 32, "b" * 32)


@when(parsers.parse('I open the dashboard and sign in with "{pin}"'))
def open_and_sign_in(ctx, browser, pin):
    """As the shared step, but a dialog may follow the PIN: the request, which waited."""
    ui = Dashboard(browser, ctx["sim"], device="phone", answers=ctx["answers"], init=ctx["init"],
                   clock=ctx.get("clock", False))
    ui.page.goto(ui.sim.URL)
    ui.page.wait_for_selector("#sheet.on .pinbox", timeout=8000)
    assert "asks to factory reset" not in ui.page.inner_text("#sheetin")      # the PIN comes first
    boxes = ui.page.locator("#sheet.on .pinbox")          # as conftest's _enter_pin does
    boxes.first.click()
    ui.page.wait_for_timeout(120)
    for d in pin:
        ui.page.keyboard.type(d)
        ui.page.wait_for_timeout(40)
    ui.page.wait_for_function("() => document.querySelector('#sheetin h3') && "
                              "document.querySelector('#sheetin h3').textContent === 'Factory reset requested'",
                              timeout=8000)
    ctx["ui"] = ui
