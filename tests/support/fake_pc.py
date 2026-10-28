"""A computer running HID-Fi Companion, for the dashboard's Screen scenarios.

The real app (companion/) sends H.264, which Playwright's Chromium cannot decode,
so this stands in with a second page in the same browser: an animated canvas
sent over a real RTCPeerConnection (VP8, which the dashboard's viewer accepts
after H.264). Its signaling socket is played by `route_web_socket`, speaking the
app's protocol (companion/crates/hidfi-stream/src/server.rs):

  hello -> ready | error ;  offer -> answer ;  stats every second ;  end
  display -> display (a computer with several displays; `displays=n`)
  audio    (docs/AUDIO.md) with ready and on `set_sound`; a test tone in the
           picture's stream when the viewer asks for sound (`sound=None`: an app
           from before sound - no audio state, the audio section declined)

`behaviour` decides what it does with a viewer:
  "live"          accept: ready, answer, stats
  "refuse:<why>"  answer the hello with {"t":"error","reason":why}
  "silent"        accept the socket, say nothing (the viewer times out)
  "end:<why>"     accept, then end the session at once with that reason

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
window.answerOffer=async(offer,ip,sound)=>{
  const pc=new RTCPeerConnection({certificates:[window.CERT]});
  window.PC=pc;
  const track=c.captureStream(15).getVideoTracks()[0];
  await pc.setRemoteDescription({type:'offer',sdp:offer});
  const trs=pc.getTransceivers();
  const tr=trs.find(x=>x.receiver.track.kind==='video')||trs[0];
  const stream=new MediaStream([track]);
  await tr.sender.replaceTrack(track);
  tr.sender.setStreams(stream);
  tr.direction='sendonly';
  // Sound, in the picture's stream as the app sends it: a quiet tone.
  const at=trs.find(x=>x.receiver.track.kind==='audio');
  if(at){
    if(sound){
      const ac=new AudioContext(),o=ac.createOscillator(),g=ac.createGain(),d=ac.createMediaStreamDestination();
      g.gain.value=0.05;o.connect(g).connect(d);o.start();window.TONE=ac;
      const a=d.stream.getAudioTracks()[0];stream.addTrack(a);
      await at.sender.replaceTrack(a);at.sender.setStreams(stream);at.direction='sendonly';
    }else at.direction='inactive';
  }
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
    def __init__(self, browser, app, behaviour="live", displays=1, size=(640, 360), sound="on", mic="off"):
        """`app` is the companion_sim.FakeApp whose ip/port/fp the board hands out.
        `displays`: how many monitors it has (main first, as the app lists them).
        `size`: the picture it sends (a portrait monitor is taller than wide)."""
        self.ctx = browser.new_context()
        self.page = self.ctx.new_page()
        self.page.set_content(PAGE.replace("640", str(size[0])).replace("360", str(size[1])))
        self.page.wait_for_function("window.CERT !== null")
        fp = self.page.evaluate("CERT.getFingerprints().find(f => f.algorithm === 'sha-256').value")
        self.fp = fp.replace(":", "").lower()
        self.app = app
        app.fp = self.fp
        self.behaviour = behaviour
        self.ip = lan_ip()
        self.sockets = []
        self.hellos = []
        sizes = [(3024, 1964), (2560, 1440), (1080, 1920), (1920, 1080)]
        self.displays = [{"id": i + 1, "name": "Built-in display" if i == 0 else f"Display {i + 1}",
                          "width": sizes[i][0], "height": sizes[i][1], "main": i == 0} for i in range(displays)]
        self.asked = []          # display ids viewers asked for, in order
        # The app's sound switches (None: an app from before sound).
        self.sound = None if sound is None else {"system": sound, "mic": mic, "why": {"system": None, "mic": None}}
        self.offers = []         # each viewer's offer SDP
        self.showing = {}        # socket -> display id it is shown

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
                elif self.behaviour.startswith("end:"):
                    # Accepts, then cannot capture (e.g. macOS Screen Recording not allowed).
                    ws.send(json.dumps({"t": "ready", "displays": self.displays, "display": self.displays[0]["id"]}))
                    ws.send(json.dumps({"t": "end", "reason": self.behaviour.split(":", 1)[1]}))
                elif self.behaviour == "live":
                    self.showing[id(ws)] = self.displays[0]["id"]
                    ready = {"t": "ready", "displays": self.displays, "display": self.displays[0]["id"]}
                    if self.sound is not None:
                        ready["audio"] = self.sound
                    ws.send(json.dumps(ready))
            elif m.get("t") == "offer" and self.behaviour == "live":
                self.offers.append(m["sdp"])
                sdp = self.page.evaluate("([o, ip, s]) => answerOffer(o, ip, s)", [m["sdp"], self.ip, self.sound is not None])
                ws.send(json.dumps({"t": "answer", "sdp": sdp}))
                ws.send(json.dumps({"t": "stats", "fps": 15}))
            elif m.get("t") == "display":
                self.asked.append(m.get("id"))
                if any(d["id"] == m.get("id") for d in self.displays):
                    self.showing[id(ws)] = m["id"]
                    ws.send(json.dumps({"t": "display", "id": m["id"], "fallback": False}))
            elif m.get("t") == "bye":
                ws.close()

        ws.on_message(on_message)

    def set_sound(self, system=None, mic=None, why=None):
        """The person changes the app's sound switches (or a device goes): every
        viewer is told at once, as server.rs does."""
        if self.sound is None:
            return
        if system is not None:
            self.sound["system"] = system
        if mic is not None:
            self.sound["mic"] = mic
        if why is not None:
            self.sound["why"] = why
        for ws in self.sockets:
            try:
                ws.send(json.dumps({"t": "audio", **self.sound}))
            except Exception:
                pass

    def unplug_display(self, display_id):
        """A monitor is pulled out: its viewers move to the main display, told why."""
        self.displays = [d for d in self.displays if d["id"] != display_id or d["main"]]
        main = self.displays[0]["id"]
        # As server.rs does: the new list says which display each viewer now
        # watches; no separate "display" message for a list change.
        for ws in self.sockets:
            if self.showing.get(id(ws)) == display_id:
                self.showing[id(ws)] = main
            try:
                ws.send(json.dumps({"t": "displays", "displays": self.displays,
                                    "display": self.showing.get(id(ws), main)}))
            except Exception:
                pass

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
