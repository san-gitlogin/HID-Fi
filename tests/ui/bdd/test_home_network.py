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


@given("this phone reaches the board over the home network")
def phone_on_lan(ctx):
    ctx["sim"].phone_on_lan = True


@given(parsers.parse('the board is used over "{ssid}"'))
def used_over(ctx, ssid):
    sim = ctx["sim"]
    row = next(n for n in sim.nets if n["ssid"] == ssid)
    sim.use_count += 1
    row["used"] = sim.use_count


@given(parsers.parse('the paired computer uses "{ssid}"'))
def computer_uses(ctx, ssid):
    used_over(ctx, ssid)
    for n in ctx["sim"].nets:
        n["computer"] = n["ssid"] == ssid


@given(parsers.parse('in the air "{strong}" is strong and "{weak}" is weaker'))
def air_two(ctx, strong, weak):
    sim = ctx["sim"]
    sim.air = {strong: -50, weak: -68}
    sim.sta.update(ssid=strong)


@when(parsers.parse('nobody uses the board on "{ssid}" for 5 minutes'))
def nobody_for_5(ctx, ssid):
    sim = ctx["sim"]
    for n in sim.nets:                         # nobody - not even this page, which is on the board's own WiFi
        n["used"] = 0
        n["computer"] = False
    assert sim.sta["ssid"] == ssid
    assert sim.net_check(300) is not None
    ctx["ui"].idle(400)


@then(parsers.parse('the board stays on "{ssid}" when it looks at whether to move'))
def stays(ctx, ssid):
    sim = ctx["sim"]
    assert sim.net_check(0) is None and sim.net_check(900) is None
    assert sim.sta["ssid"] == ssid


@when("the board looks at whether to move")
def looks(ctx):
    assert ctx["sim"].net_check(0) is not None
    ctx["ui"].idle(400)


@given("the dashboard is opened by the board's name")
def open_by_name(ctx, browser):
    from support.ui import Dashboard
    ctx["ui"] = Dashboard(browser, ctx["sim"], device="phone", answers=ctx["answers"], init=ctx["init"]).open_url(
        f"http://{ctx['sim'].MDNS}.local/")


@given("the dashboard is opened by the board's address")
def open_by_address(ctx, browser):
    from support.ui import Dashboard
    from support.board_sim import LAN_PAGE_URL
    ctx["ui"] = Dashboard(browser, ctx["sim"], device="phone", answers=ctx["answers"], init=ctx["init"]).open_url(LAN_PAGE_URL)
    ctx["ui"].idle(3000)            # the name check: up to 2.5 s


@given("the board's name does not reach this device")
def name_unreachable(ctx):
    ctx["sim"].name_reachable = False


@given("someone else is using the board right on its network")
def someone_else(ctx):
    ctx["sim"].others_on_link = 1


@given("the paired computer reaches the board from another network")
def computer_routed(ctx):
    from support.companion_sim import FakeApp
    sim = ctx["sim"]
    sim.sta.update(phase="online", ssid=sim.nets[0]["ssid"])
    app = FakeApp(sim.comp, ip="192.168.1.60")   # the request arrives from C6's router, on the board's network
    with sim.lock:
        app.hello()
        sim.comp.command("companion_pair", {"id": app.id}, "serial")
        app.advance()
        sim.comp.command("companion_pair", {"id": app.id, "code": app.code}, "serial")
    assert app.beat(via="192.168.0.248").get("status") == "ok"   # its own address behind that router


@when(parsers.parse('I pick "{ssid}" as this device\'s WiFi'))
def pick_bring(ctx, ssid):
    ui = ctx["ui"]
    ui.page.locator(f'[data-bring="{ssid}"]').click()
    ui.idle(600)


@then("the board prefers no network yet")
def prefers_none(ctx):
    assert ctx["sim"].net_preferred() is None, [(n["ssid"], n.get("used"), n.get("computer")) for n in ctx["sim"].nets]


@then("the board says its computer is on another network")
def says_routed(ctx):
    assert ctx["sim"].status().get("comp_routed") is True


@when(parsers.parse('I prefer the saved network "{ssid}"'))
def prefer(ctx, ssid):
    ui = ctx["ui"]
    ui.page.wait_for_selector(f'#savedNets [data-prefer="{ssid}"]', timeout=5000)
    ui.page.locator(f'#savedNets [data-prefer="{ssid}"]').click()
    ui.idle(400)


@then(parsers.parse('the board prefers "{ssid}"'))
def board_prefers(ctx, ssid):
    sim = ctx["sim"]
    p = sim.net_preferred()
    assert p is not None and sim.nets[p]["ssid"] == ssid, [(n["ssid"], n.get("used"), n.get("computer")) for n in sim.nets]


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
