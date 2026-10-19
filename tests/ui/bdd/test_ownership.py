import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from support.board_sim import AP_URL, LAN_IP
from support.ui import Dashboard

pytestmark = pytest.mark.ui
scenarios("features/ownership.feature")

ATTACKER = "192.168.1.66"


def _wait(ui, pred, what, tries=40):
    for _ in range(tries):
        if pred():
            return
        ui.idle(100)
    raise AssertionError(what)


# ---- where the phone is -----------------------------------------------------------
@given("the board is on the home network")
def on_home(ctx):
    ctx["sim"].sta.update(phase="online", ssid="HomeNet", ip="192.168.1.77")


@given(parsers.parse("this phone reaches the board over {network}"))
def reaches_over(ctx, network):
    if network == "the home network":
        ctx["sim"].phone_on_lan = True
    else:
        ctx["sim"].phone_on_lan = False
        ctx["sim"].URL = AP_URL


@given("the board's own WiFi is open")
def ap_open(ctx):
    ctx["sim"].ap_open, ctx["sim"].ap_pass, ctx["sim"].ap_gen = True, "", False


@given(parsers.parse("the board's WiFi password is {kind}"))
def wifi_kind(ctx, kind):
    sim = ctx["sim"]
    if kind == "the one it made itself":
        sim.ap_pass, sim.ap_gen = "k7m2-x9qp-4tre", True
    elif kind == "the old shared default":
        sim.ap_pass, sim.ap_gen = "hid12345", False
    else:
        sim.ap_pass, sim.ap_gen = "my-own-choice-1", False


@given(parsers.parse("the board closes connections that do not sign in within {secs:g} seconds"))
def deadline(ctx, secs):
    ctx["sim"].auth_deadline = secs


@when("I open the dashboard there")
def open_there(ctx, browser):
    ui = Dashboard(browser, ctx["sim"], device="phone", answers=ctx["answers"])
    ui.page.goto(ctx["sim"].URL)
    ui.page.wait_for_function("() => document.getElementById('ltext').textContent === 'live'", timeout=8000)
    ui.idle(600)
    if ui.page.locator("#sheet.on").count():          # the LAN-lock explanation, on a board without a PIN
        ui.page.evaluate("closeSheet()")
    ctx["ui"] = ui


@when("I open the dashboard without signing in")
def open_unsigned(ctx, browser):
    ui = Dashboard(browser, ctx["sim"], device="phone", answers=ctx["answers"])
    ui.page.goto(ctx["sim"].URL)
    ui.page.wait_for_selector("#sheet.on .pinbox", timeout=8000)
    ctx["ui"] = ui


# ---- the first PIN -------------------------------------------------------------------
@when(parsers.parse('I type the new PIN "{pin}"'))
def new_pin(ctx, pin):
    ui = ctx["ui"]
    boxes = ui.page.locator("#pinSet .pinbox")
    boxes.first.evaluate("e => e.scrollIntoView({block: 'center'})")
    boxes.first.click()
    ui.page.wait_for_timeout(120)
    for d in pin:
        ui.page.keyboard.type(d)
        ui.page.wait_for_timeout(40)
    ui.idle(150)


@then("the board has no access PIN still")
def still_no_pin(ctx):
    ctx["ui"].idle(300) if ctx["ui"] else None
    assert ctx["sim"].pin == "", f"the board now has a PIN: {ctx['sim'].pin!r}"


@then("the WiFi password field is empty")
def proof_empty(ctx):
    assert ctx["ui"].page.locator("#pinProofPw").input_value() == ""


@when(parsers.parse('someone on the home network tries to set the PIN "{pin}" without the WiFi password'))
def attacker_claims(ctx, pin):
    ctx["notes"]["reply"] = ctx["sim"].handle({"cmd": "set_auth", "token": pin}, "ws", ip=ATTACKER)[0]


@then(parsers.parse('the board said "{text}"'))
def board_said(ctx, text):
    assert ctx["notes"]["reply"].get("message") == text, ctx["notes"]["reply"]


@then(parsers.parse('"#{el_id}" is visible'))
def visible(ctx, el_id):
    _wait(ctx["ui"], lambda: ctx["ui"].page.locator(f"#{el_id}").is_visible(), f"#{el_id} is hidden")


@then(parsers.parse('"#{el_id}" is hidden'))
def hidden(ctx, el_id):
    _wait(ctx["ui"], lambda: not ctx["ui"].page.locator(f"#{el_id}").is_visible(), f"#{el_id} is visible")


@then("nothing the page received contains the board's WiFi password")
def never_sent(ctx):
    ctx["ui"].idle(500)
    pw = ctx["sim"].ap_pass
    assert ctx["sim"].outbox, "nothing was recorded - the check would prove nothing"
    leaked = [m for m in ctx["sim"].outbox if pw in m]
    assert not leaked, leaked[:2]
    assert pw not in ctx["ui"].page.content()


# ---- explanations ---------------------------------------------------------------------
@then(parsers.parse('the explanation covers "{heading}"'))
def covers(ctx, heading):
    hs = ctx["ui"].page.locator("#sheetin .explain h4").all_inner_texts()
    assert any(heading.lower() == h.strip().lower() for h in hs), hs


@then("the explanation shows both recovery commands")
def both_commands(ctx):
    t = ctx["ui"].text("sheetin")
    assert ".\\flash_esp.ps1 -Recover" in t and "./flash_esp.sh --recover" in t, t


@then(parsers.parse("the password advice {shows}"))
def advice(ctx, shows):
    ui = ctx["ui"]
    adv = ui.page.locator("#apAdvice")
    if shows == "is not shown":
        _wait(ui, lambda: not adv.is_visible(), "advice is showing")
        return
    _wait(ui, lambda: adv.is_visible(), "no advice shown")
    title = ui.text("apAdviceTitle")
    if shows == "warns to change it":
        assert title == "Change this password" and "warnbox" in (adv.get_attribute("class") or ""), title
    else:
        assert title.startswith("Recommended") and "warnbox" not in (adv.get_attribute("class") or ""), title


@then(parsers.parse('the PIN prompt says "{text}"'))
def prompt_says(ctx, text):
    ctx["ui"].page.wait_for_function(
        "t => ((document.getElementById('pinAskMsg') || {}).textContent || '').includes(t)", arg=text, timeout=4000)


@then("the page keeps no PIN to send again")
def keeps_no_pin(ctx):
    ctx["ui"].idle(200)
    assert ctx["ui"].page.evaluate("sessionStorage.getItem('pin')") is None, "a refused PIN is kept and re-sent on reconnect"


# ---- someone else trying ----------------------------------------------------------------
@when(parsers.parse("a device on the home network tries {n:d} wrong PINs"))
def lan_wrong(ctx, n):
    for _ in range(n):
        ctx["sim"].handle({"cmd": "auth", "token": "0000"}, "ws", ip=ATTACKER)


@then("that device is locked out")
def attacker_locked(ctx):
    assert ctx["sim"].pin_locked(ATTACKER)
    r = ctx["sim"].handle({"cmd": "auth", "token": ctx["sim"].pin}, "ws", ip=ATTACKER)[0]
    assert r.get("reply") == "auth_locked", "even the right PIN is refused to a locked-out device"


@when(parsers.parse("{n:d} devices on the home network each try {k:d} wrong PIN"))
def many_devices(ctx, n, k):
    for i in range(n):
        for _ in range(k):
            ctx["sim"].handle({"cmd": "auth", "token": "0000"}, "ws", ip=f"192.168.1.{100 + i}")


@then("a new device on the home network is locked out")
def new_lan_locked(ctx):
    assert ctx["sim"].pin_locked("192.168.1.240")


@then(parsers.parse('the owner\'s phone on the home network can still sign in with "{pin}"'))
def owner_lan(ctx, browser, pin):
    _owner(ctx, browser, LAN_IP, pin, lan=True)


@then(parsers.parse('the owner\'s phone on the board\'s own WiFi can still sign in with "{pin}"'))
def owner_ap(ctx, browser, pin):
    _owner(ctx, browser, None, pin, lan=False)


def _owner(ctx, browser, ip, pin, lan):
    sim = ctx["sim"]
    sim.phone_on_lan = lan
    ui = Dashboard(browser, sim, device="phone", answers=ctx["answers"], ip=ip)
    ui.page.goto(sim.URL)
    ui.page.wait_for_selector("#sheet.on .pinbox", timeout=8000)
    boxes = ui.page.locator("#sheet.on .pinbox")
    boxes.first.click()
    ui.page.wait_for_timeout(120)              # the pinbox focus note, docs/TESTING.md
    for d in pin:
        ui.page.keyboard.type(d)
        ui.page.wait_for_timeout(40)
    ui.page.wait_for_function("() => !document.getElementById('sheet').classList.contains('on')", timeout=5000)
    assert any(s["authed"] for s in sim.sockets if s.get("ip") == (ip or "192.168.4.2")), "not signed in"
    ctx["ui"] = ui


# ---- connections that never sign in ------------------------------------------------------
@when(parsers.parse("{n:d} seconds pass on the page"))
def wait_page(ctx, n):
    ctx["ui"].idle(n * 1000)


@then("the board closed that connection")
def closed(ctx):
    assert ctx["sim"].dropped_unsigned >= 1


@then("the dashboard is signed in")
def signed_in(ctx):
    ui = ctx["ui"]
    ui.page.wait_for_function("() => !document.getElementById('sheet').classList.contains('on')", timeout=6000)
    _wait(ui, lambda: any(s["authed"] and s["open"] for s in ctx["sim"].sockets), "no signed-in connection")


@then("the WiFi password field shows its text")
def proof_shown(ctx):
    assert ctx["ui"].page.locator("#pinProofPw").get_attribute("type") == "text"
    assert ctx["ui"].text("pinProofEye") == "Hide"


@then("the WiFi password field hides its text")
def proof_hidden(ctx):
    assert ctx["ui"].page.locator("#pinProofPw").get_attribute("type") == "password"
