import pytest
from pytest_bdd import given, parsers, scenarios, then, when

pytestmark = pytest.mark.ui
scenarios("features/keyboard.feature")


def _wait(ui, pred, what, tries=30):
    for _ in range(tries):
        if pred():
            return
        ui.idle(100)
    raise AssertionError(what)


def _keys(sim):
    return [(f[1], f[2]) for f in sim.frames("key")]


def _key(ui, data_k):
    return f'#kbd [data-k="{data_k}"]'


@then(parsers.parse('the "#{el_id}" box is empty'))
def box_empty(ctx, el_id):
    assert ctx["ui"].page.locator(f"#{el_id}").input_value() == ""


@when(parsers.parse('I press the key "{key}"'))
def press_key(ctx, key):
    ctx["ui"].tap_point(_key(ctx["ui"], key))


@when(parsers.parse('I tap the key "{key}"'))
def tap_key(ctx, key):
    ctx["ui"].tap_point(_key(ctx["ui"], key))


@then(parsers.parse('the board receives the key "{key}" down and up'))
def key_down_up(ctx, key):
    sim, ui = ctx["sim"], ctx["ui"]
    _wait(ui, lambda: (True, key) in _keys(sim) and (False, key) in _keys(sim), f"key frames: {_keys(sim)}")


@when(parsers.parse('I hold the key "{mod}" and press "{key}"'))
def hold_and_press(ctx, mod, key):
    ui = ctx["ui"]
    ui._center("#kbd")                                  # one scroll, then both fingers
    mx, my = ui.position(_key(ui, mod))
    kx, ky = ui.position(_key(ui, key))
    ui._touch("touchStart", [(mx, my)])
    ui.page.wait_for_timeout(450)                       # held, not a latch tap
    ui._touch("touchStart", [(mx, my, 0), (kx, ky, 1)])
    ui.page.wait_for_timeout(60)
    ui._touch("touchEnd", [(kx, ky, 1)])                # the key lifts first
    ui.page.wait_for_timeout(60)
    ui._touch("touchEnd", [(mx, my, 0)])
    ui.idle(300)


@then(parsers.parse('the board pressed "{mod}" before "{key}" and released it after'))
def mod_order(ctx, mod, key):
    ks = _keys(ctx["sim"])
    assert (True, mod) in ks and (True, key) in ks and (False, key) in ks and (False, mod) in ks, ks
    assert ks.index((True, mod)) < ks.index((True, key)) < ks.index((False, key)) <= ks.index((False, mod)), ks


@then(parsers.parse('the key "{key}" shows latched'))
def latched(ctx, key):
    _wait(ctx["ui"], lambda: "on" in (ctx["ui"].page.locator(_key(ctx["ui"], key)).first.get_attribute("class") or ""), f"{key} not latched")


@then(parsers.parse('the key "{key}" shows released'))
def released_ui(ctx, key):
    _wait(ctx["ui"], lambda: "on" not in (ctx["ui"].page.locator(_key(ctx["ui"], key)).first.get_attribute("class") or "").split(),
          f"{key} still latched")


@then(parsers.parse('the board releases "{key}"'))
def board_releases(ctx, key):
    _wait(ctx["ui"], lambda: (False, key) in _keys(ctx["sim"]), f"{key} never released: {_keys(ctx['sim'])}")


@then(parsers.parse('the keyboard shows the key "{key}"'))
def kb_shows(ctx, key):
    _wait(ctx["ui"], lambda: ctx["ui"].page.locator(_key(ctx["ui"], key)).count() > 0, f"no {key} key")


@then(parsers.parse('the key "{key}" is labelled "{label}"'))
def key_label(ctx, key, label):
    got = ctx["ui"].page.locator(_key(ctx["ui"], key)).first.inner_text().strip()
    assert got == label, f"{key} shows {got!r}"


def _slot(ui, n):
    return ui.page.locator(f'#macroTiles [data-ms="{n}"]')


@when(parsers.parse("I tap the macro slot {n:d}"))
def tap_slot(ctx, n):
    _slot(ctx["ui"], n).click()
    ctx["ui"].idle(400)


@when(parsers.parse("I long-press the macro slot {n:d}"))
def long_slot(ctx, n):
    ctx["ui"].touch_hold(f'#macroTiles [data-ms="{n}"]', ms=750)
    ctx["ui"].idle(400)


@when(parsers.parse('I enter the macro steps "{a}" and "{b}"'))
def macro_steps(ctx, a, b):
    ctx["ui"].page.locator("#m_s").fill(a + "\n" + b)


@then(parsers.parse('the macro slot {n:d} is named "{name}"'))
def slot_named(ctx, n, name):
    _wait(ctx["ui"], lambda: name in _slot(ctx["ui"], n).inner_text(), f"slot {n} reads {_slot(ctx['ui'], n).inner_text()!r}")


@then(parsers.parse("the macro slot {n:d} is empty"))
def slot_empty(ctx, n):
    # Only saved macros have tiles now: an empty slot has none.
    _wait(ctx["ui"], lambda: _slot(ctx["ui"], n).count() == 0, f"slot {n} still has a tile")


@given(parsers.parse('the board has a macro "{name}" in slot {n:d}'))
def has_macro(ctx, name, n):
    ctx["sim"].macros[n] = {"name": name, "steps": ["text Regards,", "key ENTER", "text Santhosh"]}


@given("the board has macros in every slot")
def all_macros(ctx):
    for i in range(32):
        ctx["sim"].macros[i] = {"name": f"M{i}", "steps": ["key ENTER"]}


@then(parsers.parse('the macro editor shows the steps of "{name}"'))
def editor_steps(ctx, name):
    v = ctx["ui"].page.locator("#m_s").input_value()
    assert v == "\n".join(ctx["sim"].macros[2]["steps"]) and ctx["ui"].page.locator("#m_n").input_value() == name


@then(parsers.parse('the keyboard is the "{tier}" layout'))
def kb_tier(ctx, tier):
    got = ctx["ui"].page.evaluate("kbTierOf['kbd']")
    assert got == tier, f"#kbd is {got!r} at {ctx['ui'].page.evaluate('document.getElementById(\"kbd\").clientWidth')} px"


@then(parsers.parse('the deck keyboard is the "{tier}" layout'))
def deck_tier(ctx, tier):
    got = ctx["ui"].page.evaluate("kbTierOf['kbd2']")
    assert got == tier, got


@then(parsers.parse("there are {n:d} macros"))
def macro_count(ctx, n):
    _wait(ctx["ui"], lambda: ctx["ui"].page.locator("#macroTiles [data-ms]").count() == n,
          f'{ctx["ui"].page.locator("#macroTiles [data-ms]").count()} macro tiles, not {n}')


@given(parsers.parse("the board's macros already hold {n:d} characters"))
def macros_hold(ctx, n):
    # One big macro (names count too): steps of n - len(name) characters.
    name = "Big"
    ctx["sim"].macros[5] = {"name": name, "steps": ["x" * (n - len(name))]}


@given(parsers.parse('the board has a macro "{name}" in slot {n:d} with the lines "{a}", "{b}" and "{c}"'))
def macro_lines(ctx, name, n, a, b, c):
    ctx["sim"].macros[n] = {"name": name, "steps": [a, b, c]}


@then(parsers.parse('the macro ran: type "{a}", press "{b}", type "{c}"'))
def macro_ran(ctx, a, b, c):
    _wait(ctx["ui"], lambda: ctx["sim"].macro_ran == [("text", a), ("key", b), ("text", c)],
          f"the macro ran {ctx['sim'].macro_ran}")
