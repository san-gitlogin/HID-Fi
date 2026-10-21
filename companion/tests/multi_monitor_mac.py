"""Several monitors, for real, on a Mac with one screen: tools/vdisplay makes
temporary virtual displays (macOS treats them as monitors; ScreenCaptureKit
captures them), and the REAL app, the REAL board and Chrome do the rest.

  4 monitors (built-in + 1920x1080, 2560x1440, portrait 1080x1920) -> the
  dashboard lists them -> a phone and a watch screen show two different ones
  at once, each at its own shape -> one is unplugged -> that viewer falls back
  to the main display and is told -> the displays are removed.

Needs: the app running with Screen Recording allowed, the board on USB + WiFi,
HIDFI_PIN in the environment. Build the tool first:
  clang -fobjc-arc -framework Foundation -framework CoreGraphics \\
        -o /tmp/vdisplay companion/tools/vdisplay/vdisplay.m
Run: HIDFI_PIN=... python3 companion/tests/multi_monitor_mac.py /tmp/vdisplay [board-url]
"""
import os, subprocess, sys, time
from playwright.sync_api import sync_playwright

PIN = os.environ.get("HIDFI_PIN") or sys.exit("set HIDFI_PIN")
VD, URL = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "http://192.168.0.106")
SIZES = [(1920, 1080), (2560, 1440), (1080, 1920)]
ok = True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)

def sign_in(page):
    page.wait_for_selector("#sheet.on .pinbox", timeout=10000)
    page.locator("#sheet.on .pinbox").first.click(); page.wait_for_timeout(150)
    for d in PIN:
        page.keyboard.type(d); page.wait_for_timeout(50)

def shape(page):
    return page.evaluate("[scrVideo.videoWidth, scrVideo.videoHeight]")

def same_shape(wh, size):
    w, h = wh
    return h > 0 and abs(w / h - size[0] / size[1]) < 0.03

vd = subprocess.Popen([VD] + [f"{w}x{h}" for w, h in SIZES], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
ids = [int(vd.stdout.readline().split()[0]) for _ in SIZES]
print("virtual displays:", ids, flush=True)
try:
    time.sleep(4)   # the app checks its display list every 3 s
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        phone = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True).new_page()
        phone.goto(URL + "/#screen"); sign_in(phone)
        phone.wait_for_function("SCR.state==='live' && scrVideo.videoWidth>0", timeout=30000)
        names = phone.evaluate("SCR.displays.map(d=>[d.id,d.name,d.width,d.height,d.main])")
        check("the dashboard lists all four monitors", len(names) == 4, str(names))
        check("the display button is there", phone.locator("#scrDisp").is_visible())
        for vid, size in zip(ids, SIZES):
            phone.evaluate(f"scrDispShow({vid})")
            t0 = time.time()
            try:
                phone.wait_for_function(f"SCR.disp==={vid} && Math.abs(scrVideo.videoWidth/scrVideo.videoHeight - {size[0]/size[1]}) < 0.03", timeout=12000)
            except Exception:
                pass
            check(f"monitor {size[0]}x{size[1]} shows at its shape", same_shape(shape(phone), size),
                  f"{shape(phone)} after {time.time()-t0:.1f}s, badge {phone.inner_text('#scrBadge')!r}")
        phone.screenshot(path="/tmp/hidfi-mm-phone.png")
        # A watch screen on monitor 3 while the phone stays on monitor 2: two at once.
        phone.evaluate(f"scrDispShow({ids[1]})")
        watch = b.new_context(viewport={"width": 1280, "height": 800}).new_page()
        watch.goto(URL + "/#watch"); sign_in(watch)
        watch.wait_for_function("SCR.state==='live' && scrVideo.videoWidth>0", timeout=30000)
        watch.evaluate(f"scrDispShow({ids[2]})")
        watch.wait_for_function(f"SCR.disp==={ids[2]} && scrVideo.videoHeight > scrVideo.videoWidth", timeout=12000)
        phone.wait_for_function(f"SCR.disp==={ids[1]} && Math.abs(scrVideo.videoWidth/scrVideo.videoHeight - {16/9}) < 0.03", timeout=12000)
        check("two viewers watch two different monitors at once",
              same_shape(shape(phone), SIZES[1]) and same_shape(shape(watch), SIZES[2]), f"phone {shape(phone)}, watch {shape(watch)}")
        # Unplug the one the watch screen shows.
        vd.stdin.write("drop 3\n"); vd.stdin.flush(); vd.stdout.readline()
        t0 = time.time()
        watch.wait_for_function("SCR.displays.length===3 && SCR.displays.find(d=>d.id===SCR.disp)?.main", timeout=15000)
        toast = watch.evaluate("[...document.querySelectorAll('.toast')].map(t=>t.textContent).join(' | ')")
        check("unplugged: the watch screen falls back to the main display, and says which left",
              "is not connected" in toast and watch.evaluate("SCR.state") == "live", f"{time.time()-t0:.1f}s, {toast!r}")
        t1 = time.time()
        try:
            watch.wait_for_function("scrVideo.videoWidth > scrVideo.videoHeight", timeout=8000)
        except Exception:
            pass
        check("and its picture is the main display's, not the last frame of the one that left",
              shape(watch)[0] > shape(watch)[1], f"{shape(watch)} {time.time()-t1:.1f}s after the message")
        check("the phone keeps its own monitor meanwhile", phone.evaluate(f"SCR.disp==={ids[1]}") and phone.evaluate("SCR.state") == "live")
        watch.screenshot(path="/tmp/hidfi-mm-watch.png")
        # The wall: every monitor left (built-in + two) at once, in one window.
        wall = b.new_context(viewport={"width": 1600, "height": 1000}).new_page()
        wall.goto(URL + "/#wall"); sign_in(wall)
        wall.wait_for_function("WALL.tiles.length===3 && WALL.tiles.every(t=>t.state==='live' && t.el.querySelector('video').videoWidth>0)", timeout=40000)
        tiles = wall.evaluate("WALL.tiles.map(t=>[t.disp, t.el.querySelector('video').videoWidth, t.el.querySelector('video').videoHeight])")
        check("the wall shows all three monitors live at once, each its own", len({t[0] for t in tiles}) == 3, str(tiles))
        wall.screenshot(path="/tmp/hidfi-mm-wall.png")
        # Every monitor in its own window: windows the board serves for real.
        with wall.context.expect_page() as first:
            wall.click("#wallPopAll")
        time.sleep(6)
        pops = [p for p in wall.context.pages if "#watch" in p.url]
        for p_ in pops:
            try:
                sign_in(p_)
            except Exception:
                pass
        for p_ in pops:
            try:
                p_.wait_for_function("SCR.state==='live' && scrVideo.videoWidth>0", timeout=30000)
            except Exception:
                pass
        shown = sorted((p_.evaluate("SCR.disp"), p_.evaluate("SCR.state")) for p_ in pops)
        check("each monitor opens live in its own window", len(pops) == 3 and all(s == "live" for _, s in shown)
              and len({d for d, _ in shown}) == 3, str(shown))
        b.close()
finally:
    try:
        vd.stdin.write("quit\n"); vd.stdin.flush(); vd.wait(5)
    except Exception:
        vd.kill()
print("virtual displays removed")
sys.exit(0 if ok else 1)
