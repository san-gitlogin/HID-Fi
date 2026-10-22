import pytest
from pytest_bdd import parsers, scenarios, then, when

pytestmark = pytest.mark.ui
scenarios("features/trackpad.feature")

MASK = {"left": 1, "right": 2, "middle": 4}


def _wait(ui, pred, what, tries=30):
    for _ in range(tries):
        if pred():
            return
        ui.idle(100)
    raise AssertionError(what)


@when("I drag one finger across the trackpad")
def drag_pad(ctx):
    ctx["ui"].touch_drag("#pad", [[0, 0], [20, 5], [40, 10], [60, 15], [80, 20]])


@when("I tap the trackpad")
def tap_pad(ctx):
    ctx["ui"].tap_point("#pad")
    ctx["ui"].idle(600)        # a tap is only final once the double-tap window passes


@then(parsers.parse('the board receives a click of the "{button}" button'))
def got_click(ctx, button):
    sim, ui = ctx["sim"], ctx["ui"]
    _wait(ui, lambda: any(f[1] == MASK[button] and f[2] == 2 for f in sim.frames("button")),
          f"no {button} click; button frames: {sim.frames('button')}")


@then("the board receives no click")
def no_click(ctx):
    ctx["ui"].idle(700)
    assert not [f for f in ctx["sim"].frames("button") if f[2] == 2], ctx["sim"].frames("button")


@then("the board receives a double click")
def got_double(ctx):
    sim, ui = ctx["sim"], ctx["ui"]
    _wait(ui, lambda: any(f[1] == 1 and f[2] == 2 and f[3] == 2 for f in sim.frames("button")),
          f"no double click; button frames: {sim.frames('button')}")


@then(parsers.parse('the board receives a press of the "{button}" button'))
def got_press(ctx, button):
    sim, ui = ctx["sim"], ctx["ui"]
    _wait(ui, lambda: any(f[1] == MASK[button] and f[2] == 1 for f in sim.frames("button")), "no press")


@then(parsers.parse('the board receives a release of the "{button}" button'))
def got_release(ctx, button):
    sim, ui = ctx["sim"], ctx["ui"]
    _wait(ui, lambda: any(f[1] == MASK[button] and f[2] == 0 for f in sim.frames("button")), "no release")


@when(parsers.parse('I tap "{label}" in the Buttons card'))
def tap_in_buttons(ctx, label):
    ui = ctx["ui"]
    card = ui.page.locator("#v-pad .card").filter(has=ui.page.locator("h2", has_text="Buttons"))
    card.locator("button:visible").filter(has_text=label).first.click()
    ui.idle(200)


@then("the mouse-shaped buttons are shown")
def mouse_view(ctx):
    assert ctx["ui"].page.locator("#btnsMouse").is_visible()
    assert not ctx["ui"].page.locator("#btnsRow").is_visible()


@when("I drag the mouse wheel down")
def drag_wheel(ctx):
    ctx["ui"].touch_drag("#mWheel", [[0, 0], [0, 15], [0, 30], [0, 45], [0, 60], [0, 75]])


@when("I tap the mouse wheel")
def tap_wheel(ctx):
    ctx["sim"].clear_log()
    ctx["ui"].tap_point("#mWheel")


@then("the board receives a scroll")
def got_scroll(ctx):
    sim, ui = ctx["sim"], ctx["ui"]
    _wait(ui, lambda: any(f[3] != 0 for f in sim.frames("move")), f"no wheel movement: {sim.frames('move')}")


@then("every grab button shows it is on")
def grab_all_on(ctx):
    on = ctx["ui"].page.evaluate("['btnGrab','btnGrab2','btnGrab3','btnGrab4'].map(i=>document.getElementById(i).classList.contains('on'))")
    assert all(on), on


@when(parsers.parse('I hold "{label}"'))
def hold(ctx, label):
    ui = ctx["ui"]
    el = ui.control(label)
    el.evaluate("e => e.setAttribute('data-hold-target', '1')")
    ui.touch_hold('[data-hold-target="1"]', ms=650)
    el.evaluate("e => e.removeAttribute('data-hold-target')")


@then("the app switcher is open")
def asw_open(ctx):
    ctx["ui"].page.wait_for_selector("#asw.on", timeout=3000)


@then("the app switcher is closed")
def asw_closed(ctx):
    ctx["ui"].page.wait_for_function("() => !document.getElementById('asw').classList.contains('on')", timeout=3000)


def _keys(sim):
    return [(f[1], f[2]) for f in sim.frames("key")]


@then(parsers.parse('the board holds "{mod}" and taps "{key}"'))
def holds_and_taps(ctx, mod, key):
    sim, ui = ctx["sim"], ctx["ui"]
    _wait(ui, lambda: (True, mod) in _keys(sim) and (True, key) in _keys(sim) and (False, key) in _keys(sim),
          f"key frames: {_keys(sim)}")
    ks = _keys(sim)
    assert ks.index((True, mod)) < ks.index((True, key)), f"{mod} must go down before {key}: {ks}"


@then(parsers.parse('the board taps "{key}"'))
def taps(ctx, key):
    sim, ui = ctx["sim"], ctx["ui"]
    _wait(ui, lambda: (True, key) in _keys(sim) and (False, key) in _keys(sim), f"key frames: {_keys(sim)}")


@then(parsers.parse('the board releases "{key}"'))
def releases(ctx, key):
    sim, ui = ctx["sim"], ctx["ui"]
    _wait(ui, lambda: (False, key) in _keys(sim), f"{key} never released: {_keys(sim)}")


@then(parsers.parse('the board never tapped "{key}"'))
def never_tapped(ctx, key):
    ctx["ui"].idle(300)
    assert (True, key) not in _keys(ctx["sim"]), _keys(ctx["sim"])


@when(parsers.parse('I set the "{label}" slider to {value}'))
def set_slider(ctx, label, value):
    ui = ctx["ui"]
    inp = ui.page.locator("#v-pad .slider").filter(has=ui.page.locator("label", has_text=label)).locator("input")
    inp.evaluate("(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); }", value)
    ui.idle(150)


@then("the pad shows its keyboard")
def deck_on(ctx):
    ctx["ui"].page.wait_for_function("() => document.getElementById('padwrap').classList.contains('kb')", timeout=3000)


@then("the pad shows the trackpad")
def deck_off(ctx):
    ctx["ui"].page.wait_for_function("() => !document.getElementById('padwrap').classList.contains('kb')", timeout=3000)


@when(parsers.parse('I press the deck key "{key}"'))
def deck_key(ctx, key):
    ctx["ui"].tap_point(f'#kbd2 [data-k="{key}"]')


@then(parsers.parse('the board receives the key "{key}" down and up'))
def key_down_up(ctx, key):
    sim, ui = ctx["sim"], ctx["ui"]
    _wait(ui, lambda: (True, key) in _keys(sim) and (False, key) in _keys(sim), f"key frames: {_keys(sim)}")


@then("the pad is fullscreen")
def fs_on(ctx):
    ctx["ui"].page.wait_for_function("() => document.getElementById('padwrap').classList.contains('fs')", timeout=3000)


@then("the pad is not fullscreen")
def fs_off(ctx):
    ctx["ui"].page.wait_for_function("() => !document.getElementById('padwrap').classList.contains('fs')", timeout=3000)


@then("the fullscreen keyboard has keys")
def fs_keys(ctx):
    assert ctx["ui"].page.locator("#kbd2 [data-k]").count() > 20


# ---- several fingers -----------------------------------------------------------------
def _fingers(ui, start, end, steps=8, step_ms=16, hold_ms=0, tail_ms=0, scroll=True):
    """Fingers go down at `start` offsets (from the pad centre), travel to `end`,
    and lift together. Each finger keeps its own id throughout."""
    cx, cy = ui._center("#pad") if scroll else ui.position("#pad")
    pts = lambda f: [(cx + (s[0] + (e[0] - s[0]) * f), cy + (s[1] + (e[1] - s[1]) * f), i)
                     for i, (s, e) in enumerate(zip(start, end))]
    ui._touch("touchStart", pts(0))
    if hold_ms:
        ui.page.wait_for_timeout(hold_ms)
    for k in range(1, steps + 1):
        ui.page.wait_for_timeout(step_ms)
        ui._touch("touchMove", pts(k / steps))
    if tail_ms:
        ui.page.wait_for_timeout(tail_ms)
    ui._touch("touchEnd", [(x, y, i) for x, y, i in pts(1)])


@when("I hold one finger on the trackpad and then move it")
def hold_then_move(ctx):
    ui = ctx["ui"]
    _fingers(ui, [(0, 0)], [(60, 30)], hold_ms=500, step_ms=30)
    ctx["notes"]["lift_at"] = len(ctx["sim"].log)
    ui.idle(300)


@then("the pointer moved while the button was down")
def moved_while_down(ctx):
    log = _pointer_frames(ctx["sim"])
    down = next(i for i, f in enumerate(log) if f[0] == "button" and f[2] == 1)
    up = next((i for i, f in enumerate(log) if f[0] == "button" and f[2] == 0 and i > down), len(log))
    assert any(f[0] == "move" and (f[1] or f[2]) for f in log[down:up]), log


def _pointer_frames(sim):
    return [f for t, f in sim.log if t == "ws" and isinstance(f, tuple) and f[0] in ("move", "button")]


@when("I tap the trackpad and at once touch again and move")
def tap_then_drag(ctx):
    # Inside the double-tap window (450 ms): no scrolling or settling between.
    ui = ctx["ui"]
    # lastTapAt used to start at 0, so a page's first 450 ms paired any tap with
    # an imaginary first one (fixed in v4.0.2: it starts at -Infinity). The wait
    # stays so this scenario measures the double tap and nothing else.
    ui.page.wait_for_function("performance.now() > 700")
    x, y = ui._center("#pad")
    ui._touch("touchStart", [(x, y)])
    ui.page.wait_for_timeout(60)
    ui._touch("touchEnd", [])
    ui.page.wait_for_timeout(120)
    _fingers(ui, [(0, 0)], [(50, 20)], step_ms=25, scroll=False)
    ui.idle(300)


@then(parsers.parse("the board received a {px:d} px nudge before the press"))
def nudge(ctx, px):
    log = _pointer_frames(ctx["sim"])
    down = next(i for i, f in enumerate(log) if f[0] == "button" and f[2] == 1)
    assert down > 0 and log[down - 1][0] == "move" and log[down - 1][1] == px, log[:down + 1]


@when("I move three fingers across the trackpad")
def three_fingers(ctx):
    _fingers(ctx["ui"], [(-40, 0), (0, 0), (40, 0)], [(-40, 60), (0, 60), (40, 60)], step_ms=25)
    ctx["ui"].idle(300)


@when(parsers.parse("I pinch {direction} on the trackpad"))
def pinch(ctx, direction):
    near, far = [(-20, 0), (20, 0)], [(-110, 0), (110, 0)]
    a, b = (near, far) if direction == "out" else (far, near)
    _fingers(ctx["ui"], a, b, steps=10, step_ms=25)
    ctx["ui"].idle(300)


@when("I flick two fingers up the trackpad")
def flick_two(ctx):
    ui = ctx["ui"]
    _fingers(ui, [(-20, 60), (20, 60)], [(-20, -60), (20, -60)], steps=6, step_ms=12)
    ui.idle(40)                        # the last pre-lift frame is flushed
    ctx["sim"].clear_log()


def _wheel_after_lift(ctx):
    ctx["ui"].idle(500)
    return [f for f in ctx["sim"].frames("move") if f[3] != 0]


@then("the scroll carries on after the fingers lift")
def glides(ctx):
    assert len(_wheel_after_lift(ctx)) >= 2, ctx["sim"].frames("move")


@then("the scroll stops when the fingers lift")
def no_glide(ctx):
    assert not _wheel_after_lift(ctx), ctx["sim"].frames("move")


@then(parsers.parse('the board taps "{key}" with "{mod}" held'))
def taps_with(ctx, key, mod):
    sim, ui = ctx["sim"], ctx["ui"]
    def ok():
        ks = _keys(sim)
        for i, k in enumerate(ks):
            if k == (True, key):
                held = {n for d, n in ks[:i] if d} - {n for d, n in ks[:i] if not d}
                downs = [j for j, x in enumerate(ks[:i]) if x == (True, mod)]
                ups = [j for j, x in enumerate(ks[:i]) if x == (False, mod)]
                if downs and (not ups or max(downs) > max(ups)):
                    return True
        return False
    _wait(ui, ok, f"{key} never tapped with {mod} held: {_keys(sim)}")


@when("the page is hidden")
def page_hidden(ctx):
    # Headless Chromium has no window to switch away from, so the page is told
    # the way the browser tells it: document.hidden flips, visibilitychange fires.
    ctx["ui"].page.evaluate("""() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => true});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    ctx["ui"].idle(200)


@when(parsers.parse("I swipe three fingers {direction} the trackpad"))
def three_swipe(ctx, direction):
    d = {"up": (0, -150), "down": (0, 150), "left": (-200, 0), "right": (200, 0)}[direction]
    start = [(-50, 0), (0, 0), (50, 0)]
    _fingers(ctx["ui"], start, [(x + d[0], y + d[1]) for x, y in start], steps=12, step_ms=25)
    ctx["ui"].idle(400)


@when("I tap three fingers on the trackpad")
def three_tap(ctx):
    _fingers(ctx["ui"], [(-50, 0), (0, 0), (50, 0)], [(-50, 0), (0, 0), (50, 0)], steps=1, step_ms=40)
    ctx["ui"].idle(300)


@then(parsers.parse('the board tapped "{key}" at least {n:d} times'))
def tapped_n(ctx, key, n):
    _wait(ctx["ui"], lambda: _keys(ctx["sim"]).count((True, key)) >= n, f"key frames: {_keys(ctx['sim'])}")


@when(parsers.parse('I hold "{label}" and slide {dx:d} px right and {dy:d} px down'))
def hold_and_slide(ctx, label, dx, dy):
    ui = ctx["ui"]
    el = ui.control(label)
    el.evaluate("e => e.setAttribute('data-hold-target', '1')")
    steps = [(dx * k / 10, 0) for k in range(11)] + [(dx, dy * k / 6) for k in range(1, 7)]
    ui.touch_drag('[data-hold-target="1"]', [(0, 0)] + steps, step_ms=30, hold_ms=650)
    el.evaluate("e => e.removeAttribute('data-hold-target')")


@when("the page is shown again")
def page_shown(ctx):
    ctx["sim"].clear_log()
    ctx["ui"].page.evaluate("""() => { Object.defineProperty(document, 'hidden', {configurable: true, get: () => false});
        document.dispatchEvent(new Event('visibilitychange')); }""")
    ctx["ui"].idle(200)


@then("the hidden switch control was flipped")
def switch_flipped(ctx):
    assert ctx["ui"].page.evaluate("hapticEl && hapticEl.checked === true"), "the iOS haptic switch never ticked"


@when("I tap the trackpad and at once touch again and hold still")
def tap_then_hold(ctx):
    ui = ctx["ui"]
    ui.page.wait_for_function("performance.now() > 700")
    x, y = ui._center("#pad")
    ui._touch("touchStart", [(x, y)])
    ui.page.wait_for_timeout(60)
    ui._touch("touchEnd", [])
    ui.page.wait_for_timeout(120)
    ui._touch("touchStart", [(x, y)])
    ui.page.wait_for_timeout(450)      # past DRAG_MS (250): still held, so it is a drag
    ui._touch("touchEnd", [])
    ui.idle(300)


@when(parsers.parse('I hold "{label}" until the browser cancels the touch'))
def hold_cancelled(ctx, label):
    ui = ctx["ui"]
    el = ui.control(label)
    el.evaluate("e => e.setAttribute('data-hold-target', '1')")
    x, y = ui._center('[data-hold-target="1"]')
    ui._touch("touchStart", [(x, y)])
    ui.page.wait_for_timeout(650)
    ui._touch("touchCancel", [])       # e.g. the OS took the touch for a system gesture
    el.evaluate("e => e.removeAttribute('data-hold-target')")
    ui.idle(300)


@then(parsers.parse('the board has not released "{key}"'))
def not_released(ctx, key):
    assert (False, key) not in _keys(ctx["sim"]), _keys(ctx["sim"])
