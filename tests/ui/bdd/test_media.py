import json

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

pytestmark = pytest.mark.ui
scenarios("features/media.feature")

USAGE = {"Volume up": 233, "Volume down": 234, "Mute": 226, "Next track": 181, "Previous track": 182}


def _wait(ui, pred, what, tries=30):
    for _ in range(tries):
        if pred():
            return
        ui.idle(100)
    raise AssertionError(what)


def _consumer(sim):
    return [f[1] for f in sim.frames("consumer")]


@when(parsers.parse("I turn the knob {n:d} by {deg:d} degrees"))
def turn(ctx, n, deg):
    ctx["ui"].circle(f'[data-knob="{n}"]', deg, radius=40)


@when(parsers.parse("I hold the knob {n:d} still"))
def hold_knob(ctx, n):
    ctx["ui"].touch_hold(f'[data-knob="{n}"]', ms=700)


@then(parsers.parse('the board receives "{what}" several times'))
def several(ctx, what):
    _wait(ctx["ui"], lambda: _consumer(ctx["sim"]).count(USAGE[what]) >= 3, f"consumer frames: {_consumer(ctx['sim'])}")


@then(parsers.parse('the board receives "{what}" once'))
def once(ctx, what):
    _wait(ctx["ui"], lambda: _consumer(ctx["sim"]).count(USAGE[what]) >= 1, f"consumer frames: {_consumer(ctx['sim'])}")
    ctx["ui"].idle(200)
    assert _consumer(ctx["sim"]).count(USAGE[what]) == 1, _consumer(ctx["sim"])
    ctx["sim"].clear_log()


@then(parsers.parse("the knob {n:d} shows a positive count"))
def positive(ctx, n):
    t = ctx["ui"].page.locator(f"#kt{n}").inner_text()
    assert t.startswith("+"), t


@then(parsers.parse("the knob {n:d} shows a percentage"))
def percent(ctx, n):
    t = ctx["ui"].page.locator(f"#kt{n}").inner_text()
    assert t.endswith("%"), t


@then(parsers.parse('the "{tile}" tile looks {state}'))
def tile_state(ctx, tile, state):
    el = ctx["ui"].control(tile)
    _wait(ctx["ui"], lambda: ("act" in (el.get_attribute("class") or "").split()) == (state == "active"),
          f"{tile} class {el.get_attribute('class')!r}")


@when(parsers.parse('I choose "{option}" for "{label}"'))
def choose(ctx, option, label):
    ctx["ui"].field(label).select_option(label=option)
    ctx["ui"].idle(150)


def _saved_layout(sim):
    saves = sim.received("ui_save")
    return json.loads(saves[-1]["data"]) if saves else None


@then(parsers.parse('the board saved a layout with a knob named "{name}"'))
def saved_knob(ctx, name):
    _wait(ctx["ui"], lambda: _saved_layout(ctx["sim"]) and any(k["n"] == name for k in _saved_layout(ctx["sim"])["knobs"]),
          f"layouts saved: {ctx['sim'].received('ui_save')}")


@then(parsers.parse("the board saved a layout with {n:d} knobs"))
def saved_n(ctx, n):
    _wait(ctx["ui"], lambda: _saved_layout(ctx["sim"]) is not None and len(_saved_layout(ctx["sim"])["knobs"]) == n, "not saved")


@when(parsers.parse("I tap the edit button of knob {n:d}"))
def knob_edit(ctx, n):
    ctx["ui"].page.locator(f'[data-kedit="{n}"]').click()
    ctx["ui"].idle(200)


@then(parsers.parse("there is no knob {n:d}"))
def no_knob(ctx, n):
    assert ctx["ui"].page.locator(f'[data-knob="{n}"]').count() == 0


@given(parsers.parse('the board has a saved layout with a knob named "{name}"'))
def saved_layout(ctx, name):
    ctx["sim"].ui = json.dumps({"quick": [{"n": "Lock PC", "i": "lock", "t": "cmd", "v": "lock"}],
                                "knobs": [{"n": name, "i": "knob", "t": "key", "up": "RIGHT", "dn": "LEFT", "step": 14, "mode": "endless"}]})


@then(parsers.parse('there is a knob named "{name}"'))
def knob_named(ctx, name):
    _wait(ctx["ui"], lambda: name in ctx["ui"].text("knobs"), ctx["ui"].text("knobs"))


def _quick(ui, label):
    return ui.page.locator("#quickTiles .tile").filter(has_text=label).first


@when(parsers.parse('I tap the quick action "{label}"'))
def tap_quick(ctx, label):
    _quick(ctx["ui"], label).click()
    ctx["ui"].idle(200)


@when(parsers.parse('I long-press the quick action "{label}"'))
def long_quick(ctx, label):
    ui = ctx["ui"]
    _quick(ui, label).evaluate("e => e.setAttribute('data-hold-target', '1')")
    ui.touch_hold('[data-hold-target="1"]', ms=750)
    ui.page.evaluate("document.querySelectorAll('[data-hold-target]').forEach(e => e.removeAttribute('data-hold-target'))")
    ui.idle(200)


@then(parsers.parse('the quick action "{label}" is there'))
def quick_there(ctx, label):
    assert ctx["ui"].page.locator("#quickTiles .tile").filter(has_text=label).count() == 1, ctx["ui"].text("quickTiles")


@then(parsers.parse('the quick action "{label}" is gone'))
def quick_gone(ctx, label):
    _wait(ctx["ui"], lambda: ctx["ui"].page.locator("#quickTiles .tile").filter(has_text=label).count() == 0, "still there")


@then(parsers.parse('the quick action "{label}" looks {state}'))
def quick_state(ctx, label, state):
    el = _quick(ctx["ui"], label)
    _wait(ctx["ui"], lambda: ("act" in (el.get_attribute("class") or "").split()) == (state == "active"),
          f"class {el.get_attribute('class')!r}")


@when(parsers.parse('I set "#{el_id}" to {value}'))
def set_range(ctx, el_id, value):
    loc = ctx["ui"].page.locator(f"#{el_id}")
    loc.click()                                     # a finger on the slider first
    loc.evaluate("(el, v) => { el.value = v; el.dispatchEvent(new Event('input', {bubbles: true})); "
                 "el.dispatchEvent(new Event('change', {bubbles: true})); }", value)
    ctx["ui"].idle(100)


@then(parsers.parse('the step label reads "{text}"'))
def step_label(ctx, text):
    got = ctx["ui"].page.locator("#sheetin label").filter(has_text="Degrees per step").text_content()
    assert got == text, got


@then(parsers.re(r'the board saved a layout whose (?P<kind>knob|quick action) "(?P<name>[^"]+)" is (?P<want>\{.*\})$'))
def saved_item(ctx, kind, name, want):
    want = json.loads(want)
    key, label = ("knobs", "n") if kind == "knob" else ("quick", "n")
    def found():
        lay = _saved_layout(ctx["sim"])
        it = lay and next((x for x in lay[key] if x[label] == name), None)
        return it and all(it.get(k) == v for k, v in want.items())
    _wait(ctx["ui"], found, f"saved: {_saved_layout(ctx['sim'])}")


@when(parsers.parse('I note the quick action "{label}"'))
def note_quick(ctx, label):
    _quick(ctx["ui"], label).evaluate("e => { e.dataset.noted = '1'; }")


@when("the dashboard syncs with the board")
def sync_now(ctx):
    ctx["ui"].page.evaluate("syncAll()")
    ctx["ui"].idle(800)


@then(parsers.parse('the quick action "{label}" is the one I noted'))
def same_quick(ctx, label):
    assert _quick(ctx["ui"], label).evaluate("e => e.dataset.noted === '1'"), "the tile was redrawn"
