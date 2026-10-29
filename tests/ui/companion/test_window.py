"""Steps for features/window.feature: the Companion's window, driven as a
person would, in Chromium and WebKit (conftest.py)."""
import copy
import re

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

pytestmark = pytest.mark.ui

scenarios("features/window.feature")

ADD = "ControlOrMeta"      # the add-to-selection key: Command on a Mac, Control elsewhere


def names(text):
    """'"a", "b" and "c"' -> ['a', 'b', 'c']."""
    return re.findall(r'"([^"]*)"', text)


def card(win, name):
    p = win.page
    c = p.locator(".card").filter(has=p.locator(".name span", has_text=re.compile("^" + re.escape(name) + "$")))
    assert c.count() == 1, f"{c.count()} cards named {name}"
    return c


def new_sent(win):
    s = win.sent
    got = s[getattr(win, "mark", 0):]
    win.mark = len(s)
    return got


# ---- given ----

@given(parsers.parse("the window is {w:d} px wide"))
def _width(win, w):
    win.width = w
    if win.page:
        win.page.set_viewport_size({"width": w, "height": win.height})


@given("the clock is 3 seconds after the last answer")
def _clock(win, states):
    # Playwright's clock takes seconds since the epoch.
    win.clock_at = (states["one-online"]["boards"][0]["latency"]["last_ok"] + 3000) / 1000


@given("the window is open in a browser, not the app")
def _browser(win):
    win.in_browser = True


@then(parsers.parse('the clipboard holds "{text}" where the browser lets a test read it'))
def _clipboard(win, text):
    if win.engine != "chromium":
        return      # WebKit gives a test no clipboard reading permission; the note above is its proof
    assert win.page.evaluate("navigator.clipboard.readText()") == text


@given("the person asked for reduced motion")
def _reduced(win):
    win.reduced = True


# ---- when ----

@when(parsers.re(r'the app shows "(?P<state>[^"]+)"'))
def _show(win, state):
    win.show(state)
    win.page.wait_for_timeout(30)


@when(parsers.re(r'the app shows "(?P<state>[^"]+)" with every round trip 10 ms slower'))
def _show_slower(win, state):
    win.page.evaluate("for (const [,r] of __S.cards) r.e.__mark = 1")
    d = copy.deepcopy(win.states[state])
    for c in d["boards"]:
        if c["latency"] and c["latency"]["ms"] is not None:
            c["latency"]["ms"] += 10
            c["latency"]["last"] += 10
    win.show(state, d)


@when(parsers.re(r'the app shows "(?P<state>[^"]+)" (?P<n>\d+) times with new round trips'))
def _show_many(win, state, n):
    n = int(n)
    times = []
    for i in range(n):
        d = copy.deepcopy(win.states[state])
        for c in d["boards"]:
            if c["latency"] and c["latency"]["ms"] is not None:
                c["latency"]["ms"] = (c["latency"]["ms"] + 7 * (i + 1)) % 240
        win.show(state, d)
        times.append(win.page.evaluate("__S.lastRender"))
    win.update_times = times


@when(parsers.parse('I click "{label}" on "{name}"'))
def _click_on(win, label, name):
    b = card(win, name).get_by_role("button", name=label, exact=True)
    assert b.count() == 1, f"{b.count()} buttons {label!r} on {name}"
    b.click()


@when(parsers.parse('I click "{label}" in the selection bar'))
def _click_bar(win, label):
    win.page.locator("#bulk").get_by_role("button", name=label, exact=True).click()


@when(parsers.re(r'I click "(?P<label>[^"]+)"$'))
def _click(win, label):
    b = win.page.get_by_role("button", name=label, exact=True)
    assert b.count() == 1, f"{b.count()} buttons {label!r}"
    b.click()


@when(parsers.parse('I click the switch "{label}"'))
def _switch(win, label):
    win.page.get_by_role("switch", name=label, exact=True).click()


@when(parsers.parse('I click the card "{name}"'))
def _click_card(win, name):
    card(win, name).locator(".statusline").click()


@when(parsers.parse('I click the card "{name}" holding the add key'))
def _click_card_add(win, name):
    card(win, name).locator(".statusline").click(modifiers=[ADD])


@when(parsers.parse('I click the card "{name}" holding Shift'))
def _click_card_shift(win, name):
    card(win, name).locator(".statusline").click(modifiers=["Shift"])


@when(parsers.parse('I tick the box of "{name}"'))
def _tick(win, name):
    card(win, name).locator("[data-role=sel]").click()


@when("I select every card with Command or Control A")
def _select_all(win):
    win.page.locator("#grid .card").first.locator(".statusline").click()
    win.page.keyboard.press(f"{ADD}+a")


@when(parsers.parse('I open the details of "{name}"'))
@when(parsers.parse('I close the details of "{name}"'))
def _details(win, name):
    card(win, name).locator("[data-role=more]").click()


@when(parsers.parse('I copy "{key}" of "{name}"'))
def _copy(win, key, name):
    card(win, name).get_by_role("button", name=f"Copy {key}", exact=True).click()


@when(parsers.parse('I answer "{answer}"'))
def _answer(win, answer):
    win.page.locator("#scrim .dialog").get_by_role("button", name=answer, exact=True).click()


@when(parsers.parse('I press "{key}"'))
def _press(win, key):
    win.prev_focus = win.page.evaluate("visible().indexOf(document.activeElement && document.activeElement.dataset.mac)")
    win.page.keyboard.press(key)


@when(parsers.parse('I filter "{label}"'))
def _filter(win, label):
    win.page.locator("#filter button", has_text=re.compile("^" + re.escape(label) + r" \d+$")).click()


@when(parsers.parse('I put the focus on "{label}" of "{name}"'))
def _focus_btn(win, label, name):
    card(win, name).get_by_role("button", name=label, exact=True).focus()


@when(parsers.parse('I put the focus on the card "{name}"'))
def _focus_card(win, name):
    card(win, name).focus()


@when("5 seconds pass")
def _pass(win):
    win.page.clock.run_for(5000)


# ---- then: fits ----

@then("nothing scrolls sideways and no text is cut off")
def _fits(win):
    r = win.page.evaluate("""() => {
      const out = {side: document.scrollingElement.scrollWidth - innerWidth, cut: []};
      for (const e of document.querySelectorAll('.btn,.chip,.name span,.pill,.seg button,.switch,.link,.lat,#bcount')) {
        if (!e.offsetParent || e.closest('.details') && !e.closest('.card.open')) continue;
        if (e.scrollWidth > e.clientWidth + 1) out.cut.push(e.textContent.trim().slice(0, 40));
      }
      return out;
    }""")
    assert r["side"] <= 0, f"scrolls sideways by {r['side']} px"
    assert not r["cut"], f"cut off: {r['cut']}"


@then("every button is inside its card or panel and at least 34 px tall")
def _buttons(win):
    bad = win.page.evaluate("""() => {
      const out = [];
      for (const b of document.querySelectorAll('.btn,.switch,.link')) {
        if (!b.offsetParent || b.closest('.details') && !b.closest('.card.open') || b.closest('#bulk:not(.on)')) continue;
        const r = b.getBoundingClientRect(), box = b.closest('.upd,.zone,.card,.banner,.computer,.notes,#bulk,.dialog');
        const p = box && box.getBoundingClientRect();
        if (r.height < 34) out.push(b.textContent.trim() + ': ' + Math.round(r.height) + ' px tall');
        if (p && (r.left < p.left - 1 || r.right > p.right + 1)) out.push(b.textContent.trim() + ': outside its ' + box.className);
      }
      return out;
    }""")
    assert not bad, bad


@then("the page reported no error")
def _no_error(win):
    assert not win.errors, win.errors


@then(parsers.parse("the boards are in {n:d} column(s)"))
def _columns(win, n):
    cols = win.page.evaluate("getComputedStyle(document.getElementById('grid')).gridTemplateColumns.split(' ').length")
    assert cols == n


# ---- then: what it says ----

@then(parsers.parse('the card "{name}" reads "{label}"'))
def _reads(win, name, label):
    assert card(win, name).locator(".st").inner_text() == label


@then(parsers.parse('the card "{name}" shows "{text}"'))
def _shows(win, name, text):
    t = card(win, name).inner_text()
    assert text in t, f"{text!r} not in {t!r}"


@then(parsers.parse('the round trip of "{name}" reads "{text}" and is {quality}'))
def _rtt(win, name, text, quality):
    lat = card(win, name).locator(".lat")
    assert lat.is_visible() and lat.inner_text().strip() == text, lat.inner_text()
    assert quality in lat.get_attribute("class").split(), lat.get_attribute("class")


@then(parsers.parse('the card "{name}" has no round trip'))
def _no_rtt(win, name):
    assert not card(win, name).locator(".lat").is_visible()


@then(parsers.parse('this computer reads "{text}"'))
def _computer(win, text):
    assert text in win.page.locator(".computer").inner_text()


@then(parsers.parse('the card "{name}" shows the code "{code}"'))
def _code(win, name, code):
    assert card(win, name).locator(".digits").inner_text() == code


def order(win):
    return win.page.evaluate("[...document.querySelectorAll('#grid .card .name span')].map(e => e.textContent)")


@then(parsers.parse('the first card is "{name}"'))
def _first(win, name):
    assert order(win)[0] == name, order(win)


@then(parsers.parse('the last card is "{name}"'))
def _last(win, name):
    assert order(win)[-1] == name, order(win)


@then(parsers.parse("the counts read {which}"))
def _counts(win, which):
    t = win.page.locator("#counts").inner_text()
    for n in names(which):
        assert n in t, f"{n!r} not in {t!r}"


@then(parsers.parse('the window reads "{text}"'))
def _window_reads(win, text):
    t = win.page.locator("body").inner_text()
    assert text in t, f"{text!r} not on the page"


@then(parsers.parse("there are {n:d} steps and no card"))
def _steps(win, n):
    assert win.page.locator(".empty li").count() == n
    assert win.page.locator(".card").count() == 0


@then("nothing in the page ran from a name")
def _no_xss(win):
    assert win.page.evaluate("window.__pwned") is None
    assert win.page.locator("#grid img, .computer script").count() == 0


@then(parsers.parse('the card "{name}" has the button "{label}"'))
def _has_button(win, name, label):
    assert card(win, name).get_by_role("button", name=label, exact=True).is_visible()


@then(parsers.parse('the card "{name}" has no button "{label}" in view'))
def _no_button(win, name, label):
    c = card(win, name)
    b = c.get_by_role("button", name=label, exact=True)
    assert b.count() == 1, "it is there - under Details"
    assert b.evaluate("b => !!b.closest('.details')") and "open" not in (c.get_attribute("class") or "").split()


@then(parsers.parse('there is a banner "{title}"'))
def _banner(win, title):
    assert title in win.page.locator("#banners").inner_text()


@then(parsers.parse('no card mentions "{text}"'))
def _no_mention(win, text):
    for t in win.page.locator(".card").all_inner_texts():
        assert text not in t, t


@then(parsers.parse('the switch "{a}" is on and "{b}" is off'))
def _switches(win, a, b):
    p = win.page
    assert p.get_by_role("switch", name=a, exact=True).get_attribute("aria-checked") == "true"
    assert p.get_by_role("switch", name=b, exact=True).get_attribute("aria-checked") == "false"


@then(parsers.parse('"{label}" cannot be clicked'))
def _disabled(win, label):
    assert win.page.get_by_role("button", name=label, exact=True).is_disabled()


@then(parsers.parse('the details of "{name}" read "{key}" as "{value}"'))
def _detail(win, name, key, value):
    got = card(win, name).evaluate("""(c, k) => {
      for (const dt of c.querySelectorAll('dt')) if (dt.textContent === k) return dt.nextElementSibling.querySelector('span').textContent;
      return null; }""", key)
    assert got == value, f"{key}: {got!r}"


@then(parsers.parse('the details of "{name}" are {how}'))
def _details_state(win, name, how):
    c = card(win, name)
    is_open = "open" in (c.get_attribute("class") or "").split()
    assert is_open == (how == "open")
    assert c.locator("[data-role=more]").get_attribute("aria-expanded") == ("true" if is_open else "false")


# ---- then: what the app is asked ----

@then(parsers.re(r'the app is asked (?P<which>"[^"]+"(?:(?:, | and )"[^"]+")*)(?P<only> only)?$'))
def _asked(win, which, only):
    got = new_sent(win)
    want = names(which)
    for w in want:
        assert w in got, f"{w!r} not in {got}"
    if only or len(want) > 1:
        assert sorted(got) == sorted(want), got


@then("the app was asked nothing")
def _asked_nothing(win):
    assert new_sent(win) == []


@then(parsers.parse('a note says "{text}"'))
def _toast(win, text):
    # Copying is asynchronous: the note comes when the clipboard answered.
    win.page.wait_for_function("t => document.getElementById('toast').classList.contains('on') && "
                               "document.getElementById('toast').textContent.includes(t)", arg=text, timeout=3000)


@then(parsers.re(r'a dialog asks "(?P<title>[^"]+)" naming (?P<who>.+)$'))
def _dialog(win, title, who):
    p = win.page
    assert "on" in p.locator("#scrim").get_attribute("class").split()
    assert p.locator("#dt").inner_text() == title
    body = p.locator("#dbody").inner_text()
    for n in names(who):
        assert n in body, f"{n} not named in {body!r}"
    assert p.evaluate("document.activeElement.id") == "dno", "the safe answer has the focus"


@then("no dialog is open")
def _no_dialog(win):
    assert "on" not in (win.page.locator("#scrim").get_attribute("class") or "").split()


@then(parsers.parse('the focus is back on "{label}"'))
def _focus_back(win, label):
    assert win.page.evaluate("document.activeElement.textContent") == label


# ---- then: selection ----

def selected(win):
    return win.page.evaluate("[...document.querySelectorAll('.card[aria-selected=true] .name span')].map(e => e.textContent)")


@then(parsers.parse('only "{name}" is selected'))
def _only(win, name):
    assert selected(win) == [name], selected(win)


@then(parsers.re(r'"(?P<a>[^"]+)" and "(?P<b>[^"]+)" are selected$'))
def _two(win, a, b):
    assert sorted(selected(win)) == sorted([a, b]), selected(win)


@then(parsers.parse("{n:d} cards are selected"))
def _n_selected(win, n):
    assert win.page.evaluate("__S.sel.size") == n, selected(win)


@then("nothing is selected")
def _none(win):
    assert win.page.evaluate("__S.sel.size") == 0 and selected(win) == []


@then(parsers.parse('the selection bar reads "{text}"'))
def _bar(win, text):
    assert "on" in win.page.locator("#bulk").get_attribute("class").split()
    assert win.page.locator("#bcount").inner_text() == text


@then("the selection bar is hidden")
def _bar_hidden(win):
    assert "on" not in (win.page.locator("#bulk").get_attribute("class") or "").split()


@then(parsers.re(r'the selection bar offers (?P<which>.+?)(?P<only> only)?$'))
def _offers(win, which, only):
    got = win.page.locator("#bacts button").all_inner_texts()
    assert got == names(which), got


@then(parsers.re(r'"(?P<name>[^"]+)" has the focus'))
def _focus(win, name):
    assert win.page.evaluate("document.activeElement.querySelector && (document.activeElement.querySelector('.name span')||{}).textContent") == name


@then("the card below has the focus")
def _below(win):
    r = win.page.evaluate("""() => ({i: visible().indexOf(document.activeElement.dataset.mac), n: visible().length,
        cols: getComputedStyle(document.getElementById('grid')).gridTemplateColumns.split(' ').length})""")
    assert r["i"] == min(r["n"] - 1, win.prev_focus + r["cols"]), r


@then(parsers.parse('"{label}" of "{name}" has the focus'))
def _btn_focus(win, label, name):
    assert card(win, name).get_by_role("button", name=label, exact=True).evaluate("b => b === document.activeElement")


@then("no card was built again")
def _kept(win):
    assert win.page.evaluate("[...__S.cards.values()].every(r => r.e.__mark === 1)")


@then(parsers.parse('the card "{name}" arrives with an animation'))
def _arrives(win, name):
    assert "enter" in card(win, name).get_attribute("class").split()


# ---- then: filter ----

@then(parsers.parse("the filter reads {which}"))
def _filter_reads(win, which):
    assert win.page.locator("#filter button").all_inner_texts() == names(which)


@then(parsers.parse("{n:d} cards are shown"))
def _shown(win, n):
    assert win.page.locator("#grid .card").count() == n


@then("there is no filter")
def _no_filter(win):
    assert not win.page.locator("#filter").is_visible()


# ---- then: fast, accessible, calm ----

@then(parsers.parse("drawing took under {ms:d} ms"))
def _draw_time(win, ms):
    t = win.page.evaluate("__S.lastRender")
    assert t < ms, f"{t:.1f} ms"


@then(parsers.parse("each update took under {ms:d} ms"))
def _update_time(win, ms):
    assert max(win.update_times) < ms, [round(t, 1) for t in win.update_times]


@then("every button has an accessible name")
def _names(win):
    bad = win.page.evaluate("""() => [...document.querySelectorAll('button')].filter(b =>
        !(b.getAttribute('aria-label') || b.textContent.trim())).map(b => b.outerHTML.slice(0, 80))""")
    assert not bad, bad


@then("the grid is a list where several can be selected")
def _listbox(win):
    g = win.page.locator("#grid")
    assert g.get_attribute("role") == "listbox" and g.get_attribute("aria-multiselectable") == "true"


@then("every card says its board and state")
def _card_labels(win):
    for c in win.page.locator("#grid .card").all():
        label = c.get_attribute("aria-label")
        assert c.get_attribute("role") == "option" and label.startswith("HID-Fi ") and ", " in label, label
        assert c.get_attribute("aria-selected") in ("true", "false")


@then("the switches say whether they are on")
def _switch_state(win):
    for s in win.page.get_by_role("switch").all():
        assert s.get_attribute("aria-checked") in ("true", "false")


@then("nothing on the page moves")
def _calm(win):
    moving = win.page.evaluate("""() => [...document.querySelectorAll('*')].filter(e => {
        const s = getComputedStyle(e);
        return (s.animationName !== 'none' && parseFloat(s.animationDuration) > 0) ||
               s.transitionDuration.split(',').some(d => parseFloat(d) > 0);
      }).map(e => e.className || e.tagName).slice(0, 5)""")
    assert not moving, moving
