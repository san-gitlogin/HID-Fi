"""The real thing: the real board (on this computer's USB and the home WiFi),
the real companion service (real USB listing, real network, real mDNS; only
the picture is a test pattern), and Google Chrome loading the dashboard from
the board itself - no Local Network Access flags, as a phone would.

  app finds the board by USB serial -> asks to pair -> Pair in the Screen tab
  -> type the code -> live picture -> a watch-only screen and a third
  dashboard, one PIN -> four viewers -> a fifth waits -> remove the computer
  (the board is left as it was found).

Needs the access PIN in HIDFI_PIN (never written anywhere by this script).
Run: cargo build --release -p hidfi-companion --example hw_app
     HIDFI_PIN=... python3 companion/tests/hardware.py [board-url]
"""
import json, os, pathlib, queue, re, subprocess, sys, tempfile, threading, time, urllib.request
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIN = os.environ.get("HIDFI_PIN") or sys.exit("set HIDFI_PIN")
ok = True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)

app = subprocess.Popen([str(ROOT / "target/release/examples/hw_app"), tempfile.mkdtemp(prefix="hidfi-hw-")],
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
lines = queue.Queue(); latest = {}
def pump():
    for l in app.stdout:
        v = json.loads(l); latest.update(v); lines.put(v)
threading.Thread(target=pump, daemon=True).start()
def wait_app(pred, secs, what):
    end = time.time() + secs
    while time.time() < end:
        if latest and pred(latest):
            return dict(latest)
        try:
            lines.get(timeout=0.2)
        except queue.Empty:
            pass
    raise SystemExit(f"FAIL waiting for {what}; last: {latest}")

def frames(page, secs=3):
    page.evaluate("(()=>{window.__f=0;const v=scrVideo;const cb=()=>{__f++;v.requestVideoFrameCallback(cb)};v.requestVideoFrameCallback(cb)})()")
    time.sleep(secs); return page.evaluate("__f") / secs

def sign_in(page):
    page.wait_for_selector("#sheet.on .pinbox", timeout=10000)
    page.locator("#sheet.on .pinbox").first.click(); page.wait_for_timeout(150)
    for d in PIN:
        page.keyboard.type(d); page.wait_for_timeout(50)

shots = pathlib.Path(os.environ.get("SHOTS", tempfile.mkdtemp(prefix="hidfi-hw-shots-")))
pc_id = None
try:
    v = wait_app(lambda v: "Found" in v["board"] or "Ready" in v["board"] or "WaitingForApproval" in v["session"], 40, "the board")
    url = sys.argv[1] if len(sys.argv) > 1 else "http://" + re.search(r"(\d+\.\d+\.\d+\.\d+)", v["board"]).group(1)
    print("board:", url, "|", v["board"][:120])
    st = json.load(urllib.request.urlopen(url + "/api/status", timeout=5))
    check("the board's status speaks companion", "comp_pcs" in st or any(k.startswith("comp_") for k in st), str([k for k in st if k.startswith("comp")]))
    wait_app(lambda v: "WaitingForApproval" in v["session"], 30, "the app asking to pair")
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(url + "/#screen"); sign_in(pg)
        pg.wait_for_selector("[data-scrpair]", timeout=20000)
        check("the request appears in the real Screen tab", "HID-Fi hardware test" in pg.inner_text("#scrList"))
        pg.tap("[data-scrpair]")
        v = wait_app(lambda v: "ShowCode" in v["session"], 15, "the code on the computer")
        code = re.search(r'"(\d{6})"', v["session"]).group(1)
        pg.wait_for_selector("[data-scrcode]", timeout=10000)
        pg.tap("[data-scrcode]"); pg.keyboard.type(code, delay=40); pg.tap("[data-scrok]")
        wait_app(lambda v: v["session"] == "Online", 15, "paired and online")
        pc_id = pg.evaluate("SCR.sel")
        t0 = time.time()
        pg.wait_for_function("SCR.state==='live' && document.getElementById('scrVideo').videoWidth>0", timeout=25000)
        first_s = time.time() - t0
        pg.wait_for_function("scrVideo.videoWidth===1280", timeout=10000)   # half size first, then sharp
        check("live picture through the real board, no flags", pg.evaluate("[scrVideo.videoWidth,scrVideo.videoHeight]") == [1280, 720],
              f"{first_s:.1f}s to first frame, {time.time()-t0:.1f}s to full size")
        f = frames(pg); check("frame rate on the phone", f >= 20, f"{f:.0f} fps")
        pg.screenshot(path=str(shots / "hw-phone.png"))

        # wrong code must not pair; covered in model tests - here: more viewers, one PIN
        pages = []
        for i, (vw, frag) in enumerate([((1280, 800), "#watch"), ((1024, 768), "#watch"), ((800, 600), "#watch")]):
            c = b.new_context(viewport={"width": vw[0], "height": vw[1]}); q = c.new_page()
            q.on("pageerror", lambda e: errs.append(str(e)[:200]))
            q.goto(url + "/" + frag); sign_in(q)
            q.wait_for_function("SCR.state==='live' && document.getElementById('scrVideo').videoWidth>0", timeout=25000)
            pages.append((c, q))
        wait_app(lambda v: v["watching"] == 4, 10, "four viewers")
        rates = [frames(q, 3) for _, q in [(None, pg)] + pages]
        check("four screens watch at once", min(rates) >= 12, " / ".join(f"{r:.0f}" for r in rates) + " fps")
        pages[0][1].screenshot(path=str(shots / "hw-watch.png"))
        c5 = b.new_context(viewport={"width": 800, "height": 600}); q5 = c5.new_page()
        q5.goto(url + "/#watch"); sign_in(q5); time.sleep(6)
        check("a fifth waits, it does not push anyone out",
              q5.evaluate("SCR.state") != "live" and latest["watching"] == 4, f"fifth: {q5.evaluate('SCR.state')}, app: {latest['watching']}")
        pages[2][0].close()
        q5.wait_for_function("SCR.state==='live'", timeout=30000)
        check("the fifth joins by itself when one closes", True)
        n = pg.evaluate("+document.getElementById('connN').textContent")
        check("the board counts every dashboard, one PIN", n >= 4, f"{n} dashboards")
        for c, _ in pages[:2]: c.close()
        c5.close()
        wait_app(lambda v: v["watching"] == 1, 15, "back to one viewer")
        check("closed screens free their places", True)
        check("no page errors", not errs, str(errs))

        # Leave the board as found: remove this computer.
        pg.on("dialog", lambda d: d.accept())
        pg.click(f"[data-scrdel='{pc_id}']")
        pg.wait_for_function(f"!SCR.pcs.some(p=>p.id==={json.dumps(pc_id)})", timeout=10000)
        pc_id = None
        check("removed from the board again", True)
        b.close()
finally:
    try:
        app.stdin.write("quit\n"); app.stdin.flush(); app.wait(5)
    except Exception:
        app.kill()
    if pc_id:
        print("WARNING: the test computer is still paired on the board; remove it on the Screen tab")
print("screenshots:", shots)
sys.exit(0 if ok else 1)
