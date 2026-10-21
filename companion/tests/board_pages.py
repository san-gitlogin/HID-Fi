"""How many dashboard pages the REAL board serves at once, and what the next
one sees. Seen live: the 6th page (three phones, a watch window each) showed
"offline" and a PIN box that could not work - the WebSockets library had 5
slots, whatever the firmware asked for. Now: ws_max (8) pages connect, the
next one is told the board is full, and connects by itself when one closes.
While the board is full, the pages already open still get answers fast.

Pages already open elsewhere (a phone, a laptop tab) count: this fills up to
the limit from wherever it stands, and closes only its own pages.

Run: HIDFI_PIN=... python3 companion/tests/board_pages.py [board-url]
"""
import json, os, sys, time, urllib.request
from playwright.sync_api import sync_playwright

PIN = os.environ.get("HIDFI_PIN") or sys.exit("set HIDFI_PIN")
URL = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.0.106"
ok = True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)

def status():
    return json.load(urllib.request.urlopen(URL + "/api/status", timeout=5))

def sign_in(page):
    page.wait_for_selector("#sheet.on .pinbox", timeout=10000)
    page.locator("#sheet.on .pinbox").first.click(); page.wait_for_timeout(150)
    for d in PIN:
        page.keyboard.type(d); page.wait_for_timeout(50)

st = status()
check("the board says how many pages it serves", st.get("ws_max") == 8, f"ws_max={st.get('ws_max')}, open now {st.get('ws_clients')}")
cap = st.get("ws_max") or 8
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    mine = []
    try:
        while status()["ws_clients"] < cap:
            c = b.new_context(viewport={"width": 390, "height": 844}); pg = c.new_page()
            pg.goto(URL + "/#media"); sign_in(pg)
            pg.wait_for_function("document.getElementById('ltext').textContent==='live'", timeout=10000)
            mine.append((c, pg))
        check(f"{cap} pages connect at once", status()["ws_clients"] == cap, f"{len(mine)} of them opened here")
        # Answers still come quickly with every slot taken.
        t = []
        for _, pg in mine[:3]:
            t0 = time.time(); pg.evaluate("new Promise(r=>send({cmd:'ping'},r))"); t.append((time.time() - t0) * 1000)
        check("the open pages still answer quickly", max(t) < 400, " / ".join(f"{x:.0f}" for x in t) + " ms")
        c9 = b.new_context(viewport={"width": 390, "height": 844}); p9 = c9.new_page()
        p9.goto(URL + "/")
        p9.wait_for_selector("#sheet.on", timeout=15000)
        title = p9.inner_text("#sheet.on").split("\n")[0]
        check("one more is told the board is full, not 'offline'", "full" in title.lower(), repr(title))
        mine.pop()[0].close()
        p9.wait_for_function("document.getElementById('ltext').textContent==='live'", timeout=15000)
        check("and connects by itself when a page closes", True)
        c9.close()
    finally:
        for c, _ in mine:
            c.close()
        b.close()
time.sleep(3)
print("pages open now:", status()["ws_clients"])
sys.exit(0 if ok else 1)
