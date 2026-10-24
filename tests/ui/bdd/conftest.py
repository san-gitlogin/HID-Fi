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


# ---- tab swipes: what must never change tab, and what must -------------------
# A quick sideways flick across empty space changes tab. Anything that handles
# its own touches - the pad, the keyboard, a knob, a stick, the computer's
# screen - keeps every gesture, in every direction. Seen live: panning a
# zoomed-in picture changed tab and took the picture away.
GESTURES = r"flick|pan two fingers|swipe three fingers|pinch out|pinch in|double tap then flick|hold then drag|drag slowly"


def gesture(ui, x, y, kind, dx):
    """One gesture at (x, y) that would change tab if it began on empty space."""
    t = ui._touch
    w = ui.page.wait_for_timeout

    def path(fingers, spread=0, steps=(0.25, 0.5, 0.75, 1.0), step_ms=30, hold_ms=0):
        def at(f):
            pts = []
            for i in range(fingers):
                off = (i - (fingers - 1) / 2) * 40
                grow = spread * f * (1 if i else -1) if fingers == 2 and spread else 0
                pts.append((x + off + dx * f + grow, y + 3 * f, i))
            return pts
        t("touchStart", at(0))
        if hold_ms:
            w(hold_ms)
        for f in steps:
            w(step_ms)
            t("touchMove", at(f))
        w(step_ms)
        t("touchEnd", at(1))

    if kind == "flick":
        path(1)
    elif kind == "pan two fingers":
        path(2)
    elif kind == "swipe three fingers":
        path(3)
    elif kind == "pinch out":
        path(2, spread=60)
    elif kind == "pinch in":
        path(2, spread=-15)
    elif kind == "double tap then flick":
        for _ in range(2):
            t("touchStart", [(x, y, 0)]); w(40); t("touchEnd", [(x, y, 0)]); w(80)
        path(1)
    elif kind == "hold then drag":
        path(1, hold_ms=450)
    elif kind == "drag slowly":
        path(1, step_ms=300)
    else:
        raise ValueError(kind)
    ui.idle(300)


@when(parsers.re(r'I (?P<kind>' + GESTURES + r') (?P<direction>left|right) on "(?P<selector>[^"]+)"'))
def gesture_on(ctx, kind, direction, selector):
    ui = _ui(ctx)
    x, y = ui._center(selector)
    ctx["notes"]["view_before"] = ui.view()
    gesture(ui, x, y, kind, -160 if direction == "left" else 160)


@then(parsers.parse('the "{tab}" tab is still shown'))
def tab_still_shown(ctx, tab):
    assert _ui(ctx).view() == TABS[tab], f"the tab changed to {_ui(ctx).view()!r}"


# Every visible surface on the current tab that handles its own touches, as the
# page's CSS says (touch-action), plus the keyboards: one point on each, clear
# of anything on top. Surfaces added later are swept without anyone listing them.
SURFACES_JS = r"""() => {
  const view = document.querySelector('.view.on'); if (!view) return [];
  const owns = e => { const ta = getComputedStyle(e).touchAction;
    return /none|pan-y|pinch-zoom/.test(ta) && !/pan-x|manipulation|auto/.test(ta); };
  const out = [], seen = new Set();
  for (const e of view.querySelectorAll('*')) {
    if (!(owns(e) || e.matches('.kbd'))) continue;
    if (e.parentElement && e.parentElement.closest('.kbd')) continue;   // a key: its keyboard is the surface
    let p = e.parentElement, inner = false;
    for (; p && p !== view; p = p.parentElement) if (owns(p)) { inner = true; break; }
    if (inner) continue;
    const r = e.getBoundingClientRect();
    if (r.width < 24 || r.height < 24 || getComputedStyle(e).visibility === 'hidden') continue;
    const name = e.id ? '#' + e.id : (e.dataset && Object.keys(e.dataset).length ? '[data-' + Object.keys(e.dataset)[0] + '="' + Object.values(e.dataset)[0] + '"]' : e.className.toString().split(' ')[0]);
    if (seen.has(name)) continue; seen.add(name);
    out.push(name);
  }
  return out;
}"""

POINT_JS = r"""(name) => {
  const view = document.querySelector('.view.on');
  const e = name.startsWith('#') || name.startsWith('[') ? view.querySelector(name) : view.querySelector('.' + name);
  if (!e) return null;
  e.scrollIntoView({block: 'center', inline: 'center'});
  const r = e.getBoundingClientRect();
  // A point on the surface itself, not under the tab bar or a toolbar.
  for (const [fx, fy] of [[.5, .5], [.3, .5], [.7, .5], [.5, .3], [.5, .7]]) {
    const x = r.left + r.width * fx, y = r.top + r.height * fy, top = document.elementFromPoint(x, y);
    if (top && e.contains(top)) return [x, y];
  }
  return null;
}"""


@when("I try every tab-changing gesture on every surface that handles its own touches")
def sweep_surfaces(ctx):
    ui = _ui(ctx)
    start = ui.view()
    names = ui.page.evaluate(SURFACES_JS)
    ctx["notes"]["swept"], ctx["notes"]["left_tab"] = names, []
    assert names, f"no surface found on {start!r}"
    for name in names:
        for kind, dx in (("flick", -160), ("flick", 160), ("pan two fingers", -160), ("hold then drag", -160)):
            pt = ui.page.evaluate(POINT_JS, name)
            if not pt:
                continue
            gesture(ui, pt[0], pt[1], kind, dx)
            if ui.view() != start:
                ctx["notes"]["left_tab"].append(f"{kind} {'left' if dx < 0 else 'right'} on {name} -> {ui.view()}")
                ui.page.evaluate(f"go({start!r})")
                ui.idle(200)


@then(parsers.parse('the "{tab}" tab was never left'))
def never_left(ctx, tab):
    assert _ui(ctx).view() == TABS[tab]
    assert not ctx["notes"]["left_tab"], "changed tab: " + "; ".join(ctx["notes"]["left_tab"])
    print("swept:", ctx["notes"]["swept"])


# Empty space: nothing there handles a touch of its own (the page's rule).
EMPTY_JS = r"""([span]) => {
  const main = document.getElementById('main'), m = main.getBoundingClientRect();
  const KEEP = '#padwrap,.kbd,.knob,.mouse,.seg,input,textarea,select,button,a,label,[data-k],[data-mb],[data-md],' +
               '[contenteditable],.scrframe,.scrstage,.scrhead,.scrtools,.scrhole,.scrpadslot,.stick,.dpad,[data-gs]';
  const owned = el => { for (let e = el; e && e !== main; e = e.parentElement) {
      if (e.matches(KEEP)) return true;
      const cs = getComputedStyle(e);
      if (/none|pan-y|pinch-zoom/.test(cs.touchAction) && !/pan-x|manipulation|auto/.test(cs.touchAction)) return true;
      if (/auto|scroll/.test(cs.overflowX) && e.scrollWidth > e.clientWidth + 1) return true; }
    return !el || !el.closest('#main'); };
  const tb = document.getElementById('tabbar').getBoundingClientRect();
  const bottom = Math.min(m.bottom, innerHeight, tb.height ? tb.top : innerHeight) - 20;
  for (let y = m.top + 60; y < bottom; y += 12)
    for (let x = m.left + 20; x < m.right - 20; x += 12) {
      if (x + span > m.right - 10 || x + span < m.left + 10) continue;
      const clear = (px, py) => [[0,0],[28,0],[-28,0],[0,28],[0,-28],[20,20],[-20,-20],[20,-20],[-20,20]]
        .every(([ox, oy]) => !owned(document.elementFromPoint(px + ox, py + oy)));
      if (clear(x, y) && !owned(document.elementFromPoint(x + span, y))) return [x, y];
    }
  return null; }"""


@when(parsers.parse("I flick {direction} across empty space"))
def flick_empty(ctx, direction):
    ui = _ui(ctx)
    dx, dy = {"left": (-160, 0), "right": (160, 0), "up": (0, -160), "down": (0, 160)}[direction]
    pt = None
    for _ in range(6):
        pt = ui.page.evaluate(EMPTY_JS, [dx])
        if pt:
            break
        ui.page.evaluate("document.getElementById('main').scrollBy(0, 300)")
        ui.idle(150)
    assert pt, f"no empty space to flick across on {ui.view()!r}"
    x, y = pt
    if dy:
        gesture_vertical(ui, x, y, dy)
    else:
        gesture(ui, x, y, "flick", dx)


def gesture_vertical(ui, x, y, dy):
    t = ui._touch
    t("touchStart", [(x, y, 0)])
    for f in (0.3, 0.7, 1.0):
        ui.page.wait_for_timeout(30)
        t("touchMove", [(x + 3 * f, y + dy * f, 0)])
    ui.page.wait_for_timeout(30)
    t("touchEnd", [(x + 3, y + dy, 0)])
    ui.idle(300)


# ---- PIN rows typed at speed -------------------------------------------------
# A key the instant a box took focus once set two boxes handing the focus to
# and fro every few ms for good (bindPin's late select()), and the digits
# landed in the wrong boxes. Tap and type at once, as a fast thumb, a paste or
# a password manager does.
@when(parsers.parse('I tap the first box of "#{row}" and type "{digits}" at once'))
def type_at_once(ctx, row, digits):
    ui = _ui(ctx)
    ui.page.evaluate("""() => { window.__focusins = 0;
        document.addEventListener('focusin', () => window.__focusins++, true); }""")
    ui.page.click(f"#{row} .pinbox >> nth=0")
    ui.page.keyboard.type(digits, delay=60)
    ui.idle(150)


@then(parsers.parse('the boxes of "#{row}" read "{digits}"'))
def boxes_read(ctx, row, digits):
    got = "".join(_ui(ctx).page.evaluate(f"[...document.querySelectorAll('#{row} .pinbox')].map(b => b.value)"))
    assert got == digits, f"#{row} reads {got!r}"


@then("the focus stays put")
def focus_still(ctx):
    ui = _ui(ctx)
    before = ui.page.evaluate("window.__focusins")
    ui.idle(400)
    after = ui.page.evaluate("window.__focusins")
    assert after == before, f"the focus moved {after - before} times in 0.4 s with nobody typing"



@then("no dialog is open")
def no_dialog(ctx):
    ui = _ui(ctx)
    ui.idle(300)
    assert not ui.page.evaluate("document.getElementById('sheet').classList.contains('on')"), \
        f"a dialog opened: {ui.sheet_title()!r}"


@given("the board's storage is nearly full")
def storage_nearly_full(ctx):
    # The firmware keeps NVS_RESERVE (160) entries free for the WiFi settings, PIN and vault.
    ctx["sim"].nvs_free_entries = 160      # exactly the reserve: any growth is refused
