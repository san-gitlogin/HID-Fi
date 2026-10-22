"""Everything at once, as seen in the field on Windows (two monitors, several
viewers, 3-4 fps, a mouse that lagged): the Rust streamer (busy content, two
monitors, hardware H.264, WebRTC) and six Chrome viewers, while

  A1  watches monitor 1 through a link that swings 0.4 <-> 12 Mbit/s every 1.5 s
  B1  watches monitor 2 on a device that stalls (CPU 20x slower) 3 s of every 6
  B3  watches monitor 2 and leaves and comes back every 5 s
  A2, A3, B2  are healthy, and must stay smooth through all of it

then A1's link falls from 12 to 1.5 Mbit/s and stays there: how soon is it
smooth again? And on a viewer page, how late do events run (the mouse path)?

Judged per second from each browser's own WebRTC statistics (frames decoded,
keyframes, freezes, picture size), not from the streamer's log.

Run: cargo build --release -p hidfi-stream --example serve
     python3 companion/tests/overload.py            (SERVE=<binary> to test another build)
"""
import json, os, pathlib, socket, subprocess, sys, tempfile, threading, time, selectors, heapq
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("192.168.0.1", 9)); IP = s.getsockname()[0]; s.close()
ok = True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)

LOG = os.path.join(tempfile.mkdtemp(prefix="hidfi-ol-"), "stream.log")
SERVE = os.environ.get("SERVE") or str(ROOT / "target/release/examples" / ("serve.exe" if os.name == "nt" else "serve"))
srv = subprocess.Popen([SERVE, "1920", "1080", "30"] + ([] if os.environ.get("CONTENT") == "bar" else ["busy"]), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(LOG, "w"),
                       text=True, env={**os.environ, "HIDFI_DISPLAYS": "2", "RUST_LOG": os.environ.get("RUST_LOG", "info")})
info = json.loads(srv.stdout.readline())
# Anything more on stdout (a build with str0m's internal BWE trace) goes to a
# file, so the pipe never fills and stalls the streamer.
def _drain():
    with open(LOG + ".stats", "w") as f:
        for line in srv.stdout:
            f.write(f"{time.time():.3f} {line}")
threading.Thread(target=_drain, daemon=True).start()
tickets = iter(info["tickets"])
def ticket():
    return {"ip": IP, "port": info["port"], "fp": info["fp"], "tn": info["tn"], **next(tickets)}

html = ("<!doctype html><meta charset=utf-8><video id=v autoplay muted playsinline></video><script>" +
        "(()=>{const O=RTCPeerConnection;window.PC=null;window.RTCPeerConnection=function(c){const p=new O(c);PC=p;"
        "const s=p.setRemoteDescription.bind(p);p.setRemoteDescription=d=>s(window.RELAY?{type:d.type,"
        "sdp:d.sdp.replace(/( udp \\d+ [\\d.]+ )(\\d+)( typ host)/g,(m,a,q,b)=>a+(+q+1000)+b)}:d);return p};"
        "RTCPeerConnection.prototype=O.prototype})();" +
        (ROOT / "web" / "viewer.js").read_text() + """
window.go = (t, disp) => { window.S = []; const v = document.getElementById('v');
  window.H = watchScreen(t, v, s => { S.push(s); if (disp && s.startsWith('displays:')) H.display(disp); }); };
// How late a 10 ms timer fires: what a pointer event on this page waits.
window.LAG = []; (function tick(){ const t0 = performance.now(); setTimeout(() => { LAG.push(performance.now() - t0 - 10); tick(); }, 10); })();
window.ST = async () => { let r = null; if (window.PC) (await PC.getStats()).forEach(x => { if (x.type === 'inbound-rtp' && x.kind === 'video') r = x; });
  return r ? [r.framesDecoded||0, r.keyFramesDecoded||0, r.frameWidth||0, r.freezeCount||0, r.totalFreezesDuration||0] : null; };
</script>""")
class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        b = html.encode(); self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
web = ThreadingHTTPServer((IP, 0), Page); threading.Thread(target=web.serve_forever, daemon=True).start()

class Squeeze:
    """A UDP relay with a slow, buffered link towards the viewer (rate changeable
    while running); packets that would wait longer than buffer_s are dropped,
    as a WiFi access point's queue does."""
    def __init__(self, rate, buffer_s=0.5, stalls=False):
        self.rate, self.buffer_s = rate / 8, buffer_s
        # WiFi: the radio waits for the air ~10 times a second, 5-40 ms each
        # time, then sends what queued up in a burst - delay that comes and goes
        # without the link being full.
        self.stalls, self.next_stall = stalls, time.time()
        self.sel = selectors.DefaultSelector()
        for port in range(47820, 47880):
            r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                r.bind((IP, port + 1000))
            except OSError:
                continue
            r.setblocking(False)
            self.sel.register(r, selectors.EVENT_READ, {"port": port, "viewer": None})
        threading.Thread(target=self.run, daemon=True).start()
    def set(self, bps):
        self.rate = bps / 8
    def close(self):
        """Free its ports for the next relay (and stop relaying)."""
        self.closed = True
        for key in list(self.sel.get_map().values()):
            self.sel.unregister(key.fileobj); key.fileobj.close()
    def run(self):
        q, busy_until, n = [], time.time(), 0
        while not getattr(self, "closed", False):
            now = time.time()
            while q and q[0][0] <= now:
                _, _, r, data, to = heapq.heappop(q); r.sendto(data, to)
            timeout = max(0.0, q[0][0] - time.time()) if q else 0.5
            for key, _ in self.sel.select(timeout):
                r, st = key.fileobj, key.data
                try:
                    data, frm = r.recvfrom(65536)
                except OSError:
                    continue
                if frm == (IP, st["port"]):
                    if not st["viewer"]:
                        continue
                    now = time.time()
                    if self.stalls and now >= self.next_stall:
                        import random
                        if self.stalls == "mild":
                            # This home WiFi, measured (ping to the router, 20/s):
                            # 3 ms median, 7 ms p99, 17 ms worst. Short waits
                            # often, a longer one now and then.
                            stall = random.uniform(0.030, 0.050) if random.random() < 0.1 else random.uniform(0.002, 0.012)
                            busy_until = max(busy_until, now) + stall
                            self.next_stall = now + random.expovariate(5)
                        else:
                            busy_until = max(busy_until, now) + random.uniform(0.005, 0.040)
                            self.next_stall = now + random.expovariate(10)
                    done = max(busy_until, now) + len(data) / self.rate
                    if done - now <= self.buffer_s:
                        busy_until = done; n += 1
                        heapq.heappush(q, (done, n, r, data, st["viewer"]))
                else:
                    st["viewer"] = frm; r.sendto(data, (IP, st["port"]))

# All viewers here share this Mac's one hardware video decoder: a viewer
# leaving or joining was seen freezing every other page's decoding for ~3 s
# while the streamer kept sending 30 frames a second to each. Separate devices
# do not share a decoder, so the viewers decode in software (HW_DECODE=1 to
# see the effect).
BROWSER_ARGS = ["--autoplay-policy=no-user-gesture-required"] + ([] if os.environ.get("HW_DECODE") == "1" else ["--disable-accelerated-video-decode"])

def stats_ready(v, within=20):
    """Until the browser has decoded a frame (wait_for_function would take a
    Promise as true at once)."""
    end = time.time() + within
    while time.time() < end:
        s = v.evaluate("ST()")
        if s and s[0] > 0:
            return
        time.sleep(0.05)
    raise TimeoutError("no frame decoded")

def say(cmd):
    srv.stdin.write(cmd + "\n"); srv.stdin.flush()

def wifi(b):
    """Three viewers share a 25 Mbit/s WiFi radio that stalls now and then (no
    loss), while the screen rests 4 s and moves 3 s, as a desktop does. The
    estimate must not sink to a fraction of the link, and each burst of motion
    must play smoothly at full size."""
    global WIFI
    # NOWIFI=1: the same viewers straight to the computer, to tell the link from the rest.
    direct = os.environ.get("NOWIFI") == "1"
    # WIFI=mild: as measured here; default: a crowded channel (stress).
    WIFI = None if direct else Squeeze(25_000_000, buffer_s=0.5, stalls=os.environ.get("WIFI", "harsh"))
    vs = [b.new_page() for _ in range(int(os.environ.get("VIEWERS", 3)))]
    for v in vs:
        v.goto(f"http://{IP}:{web.server_address[1]}/")
        if not direct:
            v.evaluate("window.RELAY = 1")
        v.evaluate("([t, d]) => go(t, d)", [ticket(), 0])
    for v in vs:
        v.wait_for_function("S.includes('live')", timeout=20000)
        stats_ready(v)
    moving_fps, widths, k0 = [], [], [v.evaluate("ST()")[1] for v in vs]
    t0 = time.time()
    for cycle in range(6):
        say("still"); print(f"MARK {time.time()-t0:.1f} still", flush=True); time.sleep(4)
        say("move"); print(f"MARK {time.time()-t0:.1f} move", flush=True); time.sleep(0.5)          # the first half second: the burst starts
        a = [v.evaluate("ST()") for v in vs]; ta = time.time()
        time.sleep(2.5)
        z = [v.evaluate("ST()") for v in vs]; tz = time.time()
        moving_fps.append([(y[0] - x[0]) / (tz - ta) for x, y in zip(a, z)])
        widths.append([y[2] for y in z])
    secs = time.time() - t0
    keys = [v.evaluate("ST()")[1] - k for v, k in zip(vs, k0)]
    later = moving_fps[2:]                     # once the estimate has had time to settle
    flat = [f for c in later for f in c]
    check("on jittery WiFi, motion plays smoothly once settled", min(flat) >= 20,
          "fps per burst " + " | ".join(",".join(f"{f:.0f}" for f in c) for c in moving_fps))
    # Busy full-screen text needs ~10 Mbit/s per viewer at full size: three are
    # more than this link, and half size is the right answer. Light motion fits.
    if os.environ.get("CONTENT") == "bar":
        check("and at full size", all(w == 1920 for c in widths[2:] for w in c), str(widths))
    else:
        print("note sizes (busy content: full size does not fit three viewers):", widths)
    check("with few keyframes", all(k <= secs / 3 for k in keys), f"{keys} in {secs:.0f} s")
    for v in vs:
        v.evaluate("H.stop()")

def p95(xs):
    xs = sorted(xs); return xs[int(len(xs) * 0.95)] if xs else 0

try:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", args=BROWSER_ARGS)
        ONLY = os.environ.get("ONLY")   # "drop" or "wifi": that phase alone (to study it)
        if ONLY == "wifi":
            wifi(b); b.close(); raise SystemExit
        names = ["A1"] if ONLY == "drop" else ["A1", "A2", "A3", "B1", "B2", "B3"]
        # B3 leaves and comes back: a device of its own. Chrome decodes every
        # page of one browser in one process, and tearing a connection down
        # there was seen freezing every other page for ~3 s (the streamer kept
        # sending throughout) - a browser artifact, not what six devices see.
        b_own = p.chromium.launch(channel="chrome", args=BROWSER_ARGS)
        pg = {n: (b_own if n == "B3" else b).new_page() for n in names}
        for x in pg.values():
            x.goto(f"http://{IP}:{web.server_address[1]}/")
        sq = Squeeze(12_000_000)
        pg["A1"].evaluate("window.RELAY = 1")
        for n in names:
            pg[n].evaluate("([t, d]) => go(t, d)", [ticket(), 2 if n[0] == "B" else 0])
        for n in names:
            pg[n].wait_for_function("S.includes('live')", timeout=20000)
        time.sleep(4)
        if ONLY == "drop":
            names = []
        disp = {n: pg[n].evaluate("document.getElementById('v').videoWidth") for n in names}
        if ONLY != "drop": check("monitor 1 and monitor 2 each show on their own viewers",
              all(disp[n] == 1920 for n in ["A2", "A3"]) and all(disp[n] == 1280 for n in ["B1", "B2"]), str(disp))
        if ONLY != "drop":
            cdp = pg["B1"].context.new_cdp_session(pg["B1"])

            # Phase 1: everything at once, 30 s, sampled every second.
            hist = {n: [] for n in names}
            rejoin = []
            for x in pg.values():
                x.evaluate("LAG = []")
            t0 = time.time(); sec = 0
            while time.time() - t0 < 30:
                sec += 1
                sq.set(400_000 if (sec // 1.5) % 2 else 12_000_000) if False else sq.set(400_000 if int(sec / 1.5) % 2 else 12_000_000)
                cdp.send("Emulation.setCPUThrottlingRate", {"rate": 20 if (sec // 3) % 2 else 1})
                if sec % 5 == 0:
                    pg["B3"].evaluate("H.stop()")
                    time.sleep(0.3)
                    t_join = time.time()
                    pg["B3"].evaluate("([t, d]) => go(t, d)", [ticket(), 2])
                    try:
                        pg["B3"].wait_for_function("document.getElementById('v').videoWidth === 1280 && S.includes('live')", timeout=5000)
                        stats_ready(pg["B3"], 5)
                        rejoin.append(time.time() - t_join)
                    except Exception:
                        rejoin.append(99)
                for n in names:
                    hist[n].append((time.time(), pg[n].evaluate("ST()")))
                time.sleep(max(0, t0 + sec - time.time()))
            cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})

            def per_sec(n, a=0, z=None):
                h = [x for x in hist[n][a:z] if x[1]]
                fps = [(h[i + 1][1][0] - h[i][1][0]) / (h[i + 1][0] - h[i][0]) for i in range(len(h) - 1)]
                keys = h[-1][1][1] - h[0][1][1]
                sizes = sum(1 for i in range(len(h) - 1) if h[i + 1][1][2] != h[i][1][2])
                freeze = h[-1][1][4] - h[0][1][4]
                return fps, keys, sizes, freeze, h[-1][0] - h[0][0]
            for n in ["A2", "A3", "B2"]:
                fps, keys, _, freeze, secs = per_sec(n, 2)
                smooth = sum(f >= 15 for f in fps) / max(1, len(fps))
                check(f"{n} (healthy) stays smooth through it all",
                      sum(fps) / len(fps) >= 22 and smooth >= 0.9 and freeze < 1.0,
                      f"avg {sum(fps)/len(fps):.0f} fps, {smooth:.0%} of seconds at 15+, min {min(fps):.0f}, frozen {freeze:.1f} s"
                  + ("" if min(fps) >= 15 else " per second " + " ".join(f"{f:.0f}" for f in fps)))
                check(f"{n} gets keyframes as needed, not a storm", keys <= 1.3 * secs,
                      f"{keys} keyframes in {secs:.0f} s")
            for n in ["A1", "A2", "A3", "B2"]:
                print(f"{n} width per second:", " ".join(str(x[1][2]) for x in hist[n][2:] if x[1]), flush=True)
            fps, keys, sizes, _, secs = per_sec("A1", 2)
            check("A1 (swinging link) keeps a moving picture", sum(fps) / len(fps) >= 6, f"avg {sum(fps)/len(fps):.1f} fps, min {min(fps):.0f}")
            check("A1 does not flip quality on every swing", sizes <= secs / 3 + 1, f"{sizes} size changes in {secs:.0f} s (the link swung {int(secs/1.5)} times)")
            fps, keys, _, _, _ = per_sec("B1", -4)
            check("B1 (stalling device) recovers when it can keep up", sum(fps) / len(fps) >= 15, f"last seconds {[round(f) for f in fps]}")
            check("B3 (rejoining) gets its picture quickly every time", all(r < 2.0 for r in rejoin), " ".join(f"{r:.2f}s" for r in rejoin))
            lag = pg["A2"].evaluate("LAG")
            check("a viewer page still answers events promptly (the mouse path)", p95(lag) < 30,
                  f"timer late by p95 {p95(lag):.0f} ms, max {max(lag):.0f} ms over {len(lag)} ticks")

        b_own.close()
        if ONLY == "p1":
            raise SystemExit
        # Phase 2: A1's link falls from 12 to 1.5 Mbit/s and stays there.
        sq.set(12_000_000); time.sleep(8)
        before = pg["A1"].evaluate("document.getElementById('v').videoWidth")
        sq.set(1_500_000)
        t_drop = time.time(); last = pg["A1"].evaluate("ST()"); lt = time.time(); run = 0; recovered = None; trace = []
        while time.time() - t_drop < float(os.environ.get("DROP_SECS", 15)):
            time.sleep(0.5)
            cur = pg["A1"].evaluate("ST()"); ct = time.time()
            f = (cur[0] - last[0]) / (ct - lt); trace.append(round(f)); last, lt = cur, ct
            run = run + 1 if f >= 10 else 0
            if run >= 4 and recovered is None:
                recovered = ct - t_drop - 2.0
        after = pg["A1"].evaluate("document.getElementById('v').videoWidth")
        check("when a link really drops, the viewer is smooth again within 5 s",
              recovered is not None and recovered <= 5, f"smooth after {recovered if recovered is None else round(recovered, 1)} s; {before} -> {after} px; fps per 0.5 s {trace}")
        b.close()
        sq.close()
        if ONLY == "drop":
            raise SystemExit
        b = p.chromium.launch(channel="chrome", args=BROWSER_ARGS)
        wifi(b)
        b.close()
except SystemExit:
    pass
finally:
    srv.terminate(); web.shutdown()
    keys = [l for l in open(LOG) if "streaming:" in l]
    print("streamer, last line:", keys[-1].strip()[:400] if keys else "(none)")
sys.exit(0 if ok else 1)
