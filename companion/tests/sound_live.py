"""Sound, for real (docs/AUDIO.md): the real Companion app (with Screen Recording
allowed), this Mac's real sound, a real board serving its dashboard and the
tickets, real Google Chrome watching with sound.

  baseline -> a 1 kHz tone played on a non-default output (the virtual
  "Microsoft Teams Audio" device: nothing is heard) -> a quiet 600 Hz tone on the
  speakers -> each arrives on the dashboard at its own frequency; the delay from
  starting the tone to hearing it; the Companion's CPU while it streams.

Needs: the app running, the board on USB + WiFi and paired with this computer,
HIDFI_PIN in the environment.
Run: HIDFI_PIN=... python3 companion/tests/sound_live.py [board-url] [computer name]
"""
import os, subprocess, sys, time
from playwright.sync_api import sync_playwright

PIN = os.environ.get("HIDFI_PIN") or sys.exit("set HIDFI_PIN")
URL = sys.argv[1] if len(sys.argv) > 1 else "http://192.168.0.104"
NAME = sys.argv[2] if len(sys.argv) > 2 else None
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, detail, flush=True)


def device_index(name):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-f", "lavfi", "-i", "anullsrc", "-t", "0.01", "-f", "audiotoolbox",
                        "-list_devices", "true", "-"], capture_output=True, text=True)
    for ln in r.stderr.splitlines():
        if name in ln and "[" in ln:
            return int(ln.split("]")[1].split("[")[1])
    return None


def play(freq, secs, device, volume):
    return subprocess.Popen(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={secs}",
                             "-af", f"volume={volume}", "-f", "audiotoolbox", "-audio_device_index", str(device), "-"])


def companion_cpu():
    r = subprocess.run(["pgrep", "-f", "[H]ID-Fi Companion.app/Contents/MacOS"], capture_output=True, text=True).stdout.split()
    if not r:
        return None
    return float(subprocess.run(["ps", "-o", "%cpu=", "-p", r[0]], capture_output=True, text=True).stdout.strip() or 0)


LISTEN = """() => {
  window.__snd = [];
  const ctx = new AudioContext(); const an = ctx.createAnalyser(); an.fftSize = 8192;
  ctx.createMediaStreamSource(scrVideo.srcObject).connect(an);
  const f = new Float32Array(an.frequencyBinCount), t = new Float32Array(an.fftSize);
  setInterval(() => {
    an.getFloatFrequencyData(f); an.getFloatTimeDomainData(t);
    let bi = 0; for (let i = 1; i < f.length; i++) if (f[i] > f[bi]) bi = i;
    let s = 0; for (const x of t) s += x * x;
    __snd.push({at: Date.now(), hz: bi * ctx.sampleRate / an.fftSize, rms: Math.sqrt(s / t.length)});
  }, 20);
  return ctx.state;
}"""

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(viewport={"width": 1280, "height": 800})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL + "/#screen")
    pg.wait_for_selector("#sheet.on .pinbox", timeout=15000)
    pg.locator("#sheet.on .pinbox").first.click()
    for d in PIN:
        pg.keyboard.type(d); pg.wait_for_timeout(60)
    pg.wait_for_function("SCR.pcs && SCR.pcs.length > 0", timeout=20000)
    pcs = pg.evaluate("SCR.pcs.map(x => ({id: x.id, name: x.name, online: x.online}))")
    me = next((x for x in pcs if (NAME and x["name"] == NAME) or (not NAME and x["online"])), None)
    check("this computer is on the board's list, online", me, [(x["name"], x["online"]) for x in pcs])
    pg.evaluate("id => scrChoose(id)", me["id"])
    pg.wait_for_function("SCR.state === 'live' && scrVideo.videoWidth > 0", timeout=30000)
    pg.wait_for_function("SND.main.track && SND.main.comp", timeout=15000)
    comp = pg.evaluate("SND.main.comp")
    check("the Companion shares this computer's sound", comp.get("system") == "on", comp)
    check("the sound plays on the dashboard (unmuted after the PIN was typed)",
          pg.evaluate("!scrVideo.muted && document.querySelector('#scrSnd [data-snd=btn]').getAttribute('aria-pressed') === 'true'"))
    print("analyser:", pg.evaluate(LISTEN))
    time.sleep(2)
    base_end = time.time() * 1000
    virt, spk = device_index("Microsoft Teams Audio"), device_index("MacBook Pro Speakers")
    t1 = time.time() * 1000
    play(1000, 3, virt, 0.3).wait()
    time.sleep(0.5)
    t2 = time.time() * 1000
    cpu = companion_cpu()
    play(600, 1.5, spk, 0.03).wait()
    time.sleep(1.0)
    s = pg.evaluate("__snd")
    base = [x["rms"] for x in s if x["at"] < base_end]
    floor = max(base) if base else 0
    tone1 = [x for x in s if t1 <= x["at"] < t2 and abs(x["hz"] - 1000) < 15 and x["rms"] > max(0.02, 10 * floor)]
    tone2 = [x for x in s if x["at"] >= t2 and abs(x["hz"] - 600) < 15 and x["rms"] > max(0.002, 5 * floor)]
    check("a tone on a non-default output (virtual device) arrives at 1 kHz", len(tone1) > 40,
          f"{len(tone1)} of {len([x for x in s if t1 <= x['at'] < t2])} samples, level {max((x['rms'] for x in tone1), default=0):.3f} over a floor of {floor:.4f}")
    check("a quiet tone on the speakers arrives at 600 Hz", len(tone2) > 15, f"{len(tone2)} samples")
    if tone1:
        check("from starting the tone to hearing it on the dashboard", True,
              f"{tone1[0]['at'] - t1:.0f} ms (includes ffmpeg and CoreAudio starting the tone)")
    check("the Companion's CPU while it streams picture and sound", cpu is not None and cpu < 60, f"{cpu}%")
    check("no page errors", not errs, errs)
    b.close()
sys.exit(0 if ok else 1)
