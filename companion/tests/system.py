"""The whole system without the hardware: the real companion service (fake
USB, test-pattern screen) + the board model over real HTTP on this machine's
LAN address + real Google Chrome running companion/web/viewer.js as the
dashboard would.

  pair with the code -> online -> ticket from the board -> live video
  -> unplug the board from "USB" -> viewing ends, new viewers refused
  -> plug back -> online again -> live again

Run: cargo build --release -p hidfi-companion --example system_app
     python3 companion/tests/system.py
"""
import json, os, pathlib, queue, re, subprocess, sys, tempfile, threading, time, socket, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAC = "B8:1F:3F:DA:1A:2C"   # what the model's status reports

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 9)); ip = s.getsockname()[0]
    finally:
        s.close()
    import ipaddress
    # No home LAN (an IPv6-only hotspot gives 192.0.0.2): the viewers are here.
    return ip if ipaddress.ip_address(ip).is_private and not ip.startswith("192.0.0.") else "127.0.0.1"

IP = lan_ip()
ok = True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)

board = subprocess.Popen([sys.executable, str(ROOT / "tests" / "model_board.py"), "4826"], env=dict(os.environ, HOST=IP),
                         stdout=subprocess.PIPE, text=True)
bport = int(board.stdout.readline())
BURL = f"http://{IP}:{bport}"
def dash(**cmd):
    r = urllib.request.Request(BURL + "/__dash", data=json.dumps(cmd).encode(), method="POST")
    return json.loads(urllib.request.urlopen(r, timeout=5).read())

store = tempfile.mkdtemp(prefix="hidfi-sys-")
app = subprocess.Popen([str(ROOT / "target/release/examples" / ("system_app.exe" if os.name == "nt" else "system_app")), store, BURL, MAC],
                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
lines = queue.Queue()
threading.Thread(target=lambda: [lines.put(json.loads(l)) for l in app.stdout], daemon=True).start()
last = {}
def wait_for(pred, secs, what):
    global last
    end = time.time() + secs
    while time.time() < end:
        try:
            last = lines.get(timeout=0.2)
        except queue.Empty:
            continue
        if pred(last):
            return last
    raise SystemExit(f"FAIL waiting for {what}; last view: {last}")
def say(cmd):
    app.stdin.write(cmd + "\n"); app.stdin.flush()

page_html = "<!doctype html><meta charset=utf-8><video id=v autoplay muted playsinline></video><script>" + \
            (ROOT / "web" / "viewer.js").read_text() + "</script>"
class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        b = page_html.encode(); self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
web = ThreadingHTTPServer((IP, 0), Page); threading.Thread(target=web.serve_forever, daemon=True).start()

try:
    wait_for(lambda v: "WaitingForApproval" in v["session"], 15, "a pairing request")
    pend = dash(cmd="companion_list")["pending"]
    check("the dashboard sees this computer asking to pair", pend and pend[0]["name"] == "System Test PC", str(pend))
    pc = pend[0]["id"]
    check("approving it", dash(cmd="companion_pair", id=pc).get("state") == "confirm")
    v = wait_for(lambda v: "ShowCode" in v["session"], 10, "the code")
    code = re.search(r'"(\d{6})"', v["session"]).group(1)
    r = dash(cmd="companion_pair", id=pc, code=code)
    check("the code shown on the computer pairs it", r.get("state") == "paired", str(r))
    wait_for(lambda v: v["session"] == "Online", 10, "online")
    check("the board lists it online", dash(cmd="companion_list")["paired"][0]["online"])
    # The key never left the app's folder unprotected.
    mode = oct(os.stat(os.path.join(store, "pairing-" + MAC.replace(":", "") + ".key")).st_mode & 0o777)
    # Unix permissions mean nothing on Windows: there the key is in the user's
    # own profile folder (%APPDATA%), which other accounts cannot read.
    if os.name != "nt":
        check("the pairing key is saved owner-only", mode == "0o600", mode)

    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_page(); pg.goto(f"http://{IP}:{web.server_address[1]}/")
        pg.evaluate("""t => { window.S = []; window.H = watchScreen(t, document.getElementById('v'), s => S.push(s)); }""",
                    dash(cmd="screen_ticket", id=pc))
        pg.wait_for_function("document.getElementById('v').readyState >= 2", timeout=15000)
        t_full = time.time()
        pg.wait_for_function("document.getElementById('v').videoWidth===1280", timeout=8000)   # half size first, then sharp
        dims = pg.evaluate("[document.getElementById('v').videoWidth, document.getElementById('v').videoHeight]")
        check("live video from the app, through a ticket the board signed", dims == [1280, 720], f"{dims}, full size after {time.time()-t_full:.1f}s")
        wait_for(lambda v: v["watching"] == 1, 5, "the tray to count the viewer")
        check("the tray counts one viewer", last["watching"] == 1)

        t0 = time.time(); say("unplug")
        pg.wait_for_function("S.some(s => s.startsWith('ended'))", timeout=10000)
        check("unplugging the board ends the viewing", True, f"{time.time() - t0:.1f}s, states {pg.evaluate('S')}")
        wait_for(lambda v: "NotPluggedIn" in v["board"], 5, "not plugged in")
        t2 = dash(cmd="screen_ticket", id=pc)
        if t2.get("reply") == "screen_ticket":
            pg.evaluate("""t => { window.S = []; watchScreen(t, document.getElementById('v'), s => S.push(s)); }""", t2)
            pg.wait_for_function("S.some(s => s.startsWith('refused') || s.startsWith('ended'))", timeout=10000)
            check("a new viewer is refused while it is unplugged", any(s.startswith("refused") for s in pg.evaluate("S")), str(pg.evaluate("S")))
        else:
            check("a new viewer is refused while it is unplugged", t2.get("reply") == "pc_offline", str(t2))

        say("plug")
        wait_for(lambda v: v["session"] == "Online" and "Found" in v["board"], 15, "back online")
        pg.evaluate("""t => { window.S = []; watchScreen(t, document.getElementById('v'), s => S.push(s)); }""", dash(cmd="screen_ticket", id=pc))
        try:
            pg.wait_for_function("S.includes('live')", timeout=15000)
            check("plugged back in, it streams again", True)
        except Exception:
            check("plugged back in, it streams again", False, str(pg.evaluate("S")))
        b.close()
    say("quit")
finally:
    app.kill(); board.kill(); web.shutdown()
sys.exit(0 if ok else 1)
