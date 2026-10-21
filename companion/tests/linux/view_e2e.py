"""A WebRTC viewer in Python (aiortc), for the Linux run: Chrome is not made for
Linux on ARM and Playwright's Chromium cannot decode H.264, so this decodes it.

Checks, on a two-monitor virtual X desktop (left red 1920x1080, right blue
1280x1200): both displays are listed; the picture is the left one, red, and
moving; switching shows the right one, blue, at its own shape.
"""
import asyncio, json, sys, time
import numpy as np
import websockets
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.rtcrtpsender import RTCRtpSender

info = json.loads(open(sys.argv[1]).readline())
ok = True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)

frames = []          # (t, w, h, mean rgb)

async def main():
    t = info["tickets"][0]
    async with websockets.connect(f"ws://127.0.0.1:{info['port']}/") as ws:
        await ws.send(json.dumps({"t": "hello", "tn": info["tn"], "vn": t["vn"], "ticket": t["ticket"]}))
        ready = json.loads(await ws.recv())
        check("the app lists the displays", len(ready.get("displays", [])) >= 2,
              [(d["name"], d["width"], d["height"], d["main"]) for d in ready.get("displays", [])])
        displays = ready["displays"]
        pc = RTCPeerConnection()
        tr = pc.addTransceiver("video", direction="recvonly")
        caps = RTCRtpSender.getCapabilities("video")
        tr.setCodecPreferences([c for c in caps.codecs if c.mimeType.lower() == "video/h264"])

        @pc.on("track")
        def on_track(track):
            async def pull():
                while True:
                    f = await track.recv()
                    a = f.to_ndarray(format="rgb24")
                    frames.append((time.time(), f.width, f.height, a.reshape(-1, 3).mean(0)))
            asyncio.ensure_future(pull())

        await pc.setLocalDescription(await pc.createOffer())
        await ws.send(json.dumps({"t": "offer", "sdp": pc.localDescription.sdp}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("t") == "answer":
                break
        await pc.setRemoteDescription(RTCSessionDescription(sdp=m["sdp"], type="answer"))
        t0 = time.time()
        while not frames and time.time() - t0 < 15:
            await asyncio.sleep(0.1)
        check("the first frame arrives", frames, f"after {time.time()-t0:.1f} s")
        await asyncio.sleep(5)
        recent = [f for f in frames if f[0] > time.time() - 3]
        fps = len(recent) / 3
        w, h, rgb = frames[-1][1], frames[-1][2], frames[-1][3]
        check("the left display, red, moving", rgb[0] > 150 and rgb[2] < 60 and fps >= 8,
              f"{w}x{h}, mean rgb {rgb.astype(int).tolist()}, {fps:.0f} fps")
        check("its shape is 16:9", abs(w / h - 1920 / 1080) < 0.02, f"{w}x{h}")
        right = [d for d in displays if not d["main"]] or displays[1:]
        await ws.send(json.dumps({"t": "display", "id": right[0]["id"]}))
        t1 = time.time()
        while time.time() - t1 < 10:
            if frames and frames[-1][0] > t1 and frames[-1][3][2] > 150:
                break
            await asyncio.sleep(0.1)
        w, h, rgb = frames[-1][1], frames[-1][2], frames[-1][3]
        check("switching shows the other display, blue, at its own shape",
              rgb[2] > 150 and rgb[0] < 60 and abs(w / h - 1280 / 1200) < 0.03,
              f"{w}x{h}, mean rgb {rgb.astype(int).tolist()}, after {time.time()-t1:.1f} s")
        await pc.close()

asyncio.run(asyncio.wait_for(main(), 60))
sys.exit(0 if ok else 1)
