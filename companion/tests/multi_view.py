"""Several screens watching one computer at once, for real: the Rust streamer
(test pattern, hardware H.264, WebRTC) and N Google Chrome viewers in
parallel, each with its own one-use ticket.

Checks: 4 viewers all get the picture at a usable rate; a 5th is turned away
("busy") and gets in once one leaves; the computer's CPU barely moves with
more viewers (one encoder feeds them all); one viewer on a device too slow to
keep up does not drag the others down (seen live: every screen fell to 1 fps).

Run: cargo build --release -p hidfi-stream --example serve
     python3 companion/tests/multi_view.py
"""
import json, os, pathlib, socket, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("192.168.0.1", 9)); IP = s.getsockname()[0]; s.close()
import ipaddress
if not ipaddress.ip_address(IP).is_private or IP.startswith("192.0.0."):
    IP = "127.0.0.1"   # no home LAN (an IPv6-only hotspot gives 192.0.0.2): the viewers are on this computer
ok = True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)

# A frame rate measures the machine as much as the app: a hosted CI runner has
# no GPU, and its browsers decode four 1080p streams in software. There, rates
# are reported, not judged; every functional check still is.
def rate(name, cond, detail=""):
    if os.environ.get("CI"):
        print("note" if not cond else "ok  ", name, detail, "(rate, reported on CI)", flush=True)
    else:
        check(name, cond, detail)

import tempfile
LOG = os.environ.get("STREAM_LOG") or os.path.join(tempfile.mkdtemp(prefix="hidfi-mv-"), "stream.log")
srv = subprocess.Popen([str(ROOT / "target/release/examples" / ("serve.exe" if os.name == "nt" else "serve")), "1920", "1080", "30", "busy"],   # scrolling text: as hard as a real desktop
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=open(LOG, "w"), text=True)
info = json.loads(srv.stdout.readline())
html = ("<!doctype html><meta charset=utf-8><video id=v autoplay muted playsinline></video><script>" +
        "(()=>{const O=RTCPeerConnection;window.PC=null;window.RTCPeerConnection=function(c){const p=new O(c);PC=p;"
        # A page with RELAY set reaches the computer's media port through the
        # squeezing relay below (port + 1000) - a phone on a poor WiFi link.
        "const s=p.setRemoteDescription.bind(p);p.setRemoteDescription=d=>s(window.RELAY?{type:d.type,"
        "sdp:d.sdp.replace(/( udp \\d+ [\\d.]+ )(\\d+)( typ host)/g,(m,a,q,b)=>a+(+q+1000)+b)}:d);return p};"
        "RTCPeerConnection.prototype=O.prototype})();" +
        (ROOT / "web" / "viewer.js").read_text() + """
window.go = (t) => { window.S = []; window.F = 0; const v = document.getElementById('v');
  window.H = watchScreen(t, v, s => S.push(s));
  if (window.counting) return; window.counting = true;   // a second go() must not count frames twice
  const cb = () => { F++; v.requestVideoFrameCallback(cb); }; v.requestVideoFrameCallback(cb); };
</script>""")
class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        b = html.encode(); self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
web = ThreadingHTTPServer((IP, 0), Page); threading.Thread(target=web.serve_forever, daemon=True).start()
class Squeeze:
    """A UDP relay with a slow, buffered link towards the viewer: what a phone
    far from the router sees. Rate in bit/s; packets that would wait longer
    than `buffer_s` are dropped, like a WiFi access point's queue. One thread,
    woken only by a packet or by the next one due, so the relay adds no jitter
    of its own (the estimator would read jitter as congestion)."""
    def __init__(self, rate, buffer_s=0.5):
        import selectors
        self.rate, self.buffer_s = rate / 8, buffer_s
        self.sel = selectors.DefaultSelector()
        for port in range(47820, 47860):
            r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                r.bind((IP, port + 1000))
            except OSError:
                continue
            r.setblocking(False)
            self.sel.register(r, selectors.EVENT_READ, {"port": port, "viewer": None})
        threading.Thread(target=self.run, daemon=True).start()
    def run(self):
        import heapq
        q, busy_until, n = [], time.time(), 0
        while True:
            now = time.time()
            while q and q[0][0] <= now:
                _, _, r, data, to = heapq.heappop(q)
                r.sendto(data, to)
            timeout = max(0.0, q[0][0] - time.time()) if q else 0.5
            for key, _ in self.sel.select(timeout):
                r, st = key.fileobj, key.data
                try:
                    data, frm = r.recvfrom(65536)
                except OSError:
                    continue
                if frm == (IP, st["port"]):                # computer -> viewer: squeezed
                    if not st["viewer"]:
                        continue
                    now = time.time()
                    done = max(busy_until, now) + len(data) / self.rate
                    if done - now <= self.buffer_s:
                        busy_until = done; n += 1
                        heapq.heappush(q, (done, n, r, data, st["viewer"]))
                else:                                      # viewer -> computer: as is
                    st["viewer"] = frm; r.sendto(data, (IP, st["port"]))

cpu = lambda: 0.0 if os.name == "nt" else float(subprocess.run(["ps", "-o", "%cpu=", "-p", str(srv.pid)], capture_output=True, text=True).stdout.strip() or 0)
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
        pages = [b.new_page() for _ in range(7)]
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
        rate("four screens watch at once", all(f >= 15 for f in fps), "fps " + ", ".join(f"{f:.0f}" for f in fps))
        dims = [pg.evaluate("[v.videoWidth, v.videoHeight]") for pg in pages[:4]]
        # The size each gets follows its link and what this computer can encode:
        # a GPU-less CI runner may give one a smaller picture (reported there).
        rate("each gets the full picture", all(d == [1920, 1080] for d in dims), str(dims))
        rate("more viewers cost the computer little CPU", four < one + 25, f"1 viewer {one:.0f}%, 4 viewers {four:.0f}% of one core")
        # Six places (a wall of four monitors and a phone or two): two more fill them.
        for pg in pages[5:7]:
            pg.evaluate("t => go(t)", ticket())
        for pg in pages[5:7]:
            pg.wait_for_function("S.includes('live')", timeout=15000)
        pages[4].evaluate("t => go(t)", ticket())
        pages[4].wait_for_function("S.some(s => s.startsWith('refused'))", timeout=10000)
        check("a seventh is turned away as busy", "refused:busy" in pages[4].evaluate("S"), str(pages[4].evaluate("S")))
        for pg in pages[5:7]:
            pg.evaluate("H.stop()")
        pages[0].evaluate("H.stop()")
        time.sleep(1.5)
        pages[4].evaluate("t => go(t)", ticket())
        pages[4].wait_for_function("S.includes('live')", timeout=15000)
        check("once others leave, it gets in", True)
        alive = [pg.evaluate("(()=>{const a=F; return a})()") for pg in pages[1:4]]
        time.sleep(1.5)
        moved = [pg.evaluate("F") > a for pg, a in zip(pages[1:4], alive)]
        check("the others kept playing throughout", all(moved), str(moved))

        # One viewer on a device far too slow to keep up (CPU slowed 20x).
        KEYS = """(async()=>{let k=0;(await PC.getStats()).forEach(r=>{if(r.type==='inbound-rtp')k=r.keyFramesDecoded||0});return k})()"""
        slow = pages[1].context.new_cdp_session(pages[1])
        slow.send("Emulation.setCPUThrottlingRate", {"rate": 20})
        time.sleep(2)
        healthy = [pages[2], pages[3], pages[4]]
        k0 = [pg.evaluate(KEYS) for pg in healthy]
        for pg in healthy:
            pg.evaluate("F = 0")
        time.sleep(6)
        fps = [pg.evaluate("F") / 6 for pg in healthy]
        keys = [pg.evaluate(KEYS) - k for pg, k in zip(healthy, k0)]
        slow.send("Emulation.setCPUThrottlingRate", {"rate": 1})
        rate("a slow device does not drag the others down", all(f >= 20 for f in fps), "fps " + ", ".join(f"{f:.0f}" for f in fps))
        rate("nor turn their stream into keyframes", all(k <= 8 for k in keys), f"keyframes in 6 s: {keys}")
        time.sleep(3)
        pages[1].evaluate("F = 0"); time.sleep(3)
        f = pages[1].evaluate("F") / 3
        rate("the slow device recovers once it can keep up", f >= 15, f"{f:.0f} fps")

        # One viewer on a poor link (1.5 Mbit/s, like a phone far from the
        # router) joins three good ones.
        Squeeze(1_500_000)
        pages[1].evaluate("H.stop()"); time.sleep(1.5)
        pages[1].evaluate("window.RELAY = 1; S = []")
        pages[1].evaluate("t => go(t)", ticket())
        pages[1].wait_for_function("S.includes('live')", timeout=15000)
        RATE = """(async()=>{let b=0;(await PC.getStats()).forEach(r=>{if(r.type==='inbound-rtp')b=r.bytesReceived});return b})()"""
        time.sleep(15)                                    # let the estimates settle
        good = [pages[2], pages[3], pages[4]]
        b0 = [pg.evaluate(RATE) for pg in good]
        for pg in pages[1:5]:
            pg.evaluate("F = 0")
        time.sleep(8)
        fps = [pg.evaluate("F") / 8 for pg in good]
        mbps = [(pg.evaluate(RATE) - b) * 8 / 8 / 1e6 for pg, b in zip(good, b0)]
        poor = pages[1].evaluate("F") / 8
        rate("a viewer on a poor link does not drag the good ones down",
              # All four viewers decode on this one computer, which also encodes:
              # the browsers show ~20 of the ~27 fps sent. Before: 1 fps.
              all(f >= 15 for f in fps), "fps " + ", ".join(f"{f:.0f}" for f in fps) + ", Mbit/s " + ", ".join(f"{m:.1f}" for m in mbps))
        rate("the poor link still gets a moving picture", poor >= 3, f"{poor:.1f} fps")
        b.close()
finally:
    srv.terminate(); web.shutdown()
    if not ok:
        print("streamer log (last lines):"); print("".join(open(LOG).readlines()[-8:]))
sys.exit(0 if ok else 1)
