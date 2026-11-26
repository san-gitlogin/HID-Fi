"""Capture-to-present latency of the Companion's stream, as the dashboard's own viewer
code (web/viewer.js, the same watchScreen as web_ui.h) shows it in real Chrome. From
Wi-Live's tests/latency (its D-07): the test picture carries the wall-clock ms of each
frame in a two-row, self-checking strip; the page reads it back from every presented
frame and compares it with the frame's display time (never before its decode). 59 fps
against a 60 Hz display sweeps every frame-to-refresh phase, so p50 is the average and
p99 the worst. Frames the canvas had already replaced are set aside and counted.

  cargo build --release -p hidfi-stream --example serve && python3 companion/tests/latency_view.py [W H]
Prints one JSON line; exits 1 above the budget (p50 <= 35 ms, p99 <= 60 ms at 1080p).
"""
import json, os, pathlib, subprocess, sys, threading, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
W, H = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (1920, 1080)
P50, P99 = 35, 60
EXE = ROOT / "target/release/examples" / ("serve.exe" if os.name == "nt" else "serve")
srv = subprocess.Popen([str(EXE), str(W), str(H), "59"], stdin=subprocess.DEVNULL, cwd=ROOT, stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, text=True, env=dict(os.environ, HIDFI_CLOCK="1"))
info = json.loads(srv.stdout.readline())
PROBE = """
window.lat = {samples: [], parts: [], ahead: 0, bad: 0};
function probe(v, srcW) {
  const BITS = 32, B = 16, cv = document.createElement('canvas'); cv.width = (BITS + 2) * B; cv.height = 2 * B;
  const cx = cv.getContext('2d', {willReadFrequently: true});
  const on = (now, meta) => {
    try {
      const sx = meta.width / srcW;
      cx.drawImage(v, 0, 0, cv.width * sx, 2 * B * sx, 0, 0, cv.width, 2 * B);
      const px = cx.getImageData(0, 0, cv.width, 2 * B).data;
      const bit = (r, c) => px[((r * B + (B >> 1)) * cv.width + c * B + (B >> 1)) * 4] > 128;
      const word = r => { if (!bit(r, 0) || bit(r, BITS + 1)) return null; let x = 0; for (let c = 1; c <= BITS; c++) x = x * 2 + (bit(r, c) ? 1 : 0); return x; };
      const w0 = word(0), w1 = word(1);
      if (w0 !== null && w1 !== null && w0 + w1 === 4294967295) {
        const wn = Date.now(), pn = performance.now();
        const decoded = meta.receiveTime ? meta.receiveTime + (meta.processingDuration || 0) * 1000 : 0;
        let d = (wn + Math.max(meta.expectedDisplayTime, decoded) - pn) % 4294967296 - w0;
        if (d < -2147483648) d += 4294967296;
        if (d < 0 && d > -50) lat.ahead++;
        else if (d >= 0 && d < 10000) {
          lat.samples.push(d);
          if (meta.receiveTime) { let rx = (wn + meta.receiveTime - pn) % 4294967296 - w0; lat.parts.push([rx, Math.max(0, meta.expectedDisplayTime - decoded)]); }
        } else lat.bad++;
      } else lat.bad++;
    } catch (e) { lat.bad++; }
    v.requestVideoFrameCallback(on);
  };
  v.requestVideoFrameCallback(on);
}
"""
VIEWER = (ROOT / "web" / "viewer.js").read_text()
if os.environ.get("LATENCY_VIDEO_JBT") == "1":   # experiment: the video's jitter buffer target 0 too (as Wi-Live)
    VIEWER = VIEWER.replace("if(e.track.kind==='audio'){\n          try{ if('jitterBufferTarget' in e.receiver) e.receiver.jitterBufferTarget=0;",
                            "try{ if('jitterBufferTarget' in e.receiver) e.receiver.jitterBufferTarget=0; }catch(x){}\n        if(e.track.kind==='audio'){\n          try{ if('jitterBufferTarget' in e.receiver) e.receiver.jitterBufferTarget=0;")
    assert "jitterBufferTarget=0; }catch(x){}" in VIEWER, "the experiment's patch did not apply"
# Laid out as the dashboard lays out the picture (web_ui.h .scrstage video): absolutely
# placed, 100%, object-fit contain. The compositor treats a raw-sized video differently.
STYLE = "<style>html,body{margin:0;height:100%;background:#000}.st{position:fixed;inset:0}.st video{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:contain;transform-origin:0 0}</style>"
page_html = "<!doctype html><meta charset=utf-8>" + STYLE + "<div class=st><video id=v autoplay muted playsinline></video></div><script>" + VIEWER + PROBE + "</script>"

class H_(SimpleHTTPRequestHandler):
    def do_GET(self):
        b = page_html.encode(); self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
web = ThreadingHTTPServer(("127.0.0.1", 0), H_); threading.Thread(target=web.serve_forever, daemon=True).start()
try:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_page(viewport={"width": 1280, "height": 800})
        pg.goto(f"http://127.0.0.1:{web.server_address[1]}/")
        t = {"ip": "127.0.0.1", "port": info["port"], "fp": info["fp"], "tn": info["tn"], **info["tickets"][0]}
        r = pg.evaluate("""async ([t, w]) => {
            const v = document.getElementById('v'); const h = watchScreen(t, v, () => {});
            const t0 = performance.now();
            await new Promise(r => { const k = () => { if (v.videoWidth === w || performance.now() - t0 > 20000) r(); else setTimeout(k, 50); }; k(); });
            await new Promise(r => setTimeout(r, 3000));
            probe(v, w);
            await new Promise(r => setTimeout(r, 12000));
            const s = lat.samples.slice().sort((a, b) => a - b), q = x => s[Math.min(s.length - 1, Math.floor(x * s.length))];
            const med = i => { const a = lat.parts.map(x => x[i]).sort((a, b) => a - b); return a[a.length >> 1]; };
            const out = {n: s.length, ahead: lat.ahead, bad: lat.bad, w: v.videoWidth, p50: q(.5), p95: q(.95), p99: q(.99), to_receive: med(0), to_display: med(1)};
            h.stop(); return out; }""", [t, W])
        b.close()
finally:
    srv.terminate(); web.shutdown()
r = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()}
r["size"] = f"{W}x{H}@59"
print(json.dumps(r))
sys.exit(0 if r["n"] >= 300 and r["p50"] <= P50 and r["p99"] <= P99 and r["ahead"] <= r["n"] * 0.02 else 1)
