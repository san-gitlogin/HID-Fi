"""Sound, end to end, no board (docs/AUDIO.md): the Rust streamer with its test
sound (a 440 Hz tone with a 2 kHz click at every whole second of the shared
clock) watched by real Google Chrome through companion/web/viewer.js, asking for
sound. An AudioWorklet in the page times each click to the sample.

Checks: the sound track arrives with the Companion's state; one click a second,
none missing; latency from the shared clock to the page (network, jitter buffer,
decode - not the OS capture, measured on its own by --check-sound); the
browser's own jitter-buffer delay; picture and sound in one stream (lip sync);
the picture's frame rate unchanged by sound; the streamer's CPU with sound.

Run: cargo build --release -p hidfi-stream --example serve && python3 companion/tests/sound_e2e.py
     MIC=1 also shares the test microphone (44.1 kHz mono, mixed in).
(needs Google Chrome)
"""
import json, os, pathlib, statistics, subprocess, sys, threading, socket
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
SECS = int(os.environ.get("SECS", 30))


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 9)); ip = s.getsockname()[0]
    finally:
        s.close()
    import ipaddress
    return ip if ipaddress.ip_address(ip).is_private and not ip.startswith("192.0.0.") else "127.0.0.1"


IP = lan_ip()
env = dict(os.environ, HIDFI_SOUND="test")
if os.environ.get("MIC"):
    env["HIDFI_SOUND_MIC"] = "1"
srv = subprocess.Popen([str(ROOT / "target/release/examples" / ("serve.exe" if os.name == "nt" else "serve")), "1280", "720", "30"],
                       stdin=subprocess.DEVNULL, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
info = json.loads(srv.stdout.readline())

WORKLET = """
class Clicks extends AudioWorkletProcessor {
  constructor(){ super(); this.quiet = 0; this.n = 0; this.max = 0; this.blocks = 0; }
  process(inputs){
    const ch = inputs[0] && inputs[0][0];
    if (ch) { this.blocks++; for (let i = 0; i < ch.length; i++) this.max = Math.max(this.max, Math.abs(ch[i])); }
    if (this.blocks % 375 === 1) this.port.postMessage({max: this.max, blocks: this.blocks});
    if (ch) for (let i = 0; i < ch.length; i++) {
      const a = Math.abs(ch[i]);
      if (a > 0.3 && this.quiet > sampleRate * 0.3) { this.port.postMessage(currentTime + i / sampleRate); this.quiet = 0; }
      else if (a < 0.3) this.quiet++;
      else this.quiet = 0;
    }
    return true;
  }
}
registerProcessor('clicks', Clicks);
"""
page_html = ("<!doctype html><meta charset=utf-8><video id=v autoplay muted playsinline></video><script>"
             "window.__pcs=[];const O=RTCPeerConnection;window.RTCPeerConnection=function(c){const p=new O(c);__pcs.push(p);return p;};"
             + (ROOT / "web" / "viewer.js").read_text() + "</script>")


class H_(SimpleHTTPRequestHandler):
    def do_GET(self):
        b = (WORKLET if self.path.endswith(".js") else page_html).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/javascript" if self.path.endswith(".js") else "text/html")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, *a):
        pass


# The page from localhost: a secure context, which AudioWorklet needs (the
# dashboard itself plays through the <video>, which does not). WebRTC still
# connects to the LAN address.
web = ThreadingHTTPServer(("127.0.0.1", 0), H_)
threading.Thread(target=web.serve_forever, daemon=True).start()
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, detail, flush=True)


def cpu_of(pid):
    return float(subprocess.run(["ps", "-o", "%cpu=", "-p", str(pid)], capture_output=True, text=True).stdout.strip() or 0)


RUN = """async ([t, secs]) => {
  const v = document.getElementById('v'); const states = []; const clicks = []; const diag = []; let frames = 0;
  let ctx = null;
  const listen = async () => {
    ctx = new AudioContext({latencyHint: 'interactive'});
    await ctx.audioWorklet.addModule('/clicks.js');
    const node = new AudioWorkletNode(ctx, 'clicks');
    node.port.onmessage = e => {
      if (typeof e.data === 'object') { diag.push(e.data); return; }
      const ts = ctx.getOutputTimestamp();
      const perf = ts.performanceTime + (e.data - ts.contextTime) * 1000;
      clicks.push(performance.timeOrigin + perf);
    };
    ctx.createMediaStreamSource(v.srcObject).connect(node);
  };
  let err = null;
  const h = watchScreen(t, v, s => { states.push(s); if (s === 'audiotrack' && !ctx) setTimeout(() => listen().catch(e => { err = String(e); }), 50); }, {audio: true});
  const t0 = performance.now();
  await new Promise(r => { const tick = () => { if (v.readyState >= 2 || performance.now() - t0 > 15000) r(); else setTimeout(tick, 50); }; tick(); });
  const cb = () => { frames++; v.requestVideoFrameCallback(cb); };
  v.requestVideoFrameCallback(cb);
  await new Promise(r => setTimeout(r, secs * 1000));
  const pc = window.__pcs[window.__pcs.length - 1];
  const stats = {};
  if (pc) (await pc.getStats()).forEach(s => { if (s.type === 'inbound-rtp') stats[s.kind] = s; });
  const sdp = pc && pc.remoteDescription ? pc.remoteDescription.sdp : '';
  const tracks = v.srcObject ? v.srcObject.getTracks().map(t => t.kind) : [];
  h.stop(); await new Promise(r => setTimeout(r, 300));
  return {states, clicks, frames, stats, sdp, tracks, diag, ctxState: ctx && ctx.state, err,
          out: ctx ? {base: ctx.baseLatency * 1000, output: (ctx.outputLatency || 0) * 1000} : null};
}"""

try:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
        pg = b.new_page()
        pg.goto(f"http://127.0.0.1:{web.server_address[1]}/")
        t = {"ip": IP, "port": info["port"], "fp": info["fp"], "tn": info["tn"], **info["tickets"][0]}
        cpu = []
        stop = threading.Event()

        def sample():
            while not stop.wait(2):
                cpu.append(cpu_of(srv.pid))
        threading.Thread(target=sample, daemon=True).start()
        r = pg.evaluate(RUN, [t, SECS])
        stop.set()
        aud = [s for s in r["states"] if s.startswith("audio:")]
        check("the computer's sound state arrives", aud and json.loads(aud[0][6:])["system"] == "on", aud[:1])
        check("a sound track arrives, in the picture's stream", "audiotrack" in r["states"] and sorted(r["tracks"]) == ["audio", "video"], r["tracks"])
        ms = [ln.split()[0][len("a=msid:"):] for ln in r["sdp"].splitlines() if ln.startswith("a=msid:")]
        check("picture and sound share one stream id (the browser syncs them)", len(ms) == 2 and ms[0] == ms[1], ms)
        epoch = info["epoch_ms"]
        lat = []
        for w in r["clicks"]:
            k = round((w - epoch) / 1000)
            lat.append(w - (epoch + k * 1000))
        lat = lat[2:]   # the first seconds settle the jitter buffer
        n_expect = SECS - 4
        check(f"one click a second, none missing (>= {n_expect} of ~{SECS})", len(lat) >= n_expect, len(lat))
        if lat:
            med, p95 = statistics.median(lat), sorted(lat)[int(len(lat) * 0.95) - 1]
            outl = (r["out"] or {}).get("base", 0) + (r["out"] or {}).get("output", 0)
            print(f"heard: median {med:.1f} ms after the click left the Companion's clock, of which {outl:.1f} ms is this computer's own audio output")
            check("latency: Companion to page (network, jitter buffer, decode) under 60 ms", med - outl < 60,
                  f"median {med - outl:.1f} ms, p95 {p95 - outl:.1f}, spread {max(lat)-min(lat):.1f}")
        a = r["stats"].get("audio", {})
        jb = 1000 * a.get("jitterBufferDelay", 0) / max(1, a.get("jitterBufferEmittedCount", 1))
        n_ = max(1, a.get("jitterBufferEmittedCount", 1))
        print("audio arrival jitter %.1f ms; jitter buffer target %.1f ms, minimum %.1f ms" % (
            1000 * a.get("jitter", 0), 1000 * a.get("jitterBufferTargetDelay", 0) / n_, 1000 * a.get("jitterBufferMinimumDelay", 0) / n_))
        check("the browser's jitter buffer stays small", jb < 50, f"{jb:.1f} ms, lost {a.get('packetsLost')}, concealed {a.get('concealedSamples')} of {a.get('totalSamplesReceived')}")
        fps = r["frames"] / SECS
        check("the picture keeps its frame rate with sound on", fps > 20, f"{fps:.1f} fps")
        check("the streamer's CPU with picture and sound", cpu and max(cpu) < 60, f"max {max(cpu) if cpu else 0:.0f}% of a core")
        b.close()
finally:
    srv.terminate()
    web.shutdown()
sys.exit(0 if ok else 1)
