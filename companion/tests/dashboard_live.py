"""The real dashboard Screen tab, end to end, in Google Chrome (H.264):

  board model (one instance) serves the dashboard to Chrome AND the companion
  endpoint to the real Rust service (fake USB, test-pattern screen).

  tap Pair -> type the code the app shows -> live picture in the Screen tab
  -> zoom in / fit -> float over another tab -> dock -> unplug ends it.

Chrome's Local Network Access check is off here only because the test page is
injected from a made-up origin; a phone loads the dashboard from the board's
own private address, which may reach the computer's private address.

Run: cargo build --release -p hidfi-companion --example system_app
     python3 companion/tests/dashboard_live.py
"""
import json, os, pathlib, queue, re, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "tests"))
from support.board_sim import BoardSim, URL          # noqa: E402
from support.dashboard import extract_html           # noqa: E402
from playwright.sync_api import sync_playwright      # noqa: E402

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 9)); return s.getsockname()[0]
    finally:
        s.close()

IP = lan_ip()
ok = True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)

sim = BoardSim(pin="4826")
class Board(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        self.send_header("Connection", "close"); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.path == "/api/status":
            with sim.lock:
                return self._send(200, sim.status())
        self._send(404, {"status": "error", "message": "Not found"})
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if self.path == "/api/companion":
            with sim.lock:
                return self._send(*sim.comp.http(body, self.client_address[0]))
        self._send(404, {"status": "error", "message": "Not found"})
board = ThreadingHTTPServer((IP, 0), Board); threading.Thread(target=board.serve_forever, daemon=True).start()

app = subprocess.Popen([str(ROOT / "target/release/examples" / ("system_app.exe" if os.name == "nt" else "system_app")), tempfile.mkdtemp(prefix="hidfi-dash-"),
                        f"http://{IP}:{board.server_address[1]}", "B8:1F:3F:DA:1A:2C"],
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                       env=dict(os.environ, HIDFI_DISPLAYS="4"))   # main 1280x720, then 1280x1024, 1080x1920, 1600x900
lines = queue.Queue()
threading.Thread(target=lambda: [lines.put(json.loads(l)) for l in app.stdout], daemon=True).start()
def wait_app(pred, secs, what):
    end = time.time() + secs; last = None
    while time.time() < end:
        try:
            last = lines.get(timeout=0.2)
        except queue.Empty:
            continue
        if pred(last):
            return last
    raise SystemExit(f"FAIL waiting for {what}; last: {last}")

shots = pathlib.Path(os.environ.get("SHOTS", tempfile.mkdtemp(prefix="hidfi-shots-")))
try:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required",
            "--disable-features=LocalNetworkAccessChecks,BlockInsecurePrivateNetworkRequests"])
        # 2x, as the README's other phone screenshots are (SHOTS keeps them).
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
        pg = ctx.new_page()
        pg.add_init_script("sessionStorage.setItem('pin','4826')")
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        sim.attach(pg, extract_html())
        pg.goto(URL + "#screen")

        wait_app(lambda v: "WaitingForApproval" in v["session"], 15, "the app asking to pair")
        pg.wait_for_selector("[data-scrpair]", timeout=12000)
        check("the request appears in the Screen tab", "System Test PC" in pg.inner_text("#scrList"))
        pg.tap("[data-scrpair]")
        v = wait_app(lambda v: "ShowCode" in v["session"], 10, "the code on the computer")
        code = re.search(r'"(\d{6})"', v["session"]).group(1)
        pg.wait_for_selector("[data-scrcode]", timeout=8000)
        pg.tap("[data-scrcode]")
        pg.keyboard.type(code, delay=40)
        pg.tap("[data-scrok]")
        wait_app(lambda v: v["session"] == "Online", 10, "online")
        pg.wait_for_function("SCR.state==='live'", timeout=20000)
        pg.wait_for_function("document.getElementById('scrVideo').videoWidth>0", timeout=10000)
        first = pg.evaluate("[scrVideo.videoWidth, scrVideo.videoHeight]")
        t_full = time.time()
        # A viewer starts at half size and sharpens once its link shows it can.
        pg.wait_for_function("scrVideo.videoWidth===1280", timeout=8000)
        check("live picture in the Screen tab after pairing by code, sharp within seconds",
              first in ([640, 360], [1280, 720]), f"first {first}, full size after {time.time()-t_full:.1f}s")
        pg.wait_for_function("document.getElementById('scrBadge').innerText.includes('fps')", timeout=6000)
        check("the badge names the computer and the frame rate", "System Test PC" in pg.inner_text("#scrBadge"), pg.inner_text("#scrBadge"))
        check("the trackpad sits in the Screen tab", pg.evaluate("padwrap.parentNode.id") == "scrPadSlot")
        pg.screenshot(path=str(shots / "live-phone.png"))

        pg.tap("#scrIn"); pg.tap("#scrIn")
        s = pg.evaluate("SCR.z.s")
        check("zoom in", abs(s - 2.25) < 0.01, f"{s:.2f}x, badge {pg.inner_text('#scrBadge')!r}")
        pg.tap("#scrFit")
        check("fit returns to the whole screen", pg.evaluate("SCR.z.s") == 1)

        # Three displays: choose another, then lose it.
        pg.wait_for_selector("#scrDisp:not([hidden])", timeout=8000)
        pg.tap("#scrDisp")
        pg.locator("#sheet.on .item", has_text="Display 2").locator("[data-disppick]").tap()
        pg.wait_for_function("scrVideo.videoHeight===1024||scrVideo.videoHeight===512", timeout=8000)
        check("another display, on request", "Display 2" in pg.inner_text("#scrBadge"),
              f"{pg.evaluate('[scrVideo.videoWidth,scrVideo.videoHeight]')} {pg.inner_text('#scrBadge')!r}")
        app.stdin.write("unplug-display 2\n"); app.stdin.flush()
        pg.wait_for_function("scrVideo.videoHeight===720||scrVideo.videoHeight===360", timeout=10000)
        pg.wait_for_function("[...document.querySelectorAll('.toast')].some(t=>t.textContent.includes('Display 2 is not connected'))", timeout=6000)
        check("unplugged: back to the main display, and the dashboard says so",
              pg.evaluate("SCR.state")=="live" and "Built-in display" in pg.inner_text("#scrBadge"), pg.inner_text("#scrBadge"))

        # The wall: every monitor at once, on a desktop browser.
        wctx = b.new_context(viewport={"width": 1280, "height": 760}, device_scale_factor=2)
        wpg = wctx.new_page(); wpg.add_init_script("sessionStorage.setItem('pin','4826')")
        sim.attach(wpg, extract_html()); wpg.goto(URL + "#wall")
        wpg.wait_for_function("WALL.tiles.length===3 && WALL.tiles.every(t=>t.state==='live' && t.el.querySelector('video').videoWidth>0)", timeout=30000)
        check("the wall shows every monitor at once", True, str(wpg.evaluate("WALL.tiles.map(t=>wallName(t.disp))")))
        wpg.wait_for_timeout(1200)
        wpg.screenshot(path=str(shots / "wall-desktop.png"))
        wctx.close()

        pg.tap("#scrPop")
        pg.tap("#tabbar [data-v='media']")
        pg.wait_for_timeout(800)
        check("floating over another tab, still live",
              pg.evaluate("SCR.float && SCR.state==='live' && curView==='media'"))
        check("the trackpad went home with the tab change", pg.evaluate("padwrap.parentNode.id") != "scrPadSlot")
        box = pg.locator("#scrFloat").bounding_box()
        check("the floating window is inside the screen, clear of the tab bar",
              box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= 390 and box["y"] + box["height"] <= 844 - 60, str(box))
        pg.screenshot(path=str(shots / "float-phone.png"))
        pg.tap("#scrFDock")
        check("dock brings it back to the Screen tab", pg.evaluate("!SCR.float && curView==='screen'"))

        # A second device that only watches, and a third dashboard, all signed in
        # with the same PIN at the same time.
        def sign_in(page):
            page.wait_for_selector("#sheet.on .pinbox", timeout=8000)
            page.locator("#sheet.on .pinbox").first.click()
            page.wait_for_timeout(120)
            for d in "4826":
                page.keyboard.type(d); page.wait_for_timeout(40)
        ctx2 = b.new_context(viewport={"width": 1280, "height": 800})
        tv = ctx2.new_page(); sim.attach(tv, extract_html())
        tv.goto(URL + "#watch"); sign_in(tv)
        tv.wait_for_function("SCR.state==='live' && document.getElementById('scrVideo').videoWidth>0", timeout=20000)
        ctx3 = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        third = ctx3.new_page(); sim.attach(third, extract_html())
        third.goto(URL + "#pad"); sign_in(third)
        third.wait_for_function("document.getElementById('ltext').textContent==='live'", timeout=8000)
        wait_app(lambda v: v["watching"] == 2, 8, "two viewers")
        f1 = pg.evaluate("(()=>{window.__f=0;const v=scrVideo;const cb=()=>{__f++;v.requestVideoFrameCallback(cb)};v.requestVideoFrameCallback(cb);return 0})()")
        f2 = tv.evaluate("(()=>{window.__f=0;const v=scrVideo;const cb=()=>{__f++;v.requestVideoFrameCallback(cb)};v.requestVideoFrameCallback(cb);return 0})()")
        time.sleep(3)
        a, c = pg.evaluate("__f") / 3, tv.evaluate("__f") / 3
        check("phone and a watch-only screen play at once", a >= 15 and c >= 15, f"{a:.0f} fps and {c:.0f} fps")
        check("the computer counts two viewers", True)
        # All three run on this one computer: one device, three pages.
        third.evaluate("refresh()"); time.sleep(1.5)
        title = third.evaluate("document.getElementById('connPill').title")
        check("three dashboards signed in with one PIN, counted as one device with three pages",
              third.evaluate("document.getElementById('connN').textContent") == "1" and "(3 pages)" in title, title)
        ctx2.close(); ctx3.close()
        wait_app(lambda v: v["watching"] == 1, 8, "back to one viewer")
        check("closing the watch screen frees its place", True)

        app.stdin.write("unplug\n"); app.stdin.flush()
        pg.wait_for_function("SCR.state!=='live'", timeout=10000)
        pg.wait_for_function("document.getElementById('scrMsg').innerText.length>0", timeout=10000)
        check("unplugging the board ends the picture with a reason", True, pg.inner_text("#scrMsg").replace("\n", " / "))
        app.stdin.write("plug\n"); app.stdin.flush()
        pg.wait_for_function("SCR.state==='live'", timeout=40000)
        check("plugged back in, the picture returns by itself", True)
        check("no page errors", not errs, str(errs))
        b.close()
    app.stdin.write("quit\n"); app.stdin.flush()
finally:
    app.kill(); board.shutdown()
print("screenshots:", shots)
sys.exit(0 if ok else 1)
