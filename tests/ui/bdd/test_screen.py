import json

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from support import companion_sim as cs
from support.fake_pc import FakePc

pytestmark = pytest.mark.ui
scenarios("features/screen.feature")


def _wait(ui, pred, what, tries=80):
    for _ in range(tries):
        if pred():
            return
        ui.idle(100)
    raise AssertionError(what)


def _apps(ctx):
    return ctx["notes"].setdefault("apps", {})


def _pcs(ctx):
    return ctx["notes"].setdefault("pcs", {})


@pytest.fixture(autouse=True)
def _close_fake_pcs(ctx):
    yield
    for pc in ctx["notes"].get("pcs", {}).values():
        try:
            pc.close()
        except Exception:
            pass


def _new_app(ctx, name):
    ip = "192.168.1.%d" % (20 + len(_apps(ctx)))
    app = cs.FakeApp(ctx["sim"].comp, name=name, ip=ip)
    _apps(ctx)[name] = app
    return app


def _pair(ctx, app):
    sim = ctx["sim"]
    with sim.lock:
        app.hello()
        sim.comp.command("companion_pair", {"id": app.id}, "serial")
        app.advance()
        sim.comp.command("companion_pair", {"id": app.id, "code": app.code}, "serial")


# ---- Given ---------------------------------------------------------------------------------
@given(parsers.parse('a computer "{name}" is asking to pair'))
def asking(ctx, name):
    with ctx["sim"].lock:
        _new_app(ctx, name).hello()


@when(parsers.parse('a computer "{name}" asks to pair now'))
def asks_now(ctx, name):
    with ctx["sim"].lock:
        _new_app(ctx, name).hello()       # the board pushes companion_pending to open dashboards


@given(parsers.parse('a computer "{name}" is paired and running'))
def paired_running(ctx, browser, name):
    app = _new_app(ctx, name)
    pc = FakePc(browser, app, "live")     # sets app.fp to the stand-in's real certificate
    _pcs(ctx)[name] = pc
    _pair(ctx, app)
    with ctx["sim"].lock:
        app.beat()
    pc.serve(ctx["ui"].page)


@given(parsers.parse('a computer "{name}" is paired but not running'))
def paired_not_running(ctx, name):
    _pair(ctx, _new_app(ctx, name))


@given(parsers.parse('a computer "{name}" is paired but its app refuses connections'))
def refuses_connections(ctx, name):
    # A real address with nothing listening: the browser's socket fails at once.
    app = _new_app(ctx, name)
    app.ip, app.port = "127.0.0.1", 47899
    _pair(ctx, app)
    with ctx["sim"].lock:
        app.beat()


@given(parsers.parse('"{name}" turns viewers away with "{why}"'))
def turns_away(ctx, name, why):
    _pcs(ctx)[name].behaviour = "refuse:" + why


@given(parsers.parse('someone in the middle answers for "{name}"'))
def middle(ctx, name):
    # The board vouches for one certificate; a different one answers.
    app = _apps(ctx)[name]
    with ctx["sim"].lock:
        app.fp = "ab" * 32
        app.beat()


# ---- When ----------------------------------------------------------------------------------
_BUTTON = {"Pair": "data-scrpair", "Not now": "data-scrno", "Dismiss": "data-scrno",
           "Show": "data-scrwatch", "Remove": "data-scrdel"}


@when(parsers.parse('I tap "{label}" for "{name}"'))
def tap_for(ctx, label, name):
    ui = ctx["ui"]
    sel = f'#scrList [{_BUTTON[label]}="{_apps(ctx)[name].id}"]'
    ui.page.locator(sel).wait_for(timeout=6000)
    ui.page.locator(sel).tap()
    ui.idle(300)


@when(parsers.parse('"{name}" shows its pairing code'))
def shows_code(ctx, name):
    with ctx["sim"].lock:
        _apps(ctx)[name].advance()
    # The dashboard learns the new state from its next list read.
    ctx["ui"].page.evaluate("scrList()")
    ctx["ui"].idle(300)


def _code_box(ctx, name):
    sel = f'[data-scrcode="{_apps(ctx)[name].id}"]'
    ctx["ui"].page.locator(sel).wait_for(timeout=6000)
    return ctx["ui"].page.locator(sel)


@when(parsers.parse('I type the code "{name}" shows'))
def type_code(ctx, name):
    box = _code_box(ctx, name)
    box.tap()
    ctx["ui"].page.keyboard.type(_apps(ctx)[name].code, delay=20)


@when(parsers.parse('I type "{code}" as the code for "{name}"'))
def type_literal(ctx, code, name):
    box = _code_box(ctx, name)
    box.tap()
    ctx["ui"].page.keyboard.type(code, delay=20)


@when(parsers.parse('I type a wrong code for "{name}" and press Enter'))
def type_wrong(ctx, name):
    app = _apps(ctx)[name]
    wrong = "%06d" % ((int(app.code) + 1) % 1_000_000)
    box = _code_box(ctx, name)
    box.tap()
    ctx["ui"].page.keyboard.type(wrong, delay=20)
    ctx["ui"].page.keyboard.press("Enter")
    ctx["ui"].idle(400)


@when(parsers.parse('I confirm the code for "{name}"'))
def confirm_code(ctx, name):
    ctx["ui"].page.locator(f'[data-scrok="{_apps(ctx)[name].id}"]').tap()
    ctx["ui"].idle(400)


def _stage(ctx):
    box = ctx["ui"].page.locator("#scrStage").bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


@when("I pinch outwards on the screen")
def pinch(ctx):
    ui = ctx["ui"]
    cx, cy = _stage(ctx)
    ui._touch("touchStart", [(cx - 20, cy, 0), (cx + 20, cy, 1)])
    for i in range(1, 9):
        ui.page.wait_for_timeout(16)
        ui._touch("touchMove", [(cx - 20 - 8 * i, cy, 0), (cx + 20 + 8 * i, cy, 1)])
    ui._touch("touchEnd", [])
    ui.idle(200)


@when("I double tap the screen")
def double_tap(ctx):
    ui = ctx["ui"]
    cx, cy = _stage(ctx)
    for _ in range(2):
        ui._touch("touchStart", [(cx, cy)])
        ui.page.wait_for_timeout(30)
        ui._touch("touchEnd", [])
        ui.page.wait_for_timeout(60)
    ui.idle(200)


@when("I drag the zoomed picture")
def drag_picture(ctx):
    ctx["ui"].touch_drag("#scrStage", [(0, 0), (-30, -20), (-60, -40)])


@when("I hold Ctrl and scroll up on the screen")
def ctrl_wheel(ctx):
    page = ctx["ui"].page
    cx, cy = _stage(ctx)
    page.mouse.move(cx, cy)
    page.keyboard.down("Control")
    page.mouse.wheel(0, -120)
    page.keyboard.up("Control")
    ctx["ui"].idle(200)


@when("I drag the floating window by its title")
def drag_float(ctx):
    ctx["ui"].touch_drag("#scrFName", [(0, 0), (-60, -100), (-400, -900)])


@when("I resize the floating window")
def resize_float(ctx):
    ctx["ui"].touch_drag("#scrFSize", [(0, 0), (-40, 0), (-80, 0)])


@when(parsers.parse('the board is unplugged from "{name}"'))
def unplugged(ctx, name):
    pc = _pcs(ctx)[name]
    pc.behaviour = "refuse:closed"      # the app turns viewers away until plugged back
    pc.end("Stopped")


# ---- Then ----------------------------------------------------------------------------------
@then(parsers.parse('the screen says "{text}"'))
def screen_says(ctx, text):
    ui = ctx["ui"]
    _wait(ui, lambda: text in ui.page.inner_text("#scrMsg"), f'#scrMsg reads {ui.page.inner_text("#scrMsg")!r}')


@then(parsers.parse('the screen shows "{name}" live'))
def shows_live(ctx, name):
    ui = ctx["ui"]
    _wait(ui, lambda: ui.page.evaluate("document.getElementById('scrVideo').videoWidth > 0"),
          f'no picture; state {ui.page.evaluate("SCR.state + ` ` + SCR.why")}, msg {ui.page.inner_text("#scrMsg")!r}', tries=150)
    _wait(ui, lambda: name in ui.page.inner_text("#scrBadge"), f'#scrBadge reads {ui.page.inner_text("#scrBadge")!r}')
    assert ui.page.inner_text("#scrMsg").strip() == ""


@then(parsers.parse("the screen is zoomed to {pct:d}%"))
def zoomed_to(ctx, pct):
    ui = ctx["ui"]
    _wait(ui, lambda: f"{pct}%" in ui.page.inner_text("#scrBadge"), f'#scrBadge reads {ui.page.inner_text("#scrBadge")!r}')


@then("the screen is zoomed in")
def zoomed_in(ctx):
    ui = ctx["ui"]
    _wait(ui, lambda: "%" in ui.page.inner_text("#scrBadge"), f'#scrBadge reads {ui.page.inner_text("#scrBadge")!r}')


@then("the screen fits")
def fits(ctx):
    ui = ctx["ui"]
    _wait(ui, lambda: "%" not in ui.page.inner_text("#scrBadge"), f'#scrBadge reads {ui.page.inner_text("#scrBadge")!r}')


@then("the trackpad is in the Screen tab")
def pad_in_screen(ctx):
    assert ctx["ui"].page.evaluate("document.getElementById('padwrap').parentNode.id") == "scrPadSlot"


@then("the trackpad is back in the Trackpad tab")
def pad_home(ctx):
    assert ctx["ui"].page.evaluate("!!document.getElementById('padwrap').closest('#v-pad')")


@then("the floating window is shown, still live")
def float_live(ctx):
    ui = ctx["ui"]
    assert ui.page.locator("#scrFloat").is_visible()
    _wait(ui, lambda: ui.page.evaluate("document.getElementById('scrVideo').videoWidth > 0"), "the floating picture is blank")


@then("the floating window is inside the screen")
def float_inside(ctx):
    ui = ctx["ui"]
    vp = ui.page.viewport_size
    b = ui.page.locator("#scrFloat").bounding_box()
    tab = 60 if vp["width"] <= 767 else 0
    assert b["x"] >= 0 and b["y"] >= 0, b
    assert b["x"] + b["width"] <= vp["width"] and b["y"] + b["height"] <= vp["height"] - tab, (b, vp)


@then("the floating window is closed")
def float_closed(ctx):
    _wait(ctx["ui"], lambda: not ctx["ui"].page.locator("#scrFloat").is_visible(), "the floating window is still open")


@then(parsers.parse('"#{el_id}" is minimised'))
def minimised(ctx, el_id):
    assert "min" in (ctx["ui"].page.locator(f"#{el_id}").get_attribute("class") or "").split()
    assert not ctx["ui"].page.locator("#scrFBody").is_visible()


@then(parsers.parse('"#{el_id}" is not minimised'))
def not_minimised(ctx, el_id):
    assert "min" not in (ctx["ui"].page.locator(f"#{el_id}").get_attribute("class") or "").split()


@then(parsers.parse('"{name}" sees the viewer leave'))
def viewer_left(ctx, name):
    pc = _pcs(ctx)[name]
    ui = ctx["ui"]
    _wait(ui, lambda: ui.page.evaluate("!SCR.h && SCR.state!=='live'"), "still watching")
    assert pc.hellos, "it never had a viewer"


@then(parsers.parse('"#{el_id}" does not contain "{text}"'))
def not_contains(ctx, el_id, text):
    ui = ctx["ui"]
    _wait(ui, lambda: text not in ui.page.inner_text(f"#{el_id}"), f"#{el_id} still contains {text!r}")
