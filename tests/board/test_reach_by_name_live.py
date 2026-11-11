"""A phone on another network, on the real board through the real dashboard (D-10).

A device routed in from another network (a router behind a router) can open the
board by IP but never by its .local name. That is staged here without a second
router: Chromium is told the name does not exist (--host-resolver-rules), which is
exactly what such a phone experiences, while the page, its WebSocket, the PIN and
the board's counting are all real.

| Opened | The name | Must happen |
|---|---|---|
| by name | resolves | counted as using the board, no banner |
| by IP | resolves | the page says net_here: counted, no banner |
| by IP | does not | the banner, not counted; both buttons work |
| Bring here | busy | the person is asked first; yes moves the board (staged network) |

"Counted" is read from the board itself: status `ws_onlink`, the signed-in pages
right on its network (the Companion, which comes and goes on its own heartbeat, is
counted apart and so cannot blur the reading). Needs --board, the board on its home network and
its access PIN in HIDFI_PIN (never printed). Puts everything back.
"""
import json
import time

import pytest

pytestmark = pytest.mark.board

SIM = "HIDFI-SIM-B"


@pytest.fixture(scope="module")
def live(request, _board_holder):
    pytest.importorskip("playwright.sync_api", reason="pip install playwright && playwright install chromium")
    board = _board_holder.get()
    end = time.time() + 60
    st = board.status()
    while not st.get("wifi_connected") and time.time() < end:
        time.sleep(1)
        st = board.status()
    if not st.get("wifi_connected"):
        pytest.skip("the board is not on a home network")
    pin = request.config.getoption("--board-pin")
    if st.get("auth_set") and not pin:
        pytest.skip("the board has an access PIN: give it in HIDFI_PIN")
    nets = board.cmd({"cmd": "wifi_networks"}, "wifi_networks")["networks"]
    home = next(n["ssid"] for n in nets if n.get("current"))
    return {"board": board, "ip": st["wifi_ip"], "name": st["mdns"], "pin": pin or "", "home": home,
            "auth": bool(st.get("auth_set")), "slots": len(nets)}


def _pages(board):
    """Signed-in pages right on the board's network now, from the board itself."""
    return board.status().get("ws_onlink", -1)


def _settled(board):
    """The count once pages closed by an earlier test have gone."""
    last, end = _pages(board), time.time() + 20
    while time.time() < end:
        time.sleep(2)
        now = _pages(board)
        if now == last:
            return now
        last = now
    return last


def _wait_pages(board, want, secs=10):
    end, got = time.time() + secs, None
    while time.time() < end:
        got = _pages(board)
        if got == want:
            return got
        time.sleep(0.5)
    return got


def _using(board, home):
    """Devices right on the board's network now (pages and the Companion) - over USB,
    asking to move where it already is; changes nothing while anyone is."""
    r = board.cmd({"cmd": "wifi_prefer", "ssid": home, "now": True}, "wifi_prefer")
    assert r.get("status") == "ok", r
    return r.get("using", 0) if r.get("busy") else 0


@pytest.fixture(scope="module")
def browsers(live):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        plain = p.chromium.launch()
        routed = p.chromium.launch(args=[f"--host-resolver-rules=MAP {live['name']}.local ~NOTFOUND"])
        yield {"plain": plain, "routed": routed}
        plain.close()
        routed.close()


def _open(browser, live, url):
    ctx = browser.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True)
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=20000)
    if live["auth"]:
        page.wait_for_selector("#sheet.on #pinAsk .pinbox", timeout=15000)
        boxes = page.locator("#sheet.on #pinAsk .pinbox")
        boxes.first.click()
        for d in live["pin"]:
            page.keyboard.type(d)
            page.wait_for_timeout(40)
        page.wait_for_selector("#sheet.on #pinAsk", state="detached", timeout=10000)
    page.wait_for_timeout(3500)                    # the name check gives up at 2.5 s
    return ctx, page


def test_opened_by_name_it_counts_and_shows_no_banner(live, browsers):
    base = _settled(live["board"])
    ctx, page = _open(browsers["plain"], live, f"http://{live['name']}.local/")
    try:
        assert page.locator("#netAway").is_hidden()
        assert _wait_pages(live["board"], base + 1) == base + 1, "a page opened by name must count"
    finally:
        ctx.close()


def test_opened_by_address_on_the_network_it_checks_the_name_and_counts(live, browsers):
    base = _settled(live["board"])
    ctx, page = _open(browsers["plain"], live, f"http://{live['ip']}/")
    try:
        assert page.locator("#netAway").is_hidden(), "the name resolves here: no banner"
        assert _wait_pages(live["board"], base + 1) == base + 1, "net_here must count the page"
    finally:
        ctx.close()


def test_from_another_network_the_banner_shows_and_the_visit_does_not_count(live, browsers):
    base = _settled(live["board"])
    ctx, page = _open(browsers["routed"], live, f"http://{live['ip']}/")
    try:
        assert page.locator("#netAway").is_visible(), "the name cannot open from here: the banner must say so"
        assert page.locator("#netAwayName").inner_text() == f"{live['name']}.local"
        assert "192.168" not in page.locator("#netAway").inner_text(), "no addresses to remember"
        assert live["board"].status().get("ws_clients", 0) >= 1, "the routed page is connected"
        time.sleep(3)
        assert _pages(live["board"]) == base, "a routed visit must not count"
        page.click("#netOwn")
        sheet = page.locator("#sheet.on").inner_text()
        assert f"{live['name']}.local" in sheet and "ESP32-HID-" in sheet, sheet
        page.evaluate("closeSheet()")
        page.click("#netAwayX")
        assert page.locator("#netAway").is_hidden()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(1500)
        if live["auth"] and page.locator("#sheet.on #pinAsk .pinbox").count():   # this visit may still be signed in
            page.locator("#sheet.on #pinAsk .pinbox").first.click()
            for d in live["pin"]:
                page.keyboard.type(d)
                page.wait_for_timeout(40)
        page.wait_for_timeout(3500)
        assert page.locator("#netAway").is_hidden(), "dismissed stays dismissed for this visit"
    finally:
        ctx.close()


def test_bring_the_board_here_asks_first_when_someone_is_using_it(live, browsers):
    board, home = live["board"], live["home"]
    if live["slots"] >= 5:
        pytest.skip("all 5 saved-network slots are in use")
    if _using(board, home) == 0:
        pytest.skip("nobody else is using the board right now (the Companion is not running): nothing to ask about")
    board.cmd({"cmd": "net_sim", "nets": [{"ssid": SIM, "rssi": -50}], "owners": home}, "net_sim")
    board.cmd({"cmd": "wifi_configure", "ssid": SIM, "password": "rehearsal-only"})
    board.cmd({"cmd": "wifi_prefer", "ssid": home, "now": True, "force": True}, "wifi_prefer")
    try:
        end = time.time() + 60
        while time.time() < end and not next((n for n in board.cmd({"cmd": "wifi_networks"}, "wifi_networks")["networks"]
                                              if n.get("current") and n["ssid"] == home), None):
            time.sleep(1)
        ctx, page = _open(browsers["routed"], live, f"http://{live['ip']}/")
        try:
            asked = []
            page.on("dialog", lambda d: (asked.append(d.message), d.accept()))
            page.click("#netBring")
            try:
                page.wait_for_selector(f'#sheet.on [data-bring="{SIM}"]', timeout=5000)
            except Exception:
                saved = [n["ssid"] for n in board.cmd({"cmd": "wifi_networks"}, "wifi_networks")["networks"]]
                shown = page.evaluate("[...document.querySelectorAll('[data-bring]')].map(b => b.dataset.bring)")
                toasts = page.evaluate("[...document.querySelectorAll('.toast')].map(t => t.innerText)")
                raise AssertionError(f"{SIM} not offered. saved={saved} offered={shown} toasts={toasts}")
            assert not page.locator(f'#sheet.on [data-bring="{home}"]').count(), "the network it is on is not offered"
            page.click(f'#sheet.on [data-bring="{SIM}"]')
            end = time.time() + 10
            while time.time() < end and not asked:
                page.wait_for_timeout(200)
            assert asked and "Someone is using the board" in asked[0], asked
            end, on = time.time() + 60, None
            while time.time() < end:
                on = next((n["ssid"] for n in board.cmd({"cmd": "wifi_networks"}, "wifi_networks")["networks"]
                           if n.get("current")), None)
                if on == SIM:
                    break
                time.sleep(1)
            assert on == SIM, f"the board did not move after yes (on {on})"
        finally:
            ctx.close()
    finally:
        board.cmd({"cmd": "net_sim", "off": True}, "net_sim")
        board.cmd({"cmd": "wifi_forget", "ssid": SIM})
        board.cmd({"cmd": "wifi_prefer", "ssid": home, "now": True, "force": True}, "wifi_prefer")
        end = time.time() + 90
        while time.time() < end:
            nets = board.cmd({"cmd": "wifi_networks"}, "wifi_networks")["networks"]
            if any(n["ssid"] == home and n.get("current") and n.get("preferred") for n in nets):
                break
            time.sleep(1)
