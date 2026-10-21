"""Several screens watching one computer at once, for real: the Rust streamer
(test pattern, hardware H.264, WebRTC) and N Google Chrome viewers in
parallel, each with its own one-use ticket.

Checks: 4 viewers all get the picture at a usable rate; a 5th is turned away
("busy") and gets in once one leaves; the computer's CPU barely moves with
more viewers (one encoder feeds them all).

Run: cargo build --release -p hidfi-stream --example serve
     python3 companion/tests/multi_view.py
"""
import json, pathlib, socket, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("192.168.0.1", 9)); IP = s.getsockname()[0]; s.close()
ok = True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)

srv = subprocess.Popen([str(ROOT / "target/release/examples/serve"), "1920", "1080", "30"],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
info = json.loads(srv.stdout.readline())
html = ("<!doctype html><meta charset=utf-8><video id=v autoplay muted playsinline></video><script>" +
        (ROOT / "web" / "viewer.js").read_text() + """
window.go = (t) => { window.S = []; window.F = 0; const v = document.getElementById('v');
  window.H = watchScreen(t, v, s => S.push(s));
  const cb = () => { F++; v.requestVideoFrameCallback(cb); }; v.requestVideoFrameCallback(cb); };
</script>""")
class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        b = html.encode(); self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
web = ThreadingHTTPServer((IP, 0), Page); threading.Thread(target=web.serve_forever, daemon=True).start()
cpu = lambda: float(subprocess.run(["ps", "-o", "%cpu=", "-p", str(srv.pid)], capture_output=True, text=True).stdout.strip() or 0)
tickets = iter(info["tickets"])
def ticket():
    return {"ip": IP, "port": info["port"], "fp": info["fp"], "tn": info["tn"], **next(tickets)}

def cpu_avg(n=4):
    xs = []
    for _ in range(n):
        time.sleep(0.6); xs.append(cpu())
    return sum(xs) / len(xs)

try:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        pages = [b.new_page() for _ in range(5)]
        for pg in pages:
            pg.goto(f"http://{IP}:{web.server_address[1]}/")
        pages[0].evaluate("t => go(t)", ticket())
        pages[0].wait_for_function("S.includes('live') && F > 10", timeout=15000)
        one = cpu_avg()
        for pg in pages[1:4]:
            pg.evaluate("t => go(t)", ticket())
        for pg in pages[:4]:
            pg.wait_for_function("S.includes('live')", timeout=15000)
        for pg in pages[:4]:
            pg.evaluate("F = 0")
        four = cpu_avg(6)
        fps = [pg.evaluate("F") / 3.6 for pg in pages[:4]]
        check("four screens watch at once", all(f >= 15 for f in fps), "fps " + ", ".join(f"{f:.0f}" for f in fps))
        dims = [pg.evaluate("[v.videoWidth, v.videoHeight]") for pg in pages[:4]]
        check("each gets the full picture", all(d == [1920, 1080] for d in dims), str(dims[0]))
        check("more viewers cost the computer little CPU", four < one + 25, f"1 viewer {one:.0f}%, 4 viewers {four:.0f}% of one core")
        pages[4].evaluate("t => go(t)", ticket())
        pages[4].wait_for_function("S.some(s => s.startsWith('refused'))", timeout=10000)
        check("a fifth is turned away as busy", "refused:busy" in pages[4].evaluate("S"), str(pages[4].evaluate("S")))
        pages[0].evaluate("H.stop()")
        time.sleep(1.5)
        pages[4].evaluate("t => go(t)", ticket())
        pages[4].wait_for_function("S.includes('live')", timeout=15000)
        check("once one leaves, the fifth gets in", True)
        alive = [pg.evaluate("(()=>{const a=F; return a})()") for pg in pages[1:4]]
        time.sleep(1.5)
        moved = [pg.evaluate("F") > a for pg, a in zip(pages[1:4], alive)]
        check("the others kept playing throughout", all(moved), str(moved))
        b.close()
finally:
    srv.terminate(); web.shutdown()
sys.exit(0 if ok else 1)
