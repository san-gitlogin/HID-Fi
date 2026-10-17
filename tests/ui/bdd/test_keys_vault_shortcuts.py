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
