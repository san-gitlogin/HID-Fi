"""A computer running HID-Fi Companion, for the dashboard's Screen scenarios.

The real app (companion/) sends H.264, which Playwright's Chromium cannot decode,
so this stands in with a second page in the same browser: an animated canvas
sent over a real RTCPeerConnection (VP8, which the dashboard's viewer accepts
after H.264). Its signaling socket is played by `route_web_socket`, speaking the
app's protocol (companion/crates/hidfi-stream/src/server.rs):

  hello -> ready | error ;  offer -> answer ;  stats every second ;  end

`behaviour` decides what it does with a viewer:
  "live"          accept: ready, answer, stats
  "refuse:<why>"  answer the hello with {"t":"error","reason":why}
  "silent"        accept the socket, say nothing (the viewer times out)

The certificate is the page's own, so the board model is told its real
fingerprint and the dashboard's man-in-the-middle check runs for real.
"""
import json
import socket

PAGE = """<!doctype html><canvas id=c width=640 height=360></canvas><script>
const c=document.getElementById('c'),g=c.getContext('2d');let t=0;
setInterval(()=>{g.fillStyle='#0d1119';g.fillRect(0,0,640,360);g.fillStyle='#00d2ff';
  g.fillRect((t*9)%640,0,40,360);g.fillStyle='#fff';g.font='40px sans-serif';g.fillText('HID-Fi '+t,30,80);t++;},66);
window.CERT=null;
RTCPeerConnection.generateCertificate({name:'ECDSA',namedCurve:'P-256'}).then(x=>{window.CERT=x;});
window.answerOffer=async(offer,ip)=>{
  const pc=new RTCPeerConnection({certificates:[window.CERT]});
  window.PC=pc;
  const track=c.captureStream(15).getVideoTracks()[0];
  await pc.setRemoteDescription({type:'offer',sdp:offer});
  const tr=pc.getTransceivers()[0];
  await tr.sender.replaceTrack(track);
  tr.direction='sendonly';
  await pc.setLocalDescription(await pc.createAnswer());
  await new Promise(r=>{if(pc.iceGatheringState==='complete')return r();
    pc.onicegatheringstatechange=()=>{if(pc.iceGatheringState==='complete')r();};setTimeout(r,3000);});
  // Chromium hides host addresses behind random .local names; the viewer is in
  // the same browser and may not be able to resolve them, so name the address.
  return pc.localDescription.sdp.replace(/[0-9a-f-]{36}\\.local/g, ip);
};
</script>"""


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 9))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class FakePc:
    def __init__(self, browser, app, behaviour="live"):
        """`app` is the companion_sim.FakeApp whose ip/port/fp the board hands out."""
        self.ctx = browser.new_context()
        self.page = self.ctx.new_page()
        self.page.set_content(PAGE)
        self.page.wait_for_function("window.CERT !== null")
        fp = self.page.evaluate("CERT.getFingerprints().find(f => f.algorithm === 'sha-256').value")
        self.fp = fp.replace(":", "").lower()
        self.app = app
        app.fp = self.fp
        self.behaviour = behaviour
        self.ip = lan_ip()
        self.sockets = []
        self.hellos = []

    def serve(self, viewer_page):
        url = f"ws://{self.app.ip}:{self.app.port}/"
        viewer_page.route_web_socket(url, self._socket)

    def _socket(self, ws):
        self.sockets.append(ws)

        def on_message(msg):
            try:
                m = json.loads(msg)
            except (TypeError, ValueError):
                return
            if m.get("t") == "hello":
                self.hellos.append(m)
                if self.behaviour.startswith("refuse:"):
                    ws.send(json.dumps({"t": "error", "reason": self.behaviour.split(":", 1)[1]}))
                    ws.close()
                elif self.behaviour == "live":
                    ws.send(json.dumps({"t": "ready", "w": 640, "h": 360}))
            elif m.get("t") == "offer" and self.behaviour == "live":
                sdp = self.page.evaluate("([o, ip]) => answerOffer(o, ip)", [m["sdp"], self.ip])
                ws.send(json.dumps({"t": "answer", "sdp": sdp}))
                ws.send(json.dumps({"t": "stats", "fps": 15}))
            elif m.get("t") == "bye":
                ws.close()

        ws.on_message(on_message)

    def end(self, reason="Disconnected"):
        """The app ends every session (e.g. the board was unplugged from it)."""
        for ws in self.sockets:
            try:
                ws.send(json.dumps({"t": "end", "reason": reason}))
                ws.close()
            except Exception:
                pass
        try:
            self.page.evaluate("window.PC && window.PC.close()")
        except Exception:
            pass

    def close(self):
        self.ctx.close()
