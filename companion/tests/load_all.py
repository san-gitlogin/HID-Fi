"""Everything at once, on the real board: a person's heaviest day, held for a minute.

The real service (real USB listing, network and mDNS; the picture is a test pattern,
as in hardware.py) paired through the real Screen tab, then at the same time:
  - six screens watching: the phone, a 1920x1080 screen in real fullscreen (the
    watch view's Fullscreen button), and four more watch screens;
  - a trackpad: pointer frames over WiFi at 60 a second (net zero), with the click
    path's own ping measuring how long the board takes to answer;
  - keystrokes: Shift pressed and released 5 times a second (the whole keyboard
    path, and nothing happens on this computer);
  - the dashboard's own status polling on every page.
Judged: every screen keeps its picture (frame rate, and the longest freeze), the
pointer's answer time while all of it runs, every page error, and the board's
memory before and after. The board is left as found (the test computer unpaired).

Needs the access PIN in HIDFI_PIN (never written anywhere by this script).
With two boards plugged in it refuses to start unless HIDFI_ONLY_BOARD names the
test board's MAC: the service would otherwise ask the other board to pair too.
Run: cargo build --release -p hidfi-companion --example hw_app
     HIDFI_PIN=... HIDFI_ONLY_BOARD=<MAC> python3 companion/tests/load_all.py [board-url]   (HOLD_S=60, HW_APP=path)
"""
import json, os, pathlib, queue, re, statistics, struct, subprocess, sys, tempfile, threading, time, urllib.request

from playwright.sync_api import sync_playwright
import websocket

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIN = os.environ.get("HIDFI_PIN") or sys.exit("set HIDFI_PIN")
HOLD = float(os.environ.get("HOLD_S", "60"))
HW_APP = os.environ.get("HW_APP") or str(ROOT / "target/release/examples/hw_app")
ONLY = os.environ.get("HIDFI_ONLY_BOARD", "")


def espressif_boards():
    """Every HID-Fi (Espressif) board on this computer's USB, by MAC."""
    if sys.platform != "darwin":
        return []
    out = subprocess.run(["ioreg", "-p", "IOUSB", "-l", "-w0"], capture_output=True, text=True).stdout
    macs = set()
    for dev in out.split("+-o ")[1:]:                 # one block per USB device
        ven = re.search(r'"idVendor" = (\d+)', dev)
        ser = re.search(r'"USB Serial Number" = "([0-9A-Fa-f:]{12,17})"', dev)
        if ven and int(ven.group(1)) == 0x303A and ser:
            macs.add(ser.group(1).replace(":", "").upper())
    return sorted(macs)


# The service serves every board on USB, as the app does: with a second board
# plugged in (an owner's), it would be asked to pair. Refuse unless one is named.
_boards = espressif_boards()
if len(_boards) > 1 and not ONLY:
    sys.exit(f"{len(_boards)} boards on USB: set HIDFI_ONLY_BOARD to the test board's MAC")
if ONLY and ONLY.replace(":", "").upper() not in _boards and _boards:
    sys.exit("HIDFI_ONLY_BOARD is not on this computer's USB")
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, detail, flush=True)


app = subprocess.Popen([HW_APP, tempfile.mkdtemp(prefix="hidfi-load-")],
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
lines = queue.Queue()
latest = {}


def pump():
    for ln in app.stdout:
        v = json.loads(ln)
        latest.update(v)
        lines.put(v)


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


def sign_in(page):
    page.wait_for_selector("#sheet.on .pinbox", timeout=10000)
    page.locator("#sheet.on .pinbox").first.click()
    page.wait_for_timeout(150)
    for d in PIN:
        page.keyboard.type(d)
        page.wait_for_timeout(50)


def unpair_test_pcs(page, pending=True):
    r = page.evaluate("new Promise(r=>send({cmd:'companion_list'},r))") or {}
    gone = 0
    for c in (r.get("paired") or []) + ((r.get("pending") or []) if pending else []):
        if c.get("name") == "HID-Fi hardware test":
            page.evaluate("id=>new Promise(r=>send({cmd:'companion_unpair',id:id},r))", c["id"])
            gone += 1
    return gone


# Every decoded frame's time, kept on the page: the frame rate and the longest gap.
WATCH_FRAMES = """(()=>{window.__t=[];const v=document.getElementById('scrVideo');
  const cb=(now)=>{__t.push(now);v.requestVideoFrameCallback(cb)};v.requestVideoFrameCallback(cb)})()"""
READ_FRAMES = """(()=>{const t=__t;__t=[];if(t.length<2)return {n:t.length,fps:0,gap:1e9};
  let g=0;for(let i=1;i<t.length;i++)g=Math.max(g,t[i]-t[i-1]);
  return {n:t.length,fps:(t.length-1)/((t[t.length-1]-t[0])/1000),gap:g}})()"""


def ws_open(url):
    c = websocket.create_connection(url, timeout=5)
    c.settimeout(0.8)
    try:
        needs = "auth_required" in str(c.recv())
    except websocket.WebSocketTimeoutException:
        needs = False
    c.settimeout(5)
    if needs:
        c.send(json.dumps({"cmd": "auth", "token": PIN}))
        end = time.time() + 5
        while time.time() < end:
            m = c.recv()
            if isinstance(m, str) and "auth_" in m:
                assert "auth_ok" in m, "the PIN was not accepted"
                break
    return c


def trackpad_and_keys(ws_url, stop, out):
    """60 pointer frames a second (net zero), a ping every third, Shift 5x a second."""
    c = ws_open(ws_url)
    lat, i, sent = [], 0, 0
    try:
        while not stop.is_set():
            d = 2 if i % 2 == 0 else -2
            c.send_binary(struct.pack("<Bhhbb", 1, d, 0, 0, 0))
            sent += 1
            if i % 12 == 0:
                c.send_binary(bytes([7, 1]) + b"shift")
                c.send_binary(bytes([7, 0]) + b"shift")
            if i % 3 == 0:
                n = 1_000_000 + i
                c.send_binary(struct.pack("<BI", 4, n))
                t0 = time.perf_counter()
                while True:
                    m = c.recv()
                    if isinstance(m, bytes) and len(m) == 5 and m[0] == 0x81 and struct.unpack("<I", m[1:])[0] == n:
                        lat.append((time.perf_counter() - t0) * 1000)
                        break
            i += 1
            time.sleep(1 / 60)
    finally:
        c.send_binary(bytes([7, 0]) + b"shift")   # anything held is let go
        c.close()
        out.update(lat=lat, frames=sent)


def board_status(url):
    return json.load(urllib.request.urlopen(url + "/api/status", timeout=5))


shots = pathlib.Path(os.environ.get("SHOTS", tempfile.mkdtemp(prefix="hidfi-load-shots-")))
try:
    v = wait_app(lambda v: "Found" in v["board"] or "Ready" in v["board"] or "WaitingForApproval" in v["session"], 40,
                 "the board")
    url = sys.argv[1] if len(sys.argv) > 1 else "http://" + re.search(r"(\d+\.\d+\.\d+\.\d+)", v["board"]).group(1)
    ws_url = "ws://" + url.split("//")[1] + ":81"
    print("board:", url)
    wait_app(lambda v: "WaitingForApproval" in v["session"], 30, "the app asking to pair")
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(url + "/#screen")
        sign_in(pg)
        pg.wait_for_function("SCR.loaded", timeout=10000)
        unpair_test_pcs(pg, pending=False)
        try:
            pg.wait_for_selector("[data-scrpair]", timeout=20000)
            pg.tap("[data-scrpair]")
            v = wait_app(lambda v: "ShowCode" in v["session"], 15, "the code on the computer")
            code = re.search(r'"(\d{6})"', v["session"]).group(1)
            pg.wait_for_selector("[data-scrcode]", timeout=10000)
            pg.tap("[data-scrcode]")
            pg.keyboard.type(code, delay=40)
            pg.tap("[data-scrok]")
            wait_app(lambda v: v["session"] == "Online", 15, "paired and online")
            pc_id = pg.evaluate("SCR.sel")
            pg.wait_for_function("SCR.state==='live' && scrVideo.videoWidth>0", timeout=25000)
            before = board_status(url)

            pages = [pg]
            sizes = [(1920, 1080), (1280, 800), (1024, 768), (800, 600), (640, 480)]
            for vw in sizes:
                c = b.new_context(viewport={"width": vw[0], "height": vw[1]})
                q = c.new_page()
                q.on("pageerror", lambda e: errs.append(str(e)[:200]))
                q.add_init_script(f"localStorage.setItem('scrpc','{pc_id}')")
                q.goto(url + "/#watch")
                sign_in(q)
                q.wait_for_function("SCR.state==='live' && scrVideo.videoWidth>0", timeout=25000)
                pages.append(q)
            wait_app(lambda v: v["watching"] == 6, 15, "six viewers")
            big = pages[1]
            # The bar fades after a few idle seconds; a moving pointer brings it back.
            big.mouse.move(400, 300)
            big.mouse.move(420, 320)
            big.wait_for_function("!document.querySelector('.watchwrap').classList.contains('idle')", timeout=5000)
            big.click("#watchFs")
            big.wait_for_function("!!document.fullscreenElement", timeout=5000)
            check("the 1920x1080 screen went fullscreen through its own button", True)
            for q in pages:
                q.evaluate(WATCH_FRAMES)

            stop, pad = threading.Event(), {}
            t = threading.Thread(target=trackpad_and_keys, args=(ws_url, stop, pad))
            t.start()
            samples = []   # every 10 s: each screen's frame rate and longest freeze
            end = time.time() + HOLD
            while time.time() < end:
                time.sleep(min(10, max(0.1, end - time.time())))
                samples.append([q.evaluate(READ_FRAMES) for q in pages])
            stop.set()
            t.join(15)
            big.screenshot(path=str(shots / "load-fullscreen.png"))
            after = board_status(url)

            names = ["phone", "1920 fullscreen", "1280", "1024", "800", "640"]
            for k, name in enumerate(names):
                fps = [s[k]["fps"] for s in samples]
                gap = max(s[k]["gap"] for s in samples)
                check(f"{name}: keeps its picture the whole {HOLD:.0f} s", min(fps) >= 8 and gap < 1500,
                      f"fps min {min(fps):.0f} / median {statistics.median(fps):.0f}, longest freeze {gap:.0f} ms")
            lat = sorted(pad.get("lat") or [0])
            p50, p95, worst = lat[len(lat) // 2], lat[int(len(lat) * 0.95)], lat[-1]
            check("the trackpad stays responsive under all of it (median under 60 ms, 95% under 150 ms)",
                  p50 < 60 and p95 < 150,
                  f"{pad.get('frames')} pointer frames, {len(lat)} pings: median {p50:.0f} ms, p95 {p95:.0f} ms, worst {worst:.0f} ms")
            fh = [before.get("free_heap"), after.get("free_heap")]
            floor = after.get("heap_floor") or after.get("min_free_heap")
            check("the board's memory holds (free heap back within 16 KB, floor above 40 KB)",
                  fh[0] and fh[1] and fh[0] - fh[1] < 16384 and (floor is None or floor > 40000),
                  f"free {fh[0]} -> {fh[1]}, floor {floor}")
            check("no page errors", not errs, str(errs))
            for q in pages[1:]:
                q.context.close()
            wait_app(lambda v: v["watching"] == 1, 15, "back to one viewer")
        finally:
            try:
                if unpair_test_pcs(pg):
                    print("cleaned up: the test computer is no longer paired")
            except Exception as e:
                print("WARNING: could not clean up the test pairing:", e)
        b.close()
finally:
    try:
        app.stdin.write("quit\n")
        app.stdin.flush()
        app.wait(5)
    except Exception:
        app.kill()
print("screenshots:", shots)
sys.exit(0 if ok else 1)
