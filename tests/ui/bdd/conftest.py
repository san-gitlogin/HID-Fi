"""BDD for the dashboard: Gherkin features in ./features, run in Chromium against
the simulated board (support/board_sim.py). The shared vocabulary of steps is
here; steps only one feature needs live in that feature's test module.

Every scenario also fails if the page throws a JavaScript error, and every one
feeds the coverage recorders that test_zz_coverage.py checks at the end.
"""
import json
import re

import pytest
from pytest_bdd import given, parsers, then, when

from support import paths
from support.board_sim import BoardSim
from support.ui import TABS, Dashboard


# A scenario tagged @known_issue documents a real defect that is not fixed yet.
# It is a STRICT expected failure: it shows up in every run, and the moment the
# defect is fixed it fails, so the tag (and the docs entry) get removed.
KNOWN_ISSUES = {
}


def pytest_bdd_apply_tag(tag, function):
    if tag.startswith("known_issue"):
        key = tag.split(":", 1)[1] if ":" in tag else ""
        pytest.mark.xfail(strict=True, reason=KNOWN_ISSUES.get(key, "known issue - docs/TESTING.md#known-issues"))(function)
    return True          # other tags are documentation, not pytest markers


# ---- the browser, the board, the page --------------------------------------------------
@pytest.fixture(scope="session")
def ui_coverage():
    cov = {"touched": set(), "inventory": set(), "ran": set(), "seen": set(), "scenarios": 0, "blocks": {}}
    yield cov
    from support import ui_contract
    ui_contract.save_part(cov)


@pytest.fixture
def sim():
    return BoardSim()


@pytest.fixture
def ctx(browser, sim, ui_coverage):
    """Scenario state: the dashboard (opened by a Given), the board, notes."""
    state = {"ui": None, "sim": sim, "device": "phone", "answers": [], "notes": {}, "init": []}
    yield state
    ui = state["ui"]
    if ui:
        try:
            ui_coverage["touched"] |= ui.touched()
            ui_coverage["inventory"] |= ui.inventory()
            ran, seen = ui.functions_run()
            ui_coverage["ran"] |= ran
            ui_coverage["seen"] |= seen
            for n, cov in ui.blocks.items():
                prev = ui_coverage["blocks"].get(n)
                ui_coverage["blocks"][n] = cov if prev is None else bytearray(x | y for x, y in zip(prev, cov))
            ui_coverage["scenarios"] += 1
            errors = list(ui.errors)
        finally:
            ui.close()
        assert not errors, f"JavaScript errors on the page: {errors}"


def _ui(ctx):
    assert ctx["ui"], "the scenario never opened the dashboard"
    return ctx["ui"]


# ---- Given ------------------------------------------------------------------------------------
@given(parsers.parse('the vault holds a password "{label}"'))
def vault_holds(ctx, label):
    ctx["sim"].vault[0] = {"label": label, "type": "password", "secret": "hunter2-" + label}


# What the phone's browser can do, set before the page loads.
BROWSERS = {
    "cannot vibrate": "delete Navigator.prototype.vibrate;",
    # iOS Safari 17.4+: no vibration API, but <input type=checkbox switch> plays the system tick.
    "is iOS Safari with switch controls": "delete Navigator.prototype.vibrate;"
        "Object.defineProperty(HTMLInputElement.prototype, 'switch', {configurable: true, value: false, writable: true});",
}


@given(parsers.parse("this phone's browser {what}"))
def browser_can(ctx, what):
    ctx["init"].append(BROWSERS[what])
    if ctx["ui"]:                      # already open (a Background): from the next load on
        ctx["ui"].page.add_init_script(BROWSERS[what])


@given("the board has no access PIN")
def no_pin(ctx):
    ctx["sim"].pin = ""


@given(parsers.parse('the board\'s access PIN is "{pin}"'))
def board_pin(ctx, pin):
    ctx["sim"].pin = pin


@given(parsers.parse("the board is plugged into a {os_} computer"))
def plugged_into(ctx, os_):
    ctx["sim"].host_detected = {"Mac": "mac", "Windows": "windows", "Linux": "linux"}.get(os_, os_.lower())


@given(parsers.parse("the dashboard is open on a {device}"))
def open_on(ctx, browser, device):
    ctx["ui"] = Dashboard(browser, ctx["sim"], device=device, answers=ctx["answers"], init=ctx["init"]).open()


@given("the dashboard is open")
def open_default(ctx, browser):
    ctx["ui"] = Dashboard(browser, ctx["sim"], device="phone", answers=ctx["answers"], init=ctx["init"]).open()


@given(parsers.parse('the dashboard is opened at "#{hash_}"'))
def open_at(ctx, browser, hash_):
    ctx["ui"] = Dashboard(browser, ctx["sim"], device="phone", answers=ctx["answers"], init=ctx["init"]).open(hash_)


@given(parsers.parse('the dashboard is open and signed in with "{pin}"'))
def open_signed_in(ctx, browser, pin):
    ctx["sim"].pin = pin
    ui = Dashboard(browser, ctx["sim"], device="phone", answers=ctx["answers"], init=ctx["init"])
    ui.page.goto(ui.sim.URL)
    ui.page.wait_for_selector("#sheet.on .pinbox", timeout=8000)
    _enter_pin(ui, pin)
    ui.page.wait_for_function("() => !document.getElementById('sheet').classList.contains('on')", timeout=5000)
    ui.idle(300)
    ctx["ui"] = ui


# ---- When ---------------------------------------------------------------------------------------
@when(parsers.parse('I open the "{tab}" tab'))
@given(parsers.parse('I am on the "{tab}" tab'))
def open_tab(ctx, tab):
    _ui(ctx).tab(tab)


@when(parsers.parse('I tap "{label}"'))
def tap(ctx, label):
    _ui(ctx).tap(label)


@when(parsers.parse('I tap the "#{el_id}" control'))
def tap_id(ctx, el_id):
    _ui(ctx).tap_id(el_id)


@when(parsers.parse('I enter "{value}" in "{label}"'))
def enter(ctx, value, label):
    _ui(ctx).fill(label, value)


@when(parsers.parse('I will answer "{answer}" to the next question'))
@given(parsers.parse('I will answer "{answer}" to the next question'))
def will_answer(ctx, answer):
    a = {"yes": True, "no": False}.get(answer.lower(), answer)
    if ctx["ui"]:
        ctx["ui"].answers.append(a)
    else:
        ctx["answers"].append(a)


def _enter_pin(ui, pin):
    # After a wrong PIN the boxes shake and clear themselves 420 ms later; typing
    # before that clear lands would be partly wiped. Wait for the real state.
    ui.page.wait_for_function("""() => { const b = [...document.querySelectorAll('#sheet.on .pinbox')];
        return b.length && b.every(x => !x.value) && !document.querySelector('#sheet.on .pinrow.bad'); }""", timeout=4000)
    boxes = ui.page.locator("#sheet.on .pinbox")
    boxes.first.click()
    ui.page.wait_for_timeout(120)      # see the pinbox focus loop note in docs/TESTING.md
    for d in pin:
        ui.page.keyboard.type(d)
        ui.page.wait_for_timeout(40)
    ui.idle(300)


@when(parsers.parse('I enter the PIN "{pin}"'))
def enter_pin(ctx, pin):
    _enter_pin(_ui(ctx), pin)


@when(parsers.parse("{n:d} seconds pass on the board"))
def time_passes(ctx, n):
    ctx["sim"].advance(n)


@given(parsers.parse('the board will refuse the next "{cmd}" with "{message}"'))
@when(parsers.parse('the board will refuse the next "{cmd}" with "{message}"'))
def refuse_next(ctx, cmd, message):
    ctx["sim"].fail_next[cmd] = message


@then(parsers.parse("the board still holds {n:d} vault entry"))
def vault_kept(ctx, n):
    assert len(ctx["sim"].vault) == n


@when(parsers.parse('I enter the board\'s WiFi password "{pw}"'))
def enter_proof(ctx, pw):
    f = ctx["ui"].page.locator("#pinProofPw")
    f.evaluate("e => e.scrollIntoView({block: 'center'})")
    f.click()
    f.fill(pw)


@when("the board becomes unreachable")
def unreachable(ctx):
    sim, ui = ctx["sim"], _ui(ctx)
    sim.refusing, before = True, sim.refused
    sim.drop_all()
    for _ in range(80):                # until the dashboard has tried, and failed, once
        if sim.refused > before:
            break
        ui.idle(100)
    assert sim.refused > before, "the dashboard never tried to reconnect"
    ui.idle(300)


@when("the board takes connections again")
def takes_again(ctx):
    ctx["sim"].refusing = False


@when("the board restarts")
def board_restarts(ctx):
    ctx["sim"].reboot()
    _ui(ctx).idle(300)


@when("the board drops the connection")
def board_drops(ctx):
    ctx["sim"].drop_all()
    _ui(ctx).idle(300)


@when("I wait for the dashboard to reconnect")
@then("the dashboard reconnects")
def reconnects(ctx):
    _ui(ctx).page.wait_for_function("() => document.getElementById('ltext').textContent === 'live'", timeout=8000)


@when("I close the dialog")
def close_dialog(ctx):
    _ui(ctx).page.locator("#sheetin .sheeth .iconbtn").click()
    _ui(ctx).idle(150)


@when("I tap outside the dialog")
def tap_outside(ctx):
    _ui(ctx).page.locator("#sheet").click(position={"x": 5, "y": 5})
    _ui(ctx).idle(150)


@when("I go back in the browser")
def go_back(ctx):
    _ui(ctx).go_back()
    _ui(ctx).idle(300)


@when("I reload the dashboard")
def reload(ctx):
    ui = _ui(ctx)
    ui.reload()
    ui.page.wait_for_function("() => document.getElementById('ltext').textContent === 'live'", timeout=8000)
    if ctx["sim"].pin:
        # The page re-sends the PIN it kept for this browser session, so the prompt
        # may flash and close by itself. Only answer it if it stays open.
        ui.idle(900)
        if ui.page.locator("#sheet.on .pinbox").count() and ui.sheet_title() == "Access PIN":
            _enter_pin(ui, ctx["sim"].pin)
    ui.idle(300)


@when(parsers.parse('I type "{text}" into "#{el_id}"'))
def type_into(ctx, text, el_id):
    ui = _ui(ctx)
    ui.page.locator(f"#{el_id}").click()
    ui.page.locator(f"#{el_id}").fill(text)
    ui.idle(100)


@when(parsers.parse('I press Enter in "#{el_id}"'))
def enter_in(ctx, el_id):
    _ui(ctx).page.locator(f"#{el_id}").press("Enter")
    _ui(ctx).idle(200)


@when("I press Escape")
def press_escape(ctx):
    _ui(ctx).page.keyboard.press("Escape")
    _ui(ctx).idle(300)


# ---- Then -----------------------------------------------------------------------------------------
def _subset(want, got):
    return all(k in got and (got[k] == v or (isinstance(v, dict) and isinstance(got[k], dict) and _subset(v, got[k])))
               for k, v in want.items())


@then(parsers.re(r"the board receives (?P<payload>\{.*\})$"))
def board_receives(ctx, payload):
    want = json.loads(payload)
    cmd = want.get("cmd")
    ui = _ui(ctx)
    deadline = 30
    for _ in range(deadline):
        got = ctx["sim"].received(cmd)
        if any(_subset(want, g) for g in got):
            return
        ui.idle(100)
    raise AssertionError(f"the board never received {want}; it received {ctx['sim'].received(cmd) or ctx['sim'].received()[-8:]}")


@then(parsers.parse('the board does not receive "{cmd}"'))
def board_not_receives(ctx, cmd):
    _ui(ctx).idle(300)
    assert not ctx["sim"].received(cmd), f"the board received {ctx['sim'].received(cmd)}"


@then(parsers.parse('the board receives a "{kind}" frame'))
def board_frame(ctx, kind):
    ui = _ui(ctx)
    for _ in range(30):
        if ctx["sim"].frames(kind):
            return
        ui.idle(100)
    raise AssertionError(f"no {kind!r} frame; frames seen: {ctx['sim'].frames()[-8:]}")


@then(parsers.parse('I see the message "{text}"'))
def see_toast(ctx, text):
    try:
        _ui(ctx).wait_toast(text)
    except Exception:
        raise AssertionError(f"no message containing {text!r}; messages: {_ui(ctx).toasts()}")


@then(parsers.parse('the dialog "{title}" is open'))
def dialog_open(ctx, title):
    ui = _ui(ctx)
    ui.page.wait_for_selector("#sheet.on", timeout=4000)
    assert ui.sheet_title() == title, f"open dialog is {ui.sheet_title()!r}"


@then("no dialog is open")
def no_dialog(ctx):
    ui = _ui(ctx)
    ui.page.wait_for_function("() => !document.getElementById('sheet').classList.contains('on')", timeout=4000)


@then(parsers.parse('I see "{text}"'))
def see_text(ctx, text):
    ui = _ui(ctx)
    loc = ui.scope().get_by_text(text, exact=False)
    try:
        loc.first.wait_for(state="visible", timeout=4000)
    except Exception:
        raise AssertionError(f"{text!r} is not visible on {ui.view()!r}")


@then(parsers.parse('"#{el_id}" reads "{text}"'))
def el_reads(ctx, el_id, text):
    ui = _ui(ctx)
    ui.page.wait_for_function("([i, t]) => (document.getElementById(i)||{}).innerText?.trim() === t", arg=[el_id, text], timeout=4000)


@then(parsers.parse('"#{el_id}" contains "{text}"'))
def el_contains(ctx, el_id, text):
    ui = _ui(ctx)
    try:
        ui.page.wait_for_function("([i, t]) => (document.getElementById(i)||{}).innerText?.includes(t)", arg=[el_id, text], timeout=4000)
    except Exception:
        raise AssertionError(f"#{el_id} reads {ui.text(el_id)!r}, expected it to contain {text!r}")


@then(parsers.parse('the "{tab}" tab is shown'))
def tab_shown(ctx, tab):
    assert _ui(ctx).view() == TABS[tab]


@then(parsers.parse('"{label}" is switched on'))
def switched_on(ctx, label):
    ui = _ui(ctx)
    c = ui.control(label)
    for _ in range(20):
        if "on" in (c.get_attribute("class") or "").split():
            return
        ui.idle(100)
    raise AssertionError(f"{label!r} is not switched on (class={c.get_attribute('class')!r})")


@then(parsers.parse('"{label}" is switched off'))
def switched_off(ctx, label):
    ui = _ui(ctx)
    c = ui.control(label)
    for _ in range(20):
        if "on" not in (c.get_attribute("class") or "").split():
            return
        ui.idle(100)
    raise AssertionError(f"{label!r} is still switched on")


@then(parsers.parse('a confirmation asking "{text}" was shown'))
def confirm_shown(ctx, text):
    msgs = [d["message"] for d in _ui(ctx).dialogs]
    assert any(text in m for m in msgs), f"questions asked: {msgs}"
