"""End to end, no board: the Rust streamer (test pattern, hardware H.264,
WebRTC) watched by real Google Chrome through companion/web/viewer.js.

Checks that frames are decoded at the size sent, that a tampered fingerprint
is refused, that a ticket works once, and that a wrong ticket is refused.
Run: cargo build --release -p hidfi-stream --example serve && python3 companion/tests/e2e_view.py
(needs Google Chrome; macOS for now)
"""
import json, os, pathlib, subprocess, sys, threading, time, socket
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
W, H, FPS = int(os.environ.get("W", 1280)), int(os.environ.get("H", 720)), int(os.environ.get("FPS", 30))

def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 9)); return s.getsockname()[0]
    finally:
        s.close()

IP = lan_ip()
srv = subprocess.Popen([str(ROOT / "target/release/examples/serve"), str(W), str(H), str(FPS)]
                       + (["still"] if os.environ.get("STILL") else []),
                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
info = json.loads(srv.stdout.readline())
page_html = "<!doctype html><meta charset=utf-8><video id=v autoplay muted playsinline></video><script>" + \
            (ROOT / "web" / "viewer.js").read_text() + "</script>"

class H_(SimpleHTTPRequestHandler):
    def do_GET(self):
        b = page_html.encode(); self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
web = ThreadingHTTPServer((IP, 0), H_); threading.Thread(target=web.serve_forever, daemon=True).start()

results, ok = {}, True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail)

def cpu_of(pid):
    return float(subprocess.run(["ps", "-o", "%cpu=", "-p", str(pid)], capture_output=True, text=True).stdout.strip() or 0)

try:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_page()
        pg.goto(f"http://{IP}:{web.server_address[1]}/")
        idle_cpu = cpu_of(srv.pid)
        def ticket(i, **over):
            t = {"ip": IP, "port": info["port"], "fp": info["fp"], "tn": info["tn"], **info["tickets"][i]}
            t.update(over); return t
        run = """async ([t, secs]) => {
            const v = document.getElementById('v'); const states = []; let frames = 0;
            const h = watchScreen(t, v, s => states.push(s));
            const t0 = performance.now();
            await new Promise(r => { const tick = () => { if (v.readyState >= 2 || performance.now() - t0 > 15000) r(); else setTimeout(tick, 50); }; tick(); });
            const firstFrameMs = Math.round(performance.now() - t0);
            const cb = () => { frames++; v.requestVideoFrameCallback(cb); };
            if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(cb);
            await new Promise(r => setTimeout(r, secs * 1000));
            let stats = {};
            try { const pc = null; } catch (e) {}
            const out = { states, frames, w: v.videoWidth, h: v.videoHeight, firstFrameMs };
            h.stop(); await new Promise(r => setTimeout(r, 300));
            return out; }"""
        samples = []
        def sample():
            time.sleep(2.5)
            for _ in range(4):
                samples.append(cpu_of(srv.pid)); time.sleep(0.5)
        th = threading.Thread(target=sample); th.start()
        r = pg.evaluate(run, [ticket(0), 5])
        th.join()
        print(f"     cpu while streaming {W}x{H}@{FPS} (one core = 100%): " + ", ".join(f"{x:.1f}%" for x in samples))
        check("decodes the stream at the size sent", r["w"] == (W & ~1) and r["h"] == (H & ~1), f'{r["w"]}x{r["h"]}')
        fps = r["frames"] / 5
        check("frames keep coming", fps >= FPS * 0.6 if not os.environ.get("STILL") else r["frames"] >= 1, f"{fps:.1f} fps, first frame after {r['firstFrameMs']} ms")
        check("reached live", "live" in r["states"], str(r["states"][:6]))
        r2 = pg.evaluate(run, [ticket(0), 1])
        check("a used ticket is refused", any(s.startswith("refused:ticket") for s in r2["states"]), str(r2["states"]))
        r3 = pg.evaluate(run, [ticket(1, ticket="00" * 32), 1])
        check("a forged ticket is refused", any(s.startswith("refused:ticket") for s in r3["states"]), str(r3["states"]))
        r4 = pg.evaluate(run, [ticket(2, fp="ab" * 32), 2])
        check("a fingerprint the board did not vouch for is refused", any(s == "refused:fingerprint" for s in r4["states"]) and r4["frames"] == 0, str(r4["states"]))
        time.sleep(1.5)
        after = cpu_of(srv.pid)
        print(f"     cpu: idle before={idle_cpu:.1f}% after viewers left={after:.1f}%")
        b.close()
finally:
    srv.terminate(); web.shutdown()
sys.exit(0 if ok else 1)
