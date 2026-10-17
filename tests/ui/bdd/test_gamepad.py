import pytest
from pytest_bdd import given, parsers, scenarios, then, when

pytestmark = pytest.mark.ui
scenarios("features/gamepad.feature")

HAT = {"up": 1, "right": 3, "down": 5, "left": 7}


def _wait(ui, pred, what, tries=30):
    for _ in range(tries):
        if pred():
            return
        ui.idle(100)
    raise AssertionError(what)


def _frames(sim):
    """(lx, ly, rx, ry, hat, buttons) from the 10-byte gamepad frame."""
    out = []
    for f in sim.frames("gamepad"):
        b = bytes(f[1:10])
        s8 = lambda v: v - 256 if v > 127 else v
        out.append((s8(b[0]), s8(b[1]), s8(b[2]), s8(b[3]), b[4], int.from_bytes(b[5:9], "little")))
    return out


@given("the controller is enabled on the board")
def enabled(ctx):
    ctx["sim"].gamepad = True


@then(parsers.parse('the controller overlay offers "{text}"'))
def overlay(ctx, text):
    assert ctx["ui"].page.locator("#gpEnable").is_visible() and text in ctx["ui"].text("gpEnable")


@then("the board receives no gamepad frame")
def no_frames(ctx):
    ctx["ui"].idle(300)
    assert not ctx["sim"].frames("gamepad"), ctx["sim"].frames("gamepad")


@then(parsers.parse('the reboot overlay says "{text}"'))
def boot_overlay(ctx, text):
    ui = ctx["ui"]
    ui.page.wait_for_selector("#boot.on", timeout=3000)
    assert ui.text("bootMsg") == text, ui.text("bootMsg")


@then("the controller is shown as enabled")
def shown_enabled(ctx):
    ui = ctx["ui"]
    _wait(ui, lambda: ui.page.evaluate("GP.enabled") and not ui.page.locator("#gpCard .gpmsg").count(), "still disabled")


@when(parsers.parse("I push the {side} stick to the {direction}"))
def push_stick(ctx, side, direction):
    sel = "#stickL" if side == "left" else "#stickR"
    d = {"right": (60, 0), "left": (-60, 0), "top": (0, -60), "bottom": (0, 60)}[direction]
    ctx["ui"].touch_drag(sel, [(0, 0), (d[0] / 2, d[1] / 2), d, d], step_ms=40)


@then(parsers.parse("the board receives the {side} stick at {axis} {sign}"))
def stick_value(ctx, side, axis, sign):
    idx = {("left", "x"): 0, ("left", "y"): 1, ("right", "x"): 2, ("right", "y"): 3}[(side, axis)]
    ok = (lambda v: v > 60) if sign == "positive" else (lambda v: v < -60)
    _wait(ctx["ui"], lambda: any(ok(f[idx]) for f in _frames(ctx["sim"])), f"frames: {_frames(ctx['sim'])[-5:]}")


@then("the last gamepad frame has both sticks centred")
def centred(ctx):
    _wait(ctx["ui"], lambda: _frames(ctx["sim"]) and _frames(ctx["sim"])[-1][:4] == (0, 0, 0, 0), f"last: {_frames(ctx['sim'])[-1:]}")


@when(parsers.parse('I press the D-pad "{d}"'))
def dpad(ctx, d):
    ctx["ui"].touch_drag(f'[data-hat="{HAT[d]}"]', [(0, 0), (0, 0)], step_ms=120)


@then(parsers.parse("a gamepad frame carried the hat value {hat:d}"))
def hat_value(ctx, hat):
    _wait(ctx["ui"], lambda: any(f[4] == hat for f in _frames(ctx["sim"])), f"frames: {_frames(ctx['sim'])}")


@then("the last gamepad frame has the hat released")
def hat_released(ctx):
    _wait(ctx["ui"], lambda: _frames(ctx["sim"]) and _frames(ctx["sim"])[-1][4] == 0, f"last: {_frames(ctx['sim'])[-1:]}")


@when(parsers.parse('I press the gamepad button "{name}"'))
def gp_button(ctx, name):
    ui = ctx["ui"]
    btn = ui.page.locator("#v-game [data-gb]").filter(has_text=name)
    btn = btn.filter(has_text=__import__("re").compile(rf"^\s*{name}\s*$")).first
    btn.evaluate("e => e.setAttribute('data-hold-target', '1')")
    ui.touch_drag('[data-hold-target="1"]', [(0, 0), (0, 0)], step_ms=120)
    ui.page.evaluate("document.querySelectorAll('[data-hold-target]').forEach(e => e.removeAttribute('data-hold-target'))")


@then(parsers.parse("a gamepad frame carried the button bit {bit:d}"))
def btn_bit(ctx, bit):
    _wait(ctx["ui"], lambda: any(f[5] & (1 << bit) for f in _frames(ctx["sim"])), f"frames: {_frames(ctx['sim'])}")


@then("the last gamepad frame has no buttons held")
def no_buttons(ctx):
    _wait(ctx["ui"], lambda: _frames(ctx["sim"]) and _frames(ctx["sim"])[-1][5] == 0, f"last: {_frames(ctx['sim'])[-1:]}")


@given("the board will not come back after a reboot")
def stuck(ctx):
    ctx["sim"].refuse_after_reboot = True


@when(parsers.parse("{n:d} seconds pass on the page"))
def page_wait(ctx, n):
    ctx["ui"].idle(n * 1000)
