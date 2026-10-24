import json

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

pytestmark = pytest.mark.ui
scenarios("features/keys_vault_shortcuts.feature")


def _wait(ui, pred, what, tries=30):
    for _ in range(tries):
        if pred():
            return
        ui.idle(100)
    raise AssertionError(what)


@given(parsers.parse('the vault has its own PIN "{pin}"'))
def vault_pin(ctx, pin):
    ctx["sim"].vault_pin = pin


def _entry(ui, label):
    return ui.page.locator("#vaultList .item").filter(has_text=label).first


@when(parsers.parse('I tap "Type" on the vault entry "{label}"'))
def tap_type(ctx, label):
    _entry(ctx["ui"], label).locator("[data-vtype]").click()
    ctx["ui"].idle(200)


@when(parsers.parse('I tap the "{what}" button on the vault entry "{label}"'))
def tap_entry_btn(ctx, what, label):
    sel = {"Show": "[data-vshow]", "Edit": "[data-ved]"}[what]
    _entry(ctx["ui"], label).locator(sel).click()
    ctx["ui"].idle(200)


@when(parsers.parse('I enter "{pin}" in the PIN row "#{row}"'))
def pin_row(ctx, pin, row):
    ui = ctx["ui"]
    ui.page.wait_for_function("""r => { const b = [...document.querySelectorAll('#' + r + ' .pinbox')];
        return b.length && b.every(x => !x.value) && !document.querySelector('#' + r + '.bad'); }""", arg=row, timeout=4000)
    ui.page.locator(f"#{row} .pinbox").first.click()
    ui.page.wait_for_timeout(120)      # a person never types within the box's own focus tick
    for d in pin:
        ui.page.keyboard.type(d)
        ui.page.wait_for_timeout(40)
    ui.idle(400)


@then(parsers.parse('the vault lists "{label}" as a "{kind}"'))
def vault_lists(ctx, label, kind):
    _wait(ctx["ui"], lambda: kind in _entry(ctx["ui"], label).inner_text() if ctx["ui"].page.locator("#vaultList .item").filter(has_text=label).count() else False,
          f"vault list: {ctx['ui'].text('vaultList')!r}")


@then(parsers.parse('the field "#{el_id}" holds "{value}"'))
def field_holds(ctx, el_id, value):
    assert ctx["ui"].page.locator(f"#{el_id}").input_value() == value


@then(parsers.parse('the field "#{el_id}" holds the stored secret of "{label}"'))
def field_secret(ctx, el_id, label):
    want = next(v["secret"] for v in ctx["sim"].vault.values() if v["label"] == label)
    _wait(ctx["ui"], lambda: ctx["ui"].page.locator(f"#{el_id}").count() and ctx["ui"].page.locator(f"#{el_id}").input_value() == want,
          "secret not shown")


@then(parsers.parse('the board receives a vault save for "{label}" without a secret'))
def save_no_secret(ctx, label):
    _wait(ctx["ui"], lambda: any(c.get("label") == label and "secret" not in c for c in ctx["sim"].received("vault_save")),
          f"vault_save sent: {ctx['sim'].received('vault_save')}")


@then("the vault is empty")
def vault_empty(ctx):
    _wait(ctx["ui"], lambda: "Nothing saved yet" in ctx["ui"].text("vaultList"), ctx["ui"].text("vaultList"))


@then(parsers.parse('the "#{el_id}" control is hidden'))
def hidden(ctx, el_id):
    _wait(ctx["ui"], lambda: not ctx["ui"].page.locator(f"#{el_id}").is_visible(), f"#{el_id} is visible")


def _group(ui, name):
    return ui.page.locator("#scList .scg").filter(has=ui.page.locator("h2", has_text=name)).first


@when(parsers.parse('I open the shortcut group "{name}"'))
def open_group(ctx, name):
    g = _group(ctx["ui"], name)
    if "open" not in g.get_attribute("class").split():       # it may already be open
        g.locator("[data-gi]").click()
        ctx["ui"].idle(200)


@when(parsers.parse('I tap the heading of the shortcut group "{name}"'))
def toggle_group(ctx, name):
    _group(ctx["ui"], name).locator("[data-gi]").click()
    ctx["ui"].idle(200)


@then(parsers.parse('the shortcut group "{name}" is open'))
def group_open(ctx, name):
    assert "open" in _group(ctx["ui"], name).get_attribute("class").split()


@then(parsers.parse('the shortcut group "{name}" is closed'))
def group_closed(ctx, name):
    assert "open" not in _group(ctx["ui"], name).get_attribute("class").split()


@when(parsers.parse('I tap the shortcut "{name}"'))
def tap_shortcut(ctx, name):
    ui = ctx["ui"]
    ui.page.locator("#scList [data-sc]:visible").filter(has=ui.page.locator("b", has_text=name)).first.click()
    ui.idle(200)


@then(parsers.parse('the shortcut "{name}" is visible'))
def sc_visible(ctx, name):
    ui = ctx["ui"]
    _wait(ui, lambda: ui.page.locator("#scList [data-sc]:visible").filter(has=ui.page.locator("b", has_text=name)).count() > 0,
          f"{name} not visible")


@then(parsers.parse('every visible shortcut matches "{q}"'))
def all_match(ctx, q):
    rows = ctx["ui"].page.evaluate("[...document.querySelectorAll('#scList [data-sc]')].filter(b=>b.offsetParent).map(b=>[b.querySelector('b').textContent, b.dataset.sc])")
    assert rows and all(q in n.lower() or q in k.lower() for n, k in rows), rows


@when(parsers.parse('I press the key "{key}" on this device'))
def press_local(ctx, key):
    ctx["ui"].page.keyboard.press(key)
    ctx["ui"].idle(100)


@then(parsers.parse('the board\'s vault PIN is still "{pin}"'))
def vault_pin_kept(ctx, pin):
    assert ctx["sim"].vault_pin == pin


@when(parsers.parse('I tap the app "{name}"'))
def tap_app(ctx, name):
    ui = ctx["ui"]
    ui.page.locator(f'#appChips [data-app="{name}"]').click()
    ui.idle(200)


@when(parsers.parse('I type "{text}" into the app name and press Enter'))
def type_app(ctx, text):
    ui = ctx["ui"]
    ui.page.locator("#appName").click()
    ui.page.keyboard.type(text)
    ui.page.keyboard.press("Enter")
    ui.idle(200)


# ---- text shortcuts --------------------------------------------------------------
def _snip_layout(n):
    """A board layout with n text shortcuts, in the live order: name, age, ID per
    person (made-up values - never a real person's)."""
    quick = [{"n": "Mic mute", "i": "mic", "t": "key", "v": "F20"}]
    for i in range(n):
        p, kind = i // 3 + 1, ("Name", "Age", "ID")[i % 3]
        v = {"Name": f"Person {p}", "Age": str(20 + p), "ID": f"{p:04d}" * 3}[kind]
        quick.append({"n": f"{p} {kind}", "i": "keycap", "t": "text", "v": v})
    return json.dumps({"quick": quick, "knobs": []}, separators=(",", ":"))


@given(parsers.parse("the board has {n:d} text shortcuts"))
def has_snips(ctx, n):
    ctx["sim"].ui = _snip_layout(n)


def _snip(ui, label):
    return ui.page.locator("#snipTiles [data-snip]", has_text=label).first


@when(parsers.parse('I tap the text shortcut "{label}"'))
def tap_snip(ctx, label):
    ui = ctx["ui"]
    _snip(ui, label).scroll_into_view_if_needed()
    _snip(ui, label).click()
    ui.idle(300)


@when(parsers.parse('I long-press the text shortcut "{label}"'))
def hold_snip(ctx, label):
    ui = ctx["ui"]
    _snip(ui, label).evaluate("e => e.setAttribute('data-hold-target', '1')")
    ui.touch_hold('[data-hold-target="1"]', ms=750)
    ui.page.evaluate("document.querySelectorAll('[data-hold-target]').forEach(e => e.removeAttribute('data-hold-target'))")
    ui.idle(300)


@then(parsers.parse("there are {n:d} text shortcuts"))
def snip_count(ctx, n):
    ui = ctx["ui"]
    _wait(ui, lambda: ui.page.locator("#snipTiles [data-snip]").count() == n,
          f'{ui.page.locator("#snipTiles [data-snip]").count()} text shortcuts, not {n}; '
          f'CFG: {ui.page.evaluate("JSON.stringify(CFG).slice(0,200)")}; board: {ctx["sim"].ui[:120]!r}')


@then(parsers.parse('the text shortcut "{label}" is gone'))
def snip_gone(ctx, label):
    labels = ctx["ui"].page.locator("#snipTiles [data-snip] span").all_inner_texts()
    assert label not in labels, labels


@then("no quick action types text")
def no_text_in_media(ctx):
    bad = ctx["ui"].page.evaluate(
        "[...document.querySelectorAll('#quickTiles [data-q]')].map(b => CFG.quick[+b.dataset.q]).filter(q => q.t === 'text').map(q => q.n)")
    assert not bad, f"text shortcuts among the media quick actions: {bad}"


@then(parsers.parse("the board's layout still has {n:d} text shortcuts"))
def board_snips(ctx, n):
    q = json.loads(ctx["sim"].ui)["quick"]
    got = [x["n"] for x in q if x.get("t") == "text"]
    assert len(got) == n, f"the board now has {len(got)} text shortcuts: {got}"


@when(parsers.parse('I hold the text shortcut "{label}" for {ms:d} ms'))
def hold_snip_for(ctx, label, ms):
    ui = ctx["ui"]
    _snip(ui, label).evaluate("e => e.setAttribute('data-hold-target', '1')")
    ui.touch_hold('[data-hold-target="1"]', ms=ms)
    ui.page.evaluate("document.querySelectorAll('[data-hold-target]').forEach(e => e.removeAttribute('data-hold-target'))")
    ui.idle(400)
