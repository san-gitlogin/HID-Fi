"""Driving the real dashboard in Chromium against the simulated board.

    ui = Dashboard(browser, sim, device="phone")
    ui.open()
    ui.tab("Settings")
    ui.tap("Set PIN")
    ...

Two recorders run in every page, so "nothing missed" is measured, not claimed:
  - element coverage: every control a scenario actually touched (id, or the
    data-* family a generated control belongs to)
  - function coverage: which of the dashboard's JavaScript functions ran, from
    Chromium's own precise coverage
"""
import json
import re
import time

from support import dashboard
from support.board_sim import AP_URL, URL

IPHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
ANDROID_UA = ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0 Mobile Safari/537.36")
DEVICES = {
    "phone": {"width": 390, "height": 844, "touch": True},
    "iPhone": {"width": 390, "height": 844, "touch": True, "ua": IPHONE_UA},
    "Android phone": {"width": 412, "height": 915, "touch": True, "ua": ANDROID_UA},
    "small phone": {"width": 320, "height": 568, "touch": True},
    "tablet": {"width": 820, "height": 1180, "touch": True},
    "desktop": {"width": 1400, "height": 900, "touch": False},
    "wide desktop": {"width": 1920, "height": 1080, "touch": False},
}
TABS = {"Trackpad": "pad", "Keyboard": "keys", "Keys": "short", "Media": "media",
        "Gamepad": "game", "Session": "power", "Settings": "setup"}

# Which element a touch belongs to: its id, else the data-* attribute that names
# its family (generated controls such as keys, shortcuts, knobs, list rows).
DESCRIBE_JS = r"""
window.__hidfiDescribe = function(t){
  const el = t && t.closest && t.closest('button,input,textarea,select,a,label,[data-k],[data-knob],[data-sc],[data-q],[data-ms],[data-gi],[data-ssid],[data-hat],[data-gb],.stick,#pad,.pinbox,.dial,[id]');
  if (!el) return null;
  for (const a of el.getAttributeNames()) if (a.startsWith('data-') && a !== 'data-guarded' && a !== 'data-nofill') return '[' + a + ']';
  if (el.id && !/^(b\d+|kp\d+|kt\d+)$/.test(el.id)) return '#' + el.id;
  if (el.classList.contains('pinbox')) return '.pinbox';
  return null;
};
"""
RECORD_JS = DESCRIBE_JS + r"""
(() => {
  // Kept in sessionStorage so a reload inside a scenario does not forget what
  // was touched before it.
  let prior = [];
  try { prior = JSON.parse(sessionStorage.getItem('__hidfiTouched') || '[]'); } catch (x) {}
  window.__touched = new Set(prior);
  const rec = e => { try { const d = window.__hidfiDescribe(e.target);
    if (d && !window.__touched.has(d)) { window.__touched.add(d);
      sessionStorage.setItem('__hidfiTouched', JSON.stringify([...window.__touched])); } } catch (x) {} };
  for (const ev of ['pointerdown', 'click', 'input', 'change', 'keydown', 'focus'])
    document.addEventListener(ev, rec, true);
})();
"""
# Everything a person can operate: what the coverage contract compares against.
INVENTORY_JS = DESCRIBE_JS + r"""
(() => {
  const out = new Set();
  const sel = 'button,input,textarea,select,[data-k],[data-knob],[data-sc],[data-hat],[data-gb],.stick,#pad,.pinbox';
  document.querySelectorAll(sel).forEach(el => { const d = window.__hidfiDescribe(el); if (d) out.add(d); });
  return [...out];
})()
"""


_SCRIPT_LEN = None


def _script_len():
    global _SCRIPT_LEN
    if _SCRIPT_LEN is None:
        from support import ui_contract
        _SCRIPT_LEN = len(ui_contract._script_and_offset()[0])
    return _SCRIPT_LEN


class Dashboard:
    def __init__(self, browser, sim, device="phone", html=None, answers=None, init=()):
        self.browser, self.sim, self.device = browser, sim, device
        d = DEVICES[device]
        extra = {"user_agent": d["ua"]} if d.get("ua") else {}
        self.ctx = browser.new_context(viewport={"width": d["width"], "height": d["height"]},
                                       has_touch=d["touch"], is_mobile=d["touch"] and d["width"] < 900, **extra)
        self.page = self.ctx.new_page()
        self.errors, self.dialogs = [], []
        self.answers = list(answers or [])          # queued answers for confirm()/prompt()
        self.default_answer = True
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("dialog", self._dialog)
        self.page.add_init_script(RECORD_JS)
        for js in init:                  # what this browser is, before the page runs
            self.page.add_init_script(js)
        self.html = html or dashboard.extract_html()
        sim.attach(self.page, self.html)
        self.cdp = self.ctx.new_cdp_session(self.page)
        self.cdp.send("Profiler.enable")
        self.cdp.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
        self.blocks = {}                 # script length -> bytearray, 1 where code ran
        self._ran, self._seen = set(), set()

    # A navigation throws the old document's coverage away, so every way a
    # scenario leaves the page takes it first. (It cannot be done from a route or
    # request handler: Chromium does not answer the profiler while the navigation
    # waits on the handler.) The page's one self-reload, #apnewReload, goes
    # through tap()/tap_id() and is caught there.
    RELOADS = ("#apnewReload",)

    def keep_coverage(self):
        try:
            self.functions_run()
        except Exception:
            pass

    def reload(self):
        self.keep_coverage()
        self.page.reload()

    def go_back(self):
        self.keep_coverage()
        self.page.go_back()

    # ---- browser dialogs ----------------------------------------------------------------
    def _dialog(self, d):
        self.dialogs.append({"type": d.type, "message": d.message})
        ans = self.answers.pop(0) if self.answers else self.default_answer
        if d.type == "prompt":
            return d.accept(ans) if isinstance(ans, str) else (d.accept() if ans else d.dismiss())
        return d.accept() if ans else d.dismiss()

    # ---- lifecycle ----------------------------------------------------------------------
    def open(self, hash_=""):
        self.page.goto(self.sim.URL + (("#" + hash_) if hash_ else ""))
        self.page.wait_for_function("() => document.getElementById('ltext').textContent === 'live'", timeout=8000)
        self.idle()
        return self

    def idle(self, ms=250):
        self.page.wait_for_timeout(ms)

    def touched(self):
        return set(self.page.evaluate("[...(window.__touched||[])]"))

    def inventory(self):
        return set(self.page.evaluate(INVENTORY_JS))

    def functions_run(self):
        """Named functions that ran / exist, from Chromium's precise coverage. The
        same call fills self.blocks: per byte of the page's script, whether any
        block containing it ran - this sees anonymous handlers, which have no name."""
        res = self.cdp.send("Profiler.takePreciseCoverage")
        ran, seen = set(), set()
        for script in res.get("result", []):
            if not script.get("url", "").startswith((URL, AP_URL)):
                continue
            ranges = [r for fn in script.get("functions", []) for r in fn["ranges"]]
            if ranges:
                n = _script_len()      # the page has one script; its offsets index into it
                cov = bytearray(n)
                # Outer ranges first, so a nested block's count overrides its parent's.
                for r in sorted(ranges, key=lambda r: r["startOffset"] - r["endOffset"]):
                    a, b = min(r["startOffset"], n), min(r["endOffset"], n)
                    cov[a:b] = (b"\x01" if r["count"] else b"\x00") * (b - a)
                prev = self.blocks.get(n)
                self.blocks[n] = cov if prev is None else bytearray(x | y for x, y in zip(prev, cov))
            for fn in script.get("functions", []):
                name = fn.get("functionName")
                if not name:
                    continue
                seen.add(name)
                if fn["ranges"] and fn["ranges"][0]["count"] > 0:
                    ran.add(name)
        self._ran |= ran
        self._seen |= seen
        return set(self._ran), set(self._seen)

    def close(self):
        try:
            self.ctx.close()
        except Exception:
            pass

    # ---- finding things -----------------------------------------------------------------
    def view(self):
        return self.page.evaluate("curView")

    def tab(self, name):
        vid = TABS[name]
        self.page.locator(f'[data-v="{vid}"]:visible').first.click()
        self.page.wait_for_function(f"() => curView === '{vid}'")
        self.idle(150)

    def scope(self):
        """Where a label is looked up: the open dialog, an overlay, or the current tab."""
        if self.page.locator("#sheet.on").count():
            return self.page.locator("#sheetin")
        for ov in ("#apnew.on", "#boot.on", "#asw.on"):
            if self.page.locator(ov).count():
                return self.page.locator(ov)
        if self.page.locator("#padwrap.fs").count():
            return self.page.locator("#padwrap")
        return self.page.locator(f"#v-{self.view()}")

    def control(self, label):
        s = self.scope()
        exact = s.locator(f"button:visible, .tile:visible, .chip:visible").filter(
            has_text=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I))
        if exact.count():
            return exact.first
        loose = s.locator("button:visible").filter(has_text=label)
        if loose.count():
            return loose.first
        aria = s.locator(f'[aria-label="{label}"]:visible')
        if aria.count():
            return aria.first
        raise AssertionError(f"no visible control labelled {label!r} on {self.view()!r} "
                             f"(sheet open: {bool(self.page.locator('#sheet.on').count())})")

    def tap(self, label):
        c = self.control(label)
        if c.evaluate("e => e.id") in [r[1:] for r in self.RELOADS]:
            self.keep_coverage()
        c.click()
        self.idle()

    def tap_id(self, el_id):
        if f"#{el_id}" in self.RELOADS:
            self.keep_coverage()
        self.page.locator(f"#{el_id}").click()
        self.idle()

    def field(self, label):
        s = self.scope()
        lab = s.locator("label").filter(has_text=re.compile(rf"^\s*{re.escape(label)}", re.I))
        if lab.count():
            inp = lab.first.locator("xpath=following::*[self::input or self::textarea or self::select][1]")
            if inp.count():
                return inp.first
        ph = s.locator(f'input[placeholder*="{label}" i], textarea[placeholder*="{label}" i]')
        if ph.count():
            return ph.first
        aria = s.locator(f'[aria-label="{label}" i]')
        if aria.count():
            return aria.first
        raise AssertionError(f"no field labelled {label!r}")

    def fill(self, label, value):
        f = self.field(label)
        f.click()
        f.fill(value)
        self.idle(100)

    def toasts(self):
        return self.page.locator("#toasts .toast").all_inner_texts()

    def wait_toast(self, text, timeout=4000):
        self.page.wait_for_function(
            "t => [...document.querySelectorAll('#toasts .toast')].some(e => e.innerText.includes(t))", arg=text, timeout=timeout)

    def sheet_title(self):
        if not self.page.locator("#sheet.on").count():
            return None
        return self.page.locator("#sheetin h3").inner_text()

    def text(self, el_id):
        return self.page.locator(f"#{el_id}").inner_text()

    # ---- gestures ------------------------------------------------------------------------
    # Real touch input through Chromium's DevTools input API, not synthetic DOM
    # events: the dashboard calls setPointerCapture() on press, which throws for a
    # pointer the browser never saw, and the pad listens to pointerrawupdate,
    # which only real input produces.
    def _center(self, selector):
        loc = self.page.locator(selector).first
        # Centre it: "just into view" can leave it under the fixed tab bar, and the
        # touch would land on the bar. A person scrolls it into the open first.
        loc.evaluate("e => e.scrollIntoView({block: 'center', inline: 'center'})")
        self.page.wait_for_timeout(80)
        box = loc.bounding_box()
        assert box, f"{selector} is not on screen"
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def position(self, selector):
        """Centre of an element where it is NOW - no scrolling. Use after scrolling
        a container into view once, when several fingers need consistent places."""
        box = self.page.locator(selector).first.bounding_box()
        assert box, f"{selector} is not on screen"
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def _touch(self, kind, points):
        """points: (x, y) or (x, y, id). For touchEnd/touchCancel the listed points
        are the ones lifting; for touchStart/touchMove, every finger that is down."""
        tps = []
        for i, p in enumerate(points):
            x, y, tid = (p if len(p) == 3 else (p[0], p[1], i))
            tps.append({"x": x, "y": y, "id": tid, "radiusX": 4, "radiusY": 4, "force": 1})
        self.cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": tps})

    def touch_drag(self, selector, points, step_ms=16, hold_ms=0):
        """One finger: down at the first point, through the rest, up at the last.
        Points are offsets from the element's centre."""
        cx, cy = self._center(selector)
        abs_pts = [(cx + x, cy + y) for x, y in points]
        self._touch("touchStart", [abs_pts[0]])
        if hold_ms:
            self.page.wait_for_timeout(hold_ms)
        for pt in abs_pts[1:]:
            self.page.wait_for_timeout(step_ms)
            self._touch("touchMove", [pt])
        self.page.wait_for_timeout(step_ms)
        self._touch("touchEnd", [])
        self.idle(200)

    def touch_hold(self, selector, ms=600):
        """Press and hold, then lift - the long-press every hold gesture uses."""
        self.touch_drag(selector, [(0, 0)], hold_ms=ms)

    def tap_point(self, selector):
        self.touch_drag(selector, [(0, 0), (0, 0)], step_ms=40)

    def circle(self, selector, degrees, radius=40, steps=None):
        """Turn a dial: a finger travelling `degrees` round its centre (positive = clockwise)."""
        import math
        steps = steps or max(8, int(abs(degrees) / 6))
        pts = []
        for i in range(steps + 1):
            a = math.radians(-90 + degrees * i / steps)
            pts.append((radius * math.cos(a), radius * math.sin(a)))
        self.touch_drag(selector, pts)
