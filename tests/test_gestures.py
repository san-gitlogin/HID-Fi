"""Trackpad gesture behaviour, driven through a real browser.

The pointer and the scroll share one gesture engine, and the failure that matters
is silent: a two-finger scroll that leaks into pointer movement sends the cursor
across the screen, which on a desktop can close a window. These drive synthetic
touches at realistic timings and assert what reaches the wire.

    python tests/test_gestures.py

Needs playwright:  python -m playwright install chromium
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
UI = HERE.parent / "hid_fi" / "web_ui.h"

# Recording harness. Everything the pad would put on the wire lands in __sent.
SETUP = r"""
() => {
  window.__sent = [];
  window.txMove = (dx, dy, w, p) => window.__sent.push({dx, dy, w, p});
  window.txBtn  = (b, a, c) => window.__sent.push({btn: b, act: a});
  window.send   = (o, cb) => { window.__sent.push(o); if (cb) cb({status: 'ok'}); };
  const pad = document.getElementById('pad');
  pad.setPointerCapture = () => {};
  pad.releasePointerCapture = () => {};
  window.__pad = pad;
  window.__r = pad.getBoundingClientRect();
  window.__ev = (type, id, x, y) => pad.dispatchEvent(new PointerEvent(type, {
    clientX: window.__r.left + x, clientY: window.__r.top + y,
    bubbles: true, pointerId: id, pointerType: 'touch', isPrimary: id === 1
  }));
  window.__reset = () => { window.__sent.length = 0; };
  window.__tally = () => {
    let moved = 0, scrolled = 0, panned = 0;
    const clicks = [];
    for (const s of window.__sent) {
      if ('dx' in s) { moved += Math.abs(s.dx) + Math.abs(s.dy);
                       scrolled += Math.abs(s.w); panned += Math.abs(s.p); }
      if ('btn' in s) clicks.push(s.btn + ':' + s.act);
      if (s && s.cmd === 'gesture') clicks.push('gesture:' + s.name);
    }
    return {moved, scrolled, panned, clicks};
  };
}
"""


def extract_html():
    src = UI.read_text(encoding="utf-8")
    s = src.index('R"rawliteral(')
    s = src.index("\n", s) + 1
    e = src.index(')rawliteral"', s)
    return src[s:e]


class Runner:
    def __init__(self, page):
        self.page = page
        self.ok = 0
        self.fail = 0

    def check(self, label, cond, detail=""):
        if cond:
            self.ok += 1
            print(f"  [OK ] {label}" + (f": {detail}" if detail else ""))
        else:
            self.fail += 1
            print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))

    def ev(self, type_, pid, x, y):
        self.page.evaluate("([t,i,x,y]) => window.__ev(t,i,x,y)", [type_, pid, x, y])

    def settle(self, ms=120):
        self.page.wait_for_timeout(ms)

    def reset(self):
        self.page.evaluate("() => window.__reset()")

    def tally(self):
        return self.page.evaluate("() => window.__tally()")

    def clear_contacts(self):
        """Leave no finger down and nothing still coasting.

        The product stops its own momentum the moment you touch again, but a test
        that starts while the previous flick is still gliding would blame the new
        gesture for the old one's movement.
        """
        self.page.evaluate("""() => {
            for (const id of [1,2,3]) window.__ev('pointercancel', id, 10, 10);
            if (typeof ASW !== 'undefined' && ASW.open) aswClose(false);
            releaseAllHeld();
            stopMomentum(); stopScrollMomentum();
            accX = 0; accY = 0; scrollPx = 0; panPx = 0;
        }""")
        self.settle(80)
        self.reset()


def main():
    html = extract_html()
    tmp = HERE / "_gestures.html"
    tmp.write_text(html, encoding="utf-8")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 430, "height": 930})
            page.goto(tmp.as_uri())
            page.wait_for_timeout(500)
            page.evaluate(SETUP)
            r = Runner(page)

            print("=" * 66)
            print("  Trackpad gestures")
            print("=" * 66)

            # ---- the reported bug: two fingers scroll, one lifts first --------
            print("\n-- two finger scroll, fingers lifting a moment apart --")
            r.reset()
            r.ev("pointerdown", 1, 120, 260)
            r.ev("pointerdown", 2, 160, 260)
            for y in range(250, 110, -20):          # both fingers slide up
                r.ev("pointermove", 1, 120, y)
                r.ev("pointermove", 2, 160, y)
                r.settle(16)
            r.ev("pointerup", 1, 120, 110)          # one finger leaves first
            for y in range(105, 45, -15):           # the other keeps travelling
                r.ev("pointermove", 2, 160, y)
                r.settle(16)
            r.ev("pointerup", 2, 160, 45)
            r.settle(400)                           # let any coasting run out
            t = r.tally()
            r.check("the cursor did not move at all", t["moved"] == 0,
                    f"{t['moved']}px of pointer travel")
            r.check("it scrolled instead", t["scrolled"] > 0, f"{t['scrolled']} notches")
            r.check("no stray clicks", not t["clicks"], str(t["clicks"]))

            # ---- the gate must not be permanent ------------------------------
            print("\n-- a fresh one finger drag straight afterwards --")
            r.clear_contacts()
            r.ev("pointerdown", 1, 100, 100)
            for x in range(110, 210, 20):
                r.ev("pointermove", 1, x, 100)
                r.settle(16)
            r.ev("pointerup", 1, 210, 100)
            r.settle(300)
            t = r.tally()
            r.check("pointing works again", t["moved"] > 0, f"{t['moved']}px")
            r.check("and it did not scroll", t["scrolled"] == 0, f"{t['scrolled']}")

            # ---- two finger flick should coast the scroll --------------------
            print("\n-- a flicked two finger scroll --")
            r.clear_contacts()
            r.ev("pointerdown", 1, 120, 280)
            r.ev("pointerdown", 2, 160, 280)
            for y in range(260, 60, -40):           # fast
                r.ev("pointermove", 1, 120, y)
                r.ev("pointermove", 2, 160, y)
                r.settle(16)
            r.ev("pointerup", 1, 120, 60)
            r.ev("pointerup", 2, 160, 60)
            during = r.tally()["scrolled"]
            r.settle(400)
            after = r.tally()
            r.check("the scroll carries on after lifting off", after["scrolled"] > during,
                    f"{during} -> {after['scrolled']} notches")
            r.check("without moving the cursor", after["moved"] == 0, f"{after['moved']}px")

            # ---- ordinary things must still work -----------------------------
            print("\n-- the everyday gestures --")
            r.clear_contacts()
            r.ev("pointerdown", 1, 100, 100)
            r.settle(40)
            r.ev("pointerup", 1, 101, 101)
            r.settle(150)
            r.check("tap is a left click", "1:2" in r.tally()["clicks"], str(r.tally()["clicks"]))

            r.clear_contacts()
            r.ev("pointerdown", 1, 100, 100)
            r.ev("pointerdown", 2, 140, 100)
            r.settle(40)
            r.ev("pointerup", 1, 100, 100)
            r.ev("pointerup", 2, 140, 100)
            r.settle(150)
            r.check("two finger tap is a right click", "2:2" in r.tally()["clicks"],
                    str(r.tally()["clicks"]))

            r.clear_contacts()
            r.ev("pointerdown", 1, 100, 150)
            r.settle(520)                            # past HOLD_MS
            t = r.tally()
            r.check("press and hold grabs", "1:1" in t["clicks"], str(t["clicks"]))
            r.ev("pointerup", 1, 100, 150)
            r.settle(120)

            r.clear_contacts()
            r.ev("pointerdown", 1, 80, 200)
            r.ev("pointerdown", 2, 120, 200)
            r.ev("pointerdown", 3, 160, 200)
            for y in range(180, 60, -20):
                for i in (1, 2, 3):
                    r.ev("pointermove", i, 80 + (i - 1) * 40, y)
                r.settle(16)
            for i in (1, 2, 3):
                r.ev("pointerup", i, 80 + (i - 1) * 40, 60)
            r.settle(200)
            t = r.tally()
            # Straight up, so it has to read as up - the fingers sit 80px apart
            # sideways and that spacing must not be mistaken for travel.
            r.check("three fingers straight up is task view",
                    "gesture:task_view" in t["clicks"], str(t["clicks"]))
            r.check("three fingers never moved the cursor", t["moved"] == 0, f"{t['moved']}px")

            # ---- three fingers sideways drives the app switcher live ----------
            print("\n-- three fingers sideways, the Windows app switch --")
            r.clear_contacts()
            page.evaluate("() => { S.f3app = true; }")
            opened = lambda: page.evaluate("() => ASW.open")
            step = lambda: page.evaluate("() => ASW.step")
            r.ev("pointerdown", 1, 60, 150)
            r.ev("pointerdown", 2, 100, 150)
            r.ev("pointerdown", 3, 140, 150)
            r.settle(30)
            r.check("nothing happens until you actually slide", not opened())
            for x in (110, 170, 230, 290):             # slide right, past each step
                for i in (1, 2, 3):
                    r.ev("pointermove", i, x + (i - 1) * 40, 150)
                r.settle(20)
            r.check("the switcher is up while the fingers are still down", opened())
            steps_live = step()
            r.check("it stepped as you slid", steps_live >= 2, f"on window {steps_live}")
            held = page.evaluate("() => [...held]")
            r.check("Alt is held meanwhile", "ALT" in held, str(held))
            for i in (1, 2, 3):                        # lift to commit
                r.ev("pointerup", i, 300, 150)
            r.settle(200)
            r.check("lifting commits and closes it", not opened())
            r.check("and nothing is left held", page.evaluate("() => [...held]") == [],
                    str(page.evaluate("() => [...held]")))
            r.check("the cursor never moved", r.tally()["moved"] == 0, f"{r.tally()['moved']}px")

            # ---- fingers leaving one at a time must not become a scroll -------
            print("\n-- three fingers, released one at a time --")
            r.clear_contacts()
            r.ev("pointerdown", 1, 80, 200)
            r.ev("pointerdown", 2, 120, 200)
            r.ev("pointerdown", 3, 160, 200)
            r.settle(30)
            r.ev("pointerup", 3, 160, 200)             # down to two fingers
            for y in (180, 160, 140):                  # and keep moving
                r.ev("pointermove", 1, 80, y)
                r.ev("pointermove", 2, 120, y)
                r.settle(16)
            r.ev("pointerup", 1, 80, 140)
            r.ev("pointerup", 2, 120, 140)
            r.settle(250)
            t = r.tally()
            r.check("it did not turn into a scroll", t["scrolled"] == 0, f"{t['scrolled']} notches")
            r.check("nor into pointer movement", t["moved"] == 0, f"{t['moved']}px")
            r.check("and nothing is left held", page.evaluate("() => [...held]") == [],
                    str(page.evaluate("() => [...held]")))

            # ---- the grab badge is state, not a touch artefact ---------------
            print("\n-- the mode label --")
            r.clear_contacts()
            label = lambda: page.evaluate("() => document.getElementById('padbadge').textContent")
            page.evaluate("() => setGrab(true)")
            r.check("turning grab on labels it straight away", label() == "grab", repr(label()))
            page.evaluate("() => setGrab(false)")
            r.check("turning it off clears the label without a touch", label() == "", repr(label()))
            r.ev("pointerdown", 1, 100, 100)
            r.settle(30)
            r.check("a finger down is reported", label() == "1 finger", repr(label()))
            r.ev("pointerup", 1, 100, 100)
            r.settle(200)
            r.check("and cleared on lift", label() == "", repr(label()))

            browser.close()
    finally:
        tmp.unlink(missing_ok=True)

    print()
    print("=" * 66)
    print(f"  {r.ok}/{r.ok + r.fail} passed")
    print("=" * 66)
    return 1 if r.fail else 0


if __name__ == "__main__":
    sys.exit(main())
