#!/usr/bin/env python3
"""README screenshots of recovery mode and factory reset, drawn by the real pages: the
Companion's window from the app's own model states (tests/golden/companion_window_states.tsv)
and the dashboard from the board model. Phone-sized at 2x, like the others in docs/assets/screens.

    python3 tools/readme_shots.py        # writes docs/assets/screens/14..17-*.png
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests" / "ui" / "companion"), str(ROOT / "tests")]   # its conftest first
from conftest import INIT, PAGE, URL, load_states  # noqa: E402  (the window tests' own loader)
from playwright.sync_api import sync_playwright  # noqa: E402

OUT = ROOT / "docs" / "assets" / "screens"
VERSION = next(l.split('"')[1] for l in (ROOT / "companion" / "Cargo.toml").read_text().splitlines()
               if l.startswith("version ="))
W, H = 400, 760


def window(browser, state, then=None, name=""):
    ctx = browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=2, reduced_motion="reduce")
    pg = ctx.new_page()
    html = PAGE.read_text(encoding="utf-8")
    pg.route(URL, lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=html))
    pg.add_init_script(INIT)
    pg.goto(URL)
    m = load_states()[state]
    m["computer"]["version"] = VERSION                       # the golden file says TEST
    pg.evaluate("m => window.render(m)", m)
    pg.wait_for_timeout(300)
    if then:
        then(pg)
        pg.wait_for_timeout(300)
    pg.screenshot(path=str(OUT / name))
    ctx.close()


def open_factory(pg, tick):
    card = pg.locator(".card", has_text="HID-Fi 09F7C8")
    card.locator("[data-role=more]").click()
    card.get_by_role("button", name="Factory reset…").click()
    if tick:
        pg.locator("#frscrim").get_by_label("I understand everything on this board will be erased").check()


def dashboard(browser):
    from support.board_sim import BoardSim
    from support.ui import Dashboard
    sim = BoardSim(pin="4826")
    ui = Dashboard(browser, sim, device="phone 2x")                    # 2x, like the other screenshots
    ui.page.goto(sim.URL)
    ui.page.wait_for_selector("#sheet.on .pinbox", timeout=8000)
    ui.page.locator("#sheet.on .pinbox").first.click()
    for d in "4826":
        ui.page.keyboard.type(d)
        ui.page.wait_for_timeout(40)
    ui.page.wait_for_function("() => !document.getElementById('sheet').classList.contains('on')", timeout=8000)
    ui.page.evaluate("go('setup');frSheet(null)")
    ui.page.get_by_label("I understand everything on this board will be erased").check()
    ui.page.wait_for_timeout(2800)                                   # the sign-in toast fades first
    ui.page.screenshot(path=str(OUT / "17-factory-reset-dashboard.png"))
    ui.ctx.close()


with sync_playwright() as p:
    b = p.chromium.launch()
    window(b, "recovery-two-boards", name="14-companion-recovery.png")
    window(b, "factory-paired", lambda pg: open_factory(pg, True), "15-companion-factory-reset.png")
    window(b, "factory-waiting", lambda pg: open_factory(pg, False), "16-companion-factory-waiting.png")
    dashboard(b)
    b.close()
for n in ("14-companion-recovery.png", "15-companion-factory-reset.png", "16-companion-factory-waiting.png",
          "17-factory-reset-dashboard.png"):
    print(OUT / n)
