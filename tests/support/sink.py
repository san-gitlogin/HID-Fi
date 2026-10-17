"""The end-to-end test page: what the host OS actually delivered.

Served from the machine running the tests. Open it on the computer the board's
USB is plugged into (the same machine, or any other on the LAN with --sink remote)
and click Start once. From then on the page reports, on every event:

  text     the textarea's value (the OS's own text input: IMEs, Unicode entry)
  keys     keydown/keyup with code, key and modifiers (default actions blocked)
  mouse    raw pointer-lock movement, buttons and wheel
  focused  whether the box has focus - the harness types NOTHING unless it does

Timestamps are epoch milliseconds (performance.timeOrigin + event.timeStamp), on
the same clock as time.time() when the page runs on the test machine.
"""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = r"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>HID-Fi end-to-end test</title>
<style>
 body{font:16px system-ui,sans-serif;margin:20px;background:#111;color:#eee}
 #go{font-size:22px;padding:14px 26px;border-radius:10px;border:0;background:#0a7;color:#fff;cursor:pointer}
 textarea{width:100%;height:46vh;font:20px Consolas,Menlo,monospace;background:#000;color:#0f0;border:3px solid #555;margin-top:10px}
 textarea:focus{border-color:#0c0}#st{margin:10px 0;font-weight:600}
 #warn{color:#fb0}
</style>
<h2>HID-Fi end-to-end test</h2>
<p>Click <b>Start</b>, then leave this window in front and do not touch the keyboard or mouse.
The board types and moves here only; if this box loses focus, the test stops.</p>
<button id=go>Start</button> <span id=st>waiting for Start</span>
<p id=warn></p>
<textarea id=t spellcheck=false autocomplete=off autocorrect=off autocapitalize=off></textarea>
<script>
const t=document.getElementById('t'),st=document.getElementById('st'),go=document.getElementById('go');
let mode='text', started=false, seq=0, evs=[], clr=0, locked=false;
const now=e=>performance.timeOrigin+(e&&e.timeStamp!==undefined?e.timeStamp:performance.now());
const mods=e=>(e.shiftKey?'S':'')+(e.ctrlKey?'C':'')+(e.altKey?'A':'')+(e.metaKey?'M':'');
function rec(o){evs.push(o); if(evs.length>5000) evs.splice(0,evs.length-5000);}
function focused(){return started && document.hasFocus() && document.activeElement===t;}
function post(){
  const f=focused();
  st.textContent=f?('FOCUSED - mode '+mode+(locked?' - pointer locked':'')):'NOT focused - click Start';
  st.style.color=f?'#0c0':'#f80';
  const batch=evs; evs=[];
  fetch('/state',{method:'POST',body:JSON.stringify({value:t.value,focused:f,locked,seq:++seq,ev:batch,cleared:clr,
    ua:navigator.userAgent,platform:navigator.platform})})
   .then(r=>r.json()).then(d=>{ if(d.mode) mode=d.mode;
      if(d.clear!==clr){clr=d.clear; t.value=''; evs=[]; post(); }
      if(d.unlock && document.pointerLockElement) document.exitPointerLock(); })
   .catch(()=>{});
}
async function lock(){
  try{ await t.requestPointerLock({unadjustedMovement:true}); }
  catch(e){ try{ await t.requestPointerLock(); document.getElementById('warn').textContent='raw (unadjusted) pointer movement not supported here - mouse distances are approximate'; }catch(e2){} }
}
go.onclick=()=>{started=true; t.focus(); lock(); post();};
document.addEventListener('pointerlockchange',()=>{locked=!!document.pointerLockElement; post();});
t.addEventListener('input',e=>{rec({k:'input',ts:now(e),data:e.data,type:e.inputType}); post();});
for(const ty of ['keydown','keyup']) document.addEventListener(ty,e=>{
  rec({k:ty,ts:now(e),code:e.code,key:e.key,mods:mods(e),rep:e.repeat});
  if(mode==='keys'){ e.preventDefault(); e.stopPropagation(); }
  else if(e.key==='Tab'||e.key==='F5'||(e.key==='r'&&(e.metaKey||e.ctrlKey))) e.preventDefault();
  post();
},true);
document.addEventListener('mousemove',e=>{ if(locked) rec({k:'move',ts:now(e),dx:e.movementX,dy:e.movementY}); });
for(const ty of ['mousedown','mouseup']) document.addEventListener(ty,e=>{
  if(!started) return; rec({k:ty,ts:now(e),button:e.button,mods:mods(e)}); post(); },true);
document.addEventListener('contextmenu',e=>{e.preventDefault(); rec({k:'contextmenu',ts:now(e)});},true);
document.addEventListener('wheel',e=>{ if(!started) return; e.preventDefault();
  rec({k:'wheel',ts:now(e),dx:e.deltaX,dy:e.deltaY,mode:e.deltaMode}); post(); },{passive:false,capture:true});
window.addEventListener('focus',post); window.addEventListener('blur',post);
setInterval(post,150);
</script>"""


class Sink:
    def __init__(self, port=0, bind="127.0.0.1"):
        self.state = {"value": None, "focused": False, "locked": False, "seq": 0, "ua": "", "platform": "", "cleared": 0}
        self.events = []
        self.mode = "text"
        self._clear = 0
        self._unlock = False
        self.lock = threading.Lock()
        sink = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body, ctype):
                b = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(b)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                self._send(PAGE, "text/html; charset=utf-8")

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                d = json.loads(self.rfile.read(n).decode("utf-8"))
                with sink.lock:
                    sink.state.update({k: d.get(k) for k in ("value", "focused", "locked", "seq", "ua", "platform", "cleared")})
                    sink.events.extend(d.get("ev") or [])
                    reply = {"mode": sink.mode, "clear": sink._clear, "unlock": sink._unlock}
                self._send(json.dumps(reply), "application/json")

        self.httpd = ThreadingHTTPServer((bind, port), H)
        self.port = self.httpd.server_address[1]
        self.bind = bind
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    # ---- addresses -----------------------------------------------------------------
    def url(self, remote=False):
        host = "127.0.0.1"
        if remote:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("192.0.2.1", 9))      # no packet is sent; picks the LAN interface
                host = s.getsockname()[0]
            finally:
                s.close()
        return f"http://{host}:{self.port}/"

    def close(self):
        self.httpd.shutdown()

    # ---- the harness side -----------------------------------------------------------
    def wait_ready(self, timeout=180, need_lock=False):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                ok = self.state["focused"] and (self.state["locked"] or not need_lock)
            if ok:
                return True
            time.sleep(0.2)
        return False

    def assert_focused(self):
        with self.lock:
            f = self.state["focused"]
        if not f:
            raise SinkNotFocused("the test page lost focus - stopped before sending anything")

    def set_mode(self, mode):
        with self.lock:
            self.mode = mode
        self._wait_posts(2)

    def clear(self):
        """Empty the box, and wait until the PAGE confirms it applied this very
        clear. Waiting for an empty value is not enough: when the box was already
        empty that passes at once, and the page's real clear then lands in the
        middle of the next test's typing, wiping its first characters."""
        with self.lock:
            self._clear += 1
            target = self._clear
        end = time.time() + 5
        while time.time() < end:
            time.sleep(0.05)
            with self.lock:
                if (self.state.get("cleared") or 0) >= target and self.state["value"] == "":
                    self.events.clear()
                    return
        raise SinkNotFocused("the test page did not confirm the clear - is it still open?")

    def _wait_posts(self, n):
        with self.lock:
            s0 = self.state["seq"] or 0
        end = time.time() + 3
        while time.time() < end:
            time.sleep(0.05)
            with self.lock:
                if (self.state["seq"] or 0) >= s0 + n:
                    return

    def settle(self, quiet=0.45, timeout=30):
        """Wait until nothing new has arrived for `quiet` seconds; return (text, events)."""
        last, t_last, end = None, time.time(), time.time() + timeout
        while time.time() < end:
            time.sleep(0.05)
            with self.lock:
                snap = (self.state["value"], len(self.events))
            if snap != last:
                last, t_last = snap, time.time()
            elif time.time() - t_last >= quiet:
                break
        with self.lock:
            return self.state["value"], list(self.events)

    def info(self):
        with self.lock:
            return dict(self.state)


class SinkNotFocused(Exception):
    pass
