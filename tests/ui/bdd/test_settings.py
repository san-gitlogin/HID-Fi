import pytest
from pytest_bdd import given, parsers, scenarios, then, when

pytestmark = pytest.mark.ui
scenarios("features/settings.feature")


def _wait(ui, pred, what, tries=40):
    for _ in range(tries):
        if pred():
            return
        ui.idle(100)
    raise AssertionError(what)


@given(parsers.parse('the board is joining "{ssid}"'))
def joining(ctx, ssid):
    ctx["sim"].sta.update(phase="connecting", ssid=ssid, since=ctx["sim"].now() + 3600)


@given("the board is on the home network")
def on_home(ctx):
    ctx["sim"].sta.update(phase="online", ssid="HomeNet", ip="192.168.1.77")


@given("nobody is on the board's access point")
def no_clients(ctx):
    ctx["sim"].ap_clients = []


@when(parsers.parse('I pick the network "{ssid}"'))
def pick(ctx, ssid):
    ui = ctx["ui"]
    ui.page.locator(f'#wifiList [data-ssid="{ssid}"]').wait_for(timeout=5000)
    ui.page.locator(f'#wifiList [data-ssid="{ssid}"]').click()
    ui.idle(200)


@then(parsers.parse('the field "#{el_id}" shows its text'))
def shows_text(ctx, el_id):
    assert ctx["ui"].page.locator(f"#{el_id}").get_attribute("type") == "text"
    assert ctx["ui"].text("w_eye") == "Hide"


@then(parsers.parse('"#{el_id}" is visible'))
def visible(ctx, el_id):
    _wait(ctx["ui"], lambda: ctx["ui"].page.locator(f"#{el_id}").is_visible(), f"#{el_id} is hidden")


@then(parsers.parse('"#{el_id}" is hidden'))
def hidden(ctx, el_id):
    _wait(ctx["ui"], lambda: not ctx["ui"].page.locator(f"#{el_id}").is_visible(), f"#{el_id} is visible")


@then(parsers.parse('"#{el_id}" is on'))
def is_on(ctx, el_id):
    _wait(ctx["ui"], lambda: "on" in (ctx["ui"].page.locator(f"#{el_id}").get_attribute("class") or "").split(), "off")


@then(parsers.parse('"#{el_id}" is off'))
def is_off(ctx, el_id):
    _wait(ctx["ui"], lambda: "on" not in (ctx["ui"].page.locator(f"#{el_id}").get_attribute("class") or "").split(), "on")


@then(parsers.parse('"#{el_id}" does not contain "{text}"'))
def not_contains(ctx, el_id, text):
    assert text not in ctx["ui"].text(el_id)


@when(parsers.parse('I replace "#{el_id}" with "{text}"'))
@when(parsers.re(r'I replace "#(?P<el_id>\w+)" with ""$'))
def replace(ctx, el_id, text=""):
    loc = ctx["ui"].page.locator(f"#{el_id}")
    loc.click()                        # a guarded field stays read-only until touched
    loc.fill("")
    if text:
        loc.press_sequentially(text, delay=10)
    else:
        loc.dispatch_event("input")
    ctx["ui"].idle(100)


@then(parsers.parse('the field "#{el_id}" is empty'))
def field_empty(ctx, el_id):
    assert ctx["ui"].page.locator(f"#{el_id}").input_value() == ""


@then(parsers.parse('the field "#{el_id}" is disabled'))
def field_disabled(ctx, el_id):
    assert ctx["ui"].page.locator(f"#{el_id}").is_disabled()


@then(parsers.parse('the reboot overlay says "{text}"'))
def boot_overlay(ctx, text):
    ctx["ui"].page.wait_for_selector("#boot.on", timeout=3000)
    assert ctx["ui"].text("bootMsg") == text, ctx["ui"].text("bootMsg")


@when(parsers.parse('I type the new PIN "{pin}"'))
def new_pin(ctx, pin):
    ui = ctx["ui"]
    boxes = ui.page.locator("#pinSet .pinbox")
    boxes.first.evaluate("e => e.scrollIntoView({block: 'center'})")
    boxes.first.click()
    ui.page.wait_for_timeout(120)      # see the pinbox focus loop note in docs/TESTING.md
    for d in pin:
        ui.page.keyboard.type(d)
        ui.page.wait_for_timeout(40)
    ui.idle(150)


@then("the new PIN boxes are empty")
def pin_boxes_empty(ctx):
    _wait(ctx["ui"], lambda: ctx["ui"].page.evaluate(
        "[...document.querySelectorAll('#pinSet .pinbox')].every(b => !b.value)"), "boxes still hold digits")


@when(parsers.parse('I tap the power mode "{mode}"'))
def power(ctx, mode):
    ctx["ui"].page.locator(f'[data-pwr="{mode}"]').click()
    ctx["ui"].idle(250)


@then(parsers.parse("{n:d} confirmation was shown"))
def n_confirms(ctx, n):
    assert len([d for d in ctx["ui"].dialogs if d["type"] == "confirm"]) == n, ctx["ui"].dialogs


@when(parsers.re(r'I type "" into "#(?P<el_id>\w+)"$'))
def type_nothing(ctx, el_id):
    """An example row with no password: the field is left as it is."""


@given("this phone reaches the board over the home network")
def on_lan(ctx):
    ctx["sim"].phone_on_lan = True


@given("the board will not take the phone back after an AP change")
def no_way_back(ctx):
    ctx["sim"].refuse_after_ap = True


@when("I drag one finger across the trackpad")
def drag_pad(ctx):
    ctx["ui"].touch_drag("#pad", [[0, 0], [20, 5], [40, 10], [60, 15], [80, 20]])


@then("the board refused the pointer")
def refused_pointer(ctx):
    ctx["ui"].idle(300)
    # What the firmware does with these frames today: hidMouseMove() on the PC.
    assert not ctx["sim"].frames("move"), f"the board moved the pointer: {ctx['sim'].frames('move')[:3]}"


@when("I open the dashboard on the home network")
def open_on_lan(ctx, browser):
    # The page's start-up commands are refused here, so it never reaches the
    # state "the dashboard is open" waits for; open it and let it settle.
    from support.ui import Dashboard
    ui = Dashboard(browser, ctx["sim"], device=ctx["device"], answers=ctx["answers"])
    ui.page.goto(ctx["sim"].URL)
    ui.page.wait_for_selector("#sheet.on", timeout=8000)
    ctx["ui"] = ui


@given("this phone is on the board's own access point")
def on_ap(ctx):
    from support.board_sim import AP_URL
    ctx["sim"].URL = AP_URL


@when("the board forgets the network before it joins")
def forget_mid_join(ctx):
    ctx["sim"].sta.update(phase="idle", ip="", error="")
    ctx["ui"].idle(1200)


@when(parsers.parse('I press "{key}" in the PIN boxes'))
def pin_key(ctx, key):
    ctx["ui"].page.keyboard.press(key)
    ctx["ui"].page.wait_for_timeout(60)


@when(parsers.parse('I type the rest of the PIN "{digits}"'))
def pin_rest(ctx, digits):
    for d in digits:
        ctx["ui"].page.keyboard.type(d)
        ctx["ui"].page.wait_for_timeout(40)
    ctx["ui"].idle(100)


@when(parsers.parse('I paste "{text}" into the PIN boxes'))
def pin_paste(ctx, text):
    # The board serves plain http, which is not a secure context, so the page has
    # no navigator.clipboard to fill - exactly as on a real board. A paste reaches
    # the page as a paste event carrying the text, which is what this sends.
    ui = ctx["ui"]
    box = ui.page.locator("#pinSet .pinbox").first
    box.evaluate("e => e.scrollIntoView({block: 'center'})")
    box.click()
    ui.page.wait_for_timeout(120)
    box.evaluate("""(e, t) => { const dt = new DataTransfer(); dt.setData('text', t);
        e.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }""", text)
    ui.idle(150)


@then(parsers.parse('the PIN boxes hold "{digits}"'))
def pin_holds(ctx, digits):
    got = ctx["ui"].page.evaluate("[...document.querySelectorAll('#pinSet .pinbox')].map(b => b.value).join('')")
    assert got == digits, got
