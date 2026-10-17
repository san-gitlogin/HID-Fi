import time

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

pytestmark = pytest.mark.ui
scenarios("features/session.feature")


def _wait(ui, pred, what, tries=40):
    for _ in range(tries):
        if pred():
            return
        ui.idle(100)
    raise AssertionError(what)


@then(parsers.parse("the board received {n:d} unlock requests"))
def n_unlocks(ctx, n):
    ctx["ui"].idle(300)
    assert len(ctx["sim"].received("unlock")) == n, ctx["sim"].received("unlock")


@then(parsers.re(r"within (?P<secs>\d+) seconds the board receives (?P<payload>\{.*\})$"))
def within(ctx, secs, payload):
    import json
    want = json.loads(payload)
    end = time.time() + int(secs)
    while time.time() < end:
        if any(all(g.get(k) == v for k, v in want.items()) for g in ctx["sim"].received(want["cmd"])):
            return
        ctx["ui"].idle(200)
    raise AssertionError(f"not received within {secs}s: {want}")


@then(parsers.parse('the field "#{el_id}" already holds "{value}"'))
def field_has(ctx, el_id, value):
    assert ctx["ui"].page.locator(f"#{el_id}").input_value() == value


def _pc(ui, name):
    return ui.page.locator("#pcList .item").filter(has_text=name).first


@then(parsers.parse('the "#{el_id}" control is hidden'))
def hidden(ctx, el_id):
    _wait(ctx["ui"], lambda: not ctx["ui"].page.locator(f"#{el_id}").is_visible(), f"#{el_id} is visible")


@then(parsers.parse('the saved PC "{name}" shows "{os}"'))
def pc_shows(ctx, name, os):
    _wait(ctx["ui"], lambda: ctx["ui"].page.locator("#pcList .item").filter(has_text=name).count()
          and os in _pc(ctx["ui"], name).inner_text(), ctx["ui"].text("pcList"))


@then(parsers.parse('no reply from the board contained "{secret}"'))
def never_echoed(ctx, secret):
    # Everything the page was told about saved PCs: the list it renders.
    assert secret not in ctx["ui"].page.evaluate("JSON.stringify(PCS)")
    assert secret not in ctx["ui"].text("pcList")


@when(parsers.parse('I tap "Unlock" on the saved PC "{name}"'))
def pc_unlock(ctx, name):
    _pc(ctx["ui"], name).locator("[data-pcgo]").click()
    ctx["ui"].idle(200)


@when(parsers.parse('I tap the "{what}" button on the saved PC "{name}"'))
def pc_btn(ctx, what, name):
    sel = {"Edit": "[data-pced]", "Forget": "[data-pcdel]"}[what]
    _pc(ctx["ui"], name).locator(sel).click()
    ctx["ui"].idle(200)


@given(parsers.parse('the board remembers a PC "{name}" running macOS'))
def remembers_pc(ctx, name):
    ctx["sim"].pcs[0] = {"name": name, "os": "mac", "password": "pw-" + name}


@then(parsers.parse('the board receives a PC save for "{name}" without a password'))
def pc_save_nopw(ctx, name):
    _wait(ctx["ui"], lambda: any(c.get("name") == name and "password" not in c for c in ctx["sim"].received("pc_save")),
          f"pc_save: {ctx['sim'].received('pc_save')}")


@given("the PC is asleep and has not allowed USB wake")
def asleep_unarmed(ctx):
    ctx["sim"].host_asleep_unarmed = True


@when(parsers.parse('I tap the presenter button "{name}"'))
def presenter_btn(ctx, name):
    ctx["ui"].page.locator("#v-power [data-pr]").filter(has_text=name).first.click()
    ctx["ui"].idle(150)


@when(parsers.parse("{n:d} seconds pass on the page"))
def page_wait(ctx, n):
    ctx["ui"].idle(n * 1000 + 200)


@then(parsers.parse('"#{el_id}" is no longer "{text}"'))
def no_longer(ctx, el_id, text):
    assert ctx["ui"].text(el_id) != text


@when(parsers.parse("I set the stay-awake interval to {n:d} seconds"))
def wig_interval(ctx, n):
    ctx["ui"].page.locator("#wigInt").evaluate("(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); }", str(n))


@then("the dialog is still open")
def still_open(ctx):
    assert ctx["ui"].page.locator("#sheet.on").count()


@when(parsers.parse('I tap "{label}" in the Buttons card'))
def tap_buttons_card(ctx, label):
    ui = ctx["ui"]
    ui.page.locator("#btnsRow button").filter(has_text=label).first.click()
    ui.idle(300)


@then("the board receives a mouse click over HTTP")
def click_http(ctx):
    _wait(ctx["ui"], lambda: any(t == "http" and isinstance(b, dict) and b.get("cmd") == "mouse_click"
                                 for t, b in ctx["sim"].log), f"log: {ctx['sim'].log[-5:]}")
