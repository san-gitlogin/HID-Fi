import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from support.board_sim import AP_URL

pytestmark = pytest.mark.ui
scenarios("features/home_network.feature")


@given(parsers.parse('the board is on the home network at "{ip}"'))
def on_home_at(ctx, ip):
    ctx["sim"].sta.update(phase="online", ssid="HomeNet", ip=ip)
    ctx["sim"].net["last_ip"] = ip
    ctx["sim"].pin = ctx["sim"].pin or "4826"        # the home network needs a PIN; sign in below
    ctx["init"].append("sessionStorage.setItem('pin','4826')")


@given("this phone is on the board's own WiFi")
def on_ap(ctx):
    ctx["sim"].URL = AP_URL


@given(parsers.parse('this phone has seen the board at "{ip}"'))
def seen_at(ctx, ip):
    ctx["ui"].page.evaluate("ip => localStorage.setItem('homeIp', ip)", ip)


@when("the home network drops the board")
def drops(ctx):
    ctx["sim"].net_drop()
    ctx["ui"].page.evaluate("refresh()")
    ctx["ui"].idle(300)


@when(parsers.parse('the board is back on the home network at "{ip}"'))
def back_at(ctx, ip):
    ctx["sim"].net_back(ip)
    ctx["ui"].idle(400)


@when("the board has not answered for a minute")
def a_minute(ctx):
    page = ctx["ui"].page
    page.evaluate("lostAt = Date.now() - 60000; lostHelp()")
    ctx["ui"].idle(300)


@when("I close the dialog")
def close_dialog(ctx):
    ctx["ui"].page.evaluate("closeSheet()")
    ctx["ui"].idle(200)


@then(parsers.parse('"#{el_id}" does not contain "{text}"'))
def not_contains(ctx, el_id, text):
    assert text not in ctx["ui"].page.locator("#" + el_id).inner_text()


@then(parsers.parse('I never see the message "{text}"'))
def never_toast(ctx, text):
    ctx["ui"].idle(400)                    # toasts last 2.3 s: anything this event raised is still up
    assert not any(text in t for t in ctx["ui"].toasts()), ctx["ui"].toasts()


@then(parsers.parse('"#{el_id}" links to "{href}"'))
def links_to(ctx, el_id, href):
    assert ctx["ui"].page.locator("#" + el_id).get_attribute("href") == href


@given("the board's own WiFi needs no PIN")
def no_pin(ctx):
    ctx["sim"].pin = ""
    ctx["init"][:] = [i for i in ctx["init"] if "setItem('pin'" not in i]


@given(parsers.parse('the board has saved the networks "{names}"'))
def saved_networks(ctx, names):
    sim = ctx["sim"]
    for n in reversed([x.strip() for x in names.split(",")]):   # listed newest first
        sim._net_add(n, "pw-" + n)
    sim._net_worked(sim.sta["ssid"] or "HomeNet")                  # the one it is on has worked


@when(parsers.parse('I forget the saved network "{ssid}"'))
def forget(ctx, ssid):
    ui = ctx["ui"]
    ui.page.wait_for_selector(f'#savedNets [data-forget="{ssid}"]', timeout=5000)
    ui.page.locator(f'#savedNets [data-forget="{ssid}"]').click()
    ui.idle(400)


@when("the board is somewhere none of its networks reach")
def nowhere(ctx):
    ctx["sim"].air = {"SomeoneElse": -40}
    ctx["sim"].net_search()
    ctx["ui"].page.evaluate("refresh()")
    ctx["ui"].idle(300)
