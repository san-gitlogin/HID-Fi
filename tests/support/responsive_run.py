"""Opening the dashboard on one Viewport and walking it through every state.

A state is something a person can put the screen into: a tab, the keyboard
deck, the fullscreen pad, a sheet, an overlay, a message. Each is entered with
the dashboard's own functions - this is about how it is drawn, the BDD
scenarios cover how it is reached - and measured by responsive_probe.js.
"""
import json
from pathlib import Path

from support.board_sim import BoardSim, VAULT_SLOTS, PC_SLOTS, MACRO_SLOTS
from support import dashboard

PROBE_JS = (Path(__file__).parent / "responsive_probe.js").read_text(encoding="utf-8")

LONG = "Personal email for the family calendar and the school portal"
SSID32 = "Upstairs-Office-Mesh-5G-Extender"          # 32 bytes, the most a network name may be
VIEWS = ["pad", "keys", "short", "media", "game", "power", "setup"]


def crowded_board():
    """A board with the most a person could have put on it, every name as long as
    it may be: the layout has to hold up to the worst case, not the demo."""
    sim = BoardSim(pin="4826", host_os="windows")
    for s in range(VAULT_SLOTS):
        sim.vault[s] = {"label": f"{LONG} {s}", "type": ["password", "pin", "note"][s % 3], "secret": "x" * 40}
    for s in range(PC_SLOTS):
        sim.pcs[s] = {"name": f"Workstation in the second floor meeting room {s}", "os": ["windows", "mac", "linux"][s % 3],
                      "password": "pw"}
    for s in range(MACRO_SLOTS):
        sim.macros[s] = {"name": f"Morning stand-up notes template {s}", "steps": ["text hello", "key ENTER"]}
    sim.ui = json.dumps({
        "quick": [{"n": f"Toggle the microphone in the meeting app {i}", "i": "micoff", "t": "key", "v": "CTRL+SHIFT+M"}
                  for i in range(12)],
        "knobs": [{"n": f"Timeline scrub for the video editor {i}", "i": "knob", "t": "media", "up": 233, "dn": 234,
                   "step": 14, "mode": ["endless", "ranged"][i % 2]} for i in range(6)]})
    sim.networks = [{"ssid": SSID32[:32 - len(str(i))] + str(i), "rssi": -40 - 4 * i, "quality": "excellent",
                     "channel": 1 + i % 11, "encryption": "WPA2"} for i in range(14)]
    sim.ap_ssid = SSID32
    sim.sta.update(phase="online", ssid=SSID32, ip="192.168.100.254")
    sim.ap_clients = [{"ip": f"192.168.4.{i}", "mac": f"AA:BB:CC:00:11:{i:02X}", "rssi": -40, "quality": "excellent",
                       "dashboard": i % 2 == 0} for i in range(2, 9)]
    sim.host_asleep_unarmed = True
    return sim


# (state, how to enter it, how to leave it). Run in the page.
PAGE_STATES = [(f"tab {v}", f"go('{v}')", "") for v in VIEWS] + [
    ("tab short searched", "go('short');$('scq').value='a';renderSC()", "$('scq').value='';renderSC()"),
    ("deck", "go('pad');if(getComputedStyle($('padkbd')).display==='none')$('btnDeck').click()",
     "if(getComputedStyle($('padkbd')).display!=='none')$('btnDeck').click()"),
    ("mouse buttons", "go('pad');if($('btnsMouse').style.display==='none')$('btnBtnView').click()",
     "if($('btnsMouse').style.display!=='none')$('btnBtnView').click()"),
    ("gamepad on", "GP.enabled=true;gpSync();go('game')", "GP.enabled=false;gpSync()"),
    ("fullscreen pad", "go('pad');$('btnFs').click()", "$('btnFsExitTop').click()"),
    ("fullscreen deck", "go('pad');$('btnFs').click();if(getComputedStyle($('padkbd')).display==='none')$('btnDeck').click()",
     "if(getComputedStyle($('padkbd')).display!=='none')$('btnDeck').click();$('btnFsExitTop').click()"),
]
SHEET_STATES = [
    ("sheet access pin", "askPin()"),
    ("sheet lan locked", "lanToldOnce=false;lanLocked()"),
    ("sheet knob", "knobEditor(0)"),
    ("sheet action", "quickEditor(0)"),
    ("sheet macro", "macroEditor(0)"),
    ("sheet vault ask", f"vaultAsk('Type {LONG}','Typed into whatever has focus.',()=>{{}})"),
    ("sheet vault secret", f"showSecret({{label:'{LONG}',type:'password',secret:'{'x' * 64}'}})"),
    ("sheet vault edit", "vaultEditor(VAULT[0])"),
    ("sheet vault pin", "vaultPinEditor()"),
    ("sheet vault reset", "vaultAskAccess()"),
    ("sheet pc", "pcEditor(PCS[0])"),
    ("sheet help", "go('setup');showHelp()"),
    ("sheet join", f"joinWifi('{SSID32}')"),
    ("sheet security info", "secInfo()"),
    ("sheet forgot pin", "forgotPin(false)"),
    ("sheet dashboards", "showDashboards()"),
    ("sheet lost board", "MDNS_NAME='esp-hid-da1a2c';wsUp=false;lostAt=1;lostTold=false;lostHelp();wsUp=true"),
    ("sheet wake failed", "go('power');document.querySelector('[data-sys=\"wake\"]').click()"),
    ("sheet watch pick", "SCR.pcs=[{id:'a'.repeat(32),name:'Studio PC',online:true},"
                         "{id:'b'.repeat(32),name:'Kitchen laptop with a much longer name',online:false}];watchPick()"),
    ("sheet board full", "boardFull(8)"),
    ("sheet display pick", "SCR.displays=[{id:1,name:'Built-in display',width:3024,height:1964,main:true},"
                           "{id:2,name:'Display 2',width:2560,height:1440,main:false},"
                           "{id:3,name:'DELL U2723QE wide monitor on the left',width:3840,height:2160,main:false}];"
                           "SCR.disp=1;scrDispPick()"),
]
OVERLAY_STATES = [
    ("overlay boot", "bootStart('Switching the pointer to absolute. Windows will install the new device.')",
     "booting=false;$('boot').classList.remove('on')"),
    ("overlay apnew", f"apStart('{SSID32}','a-long-access-point-password-1234')", "apPending=false;$('apnew').classList.remove('on')"),
    ("overlay asw", "go('pad');aswOpen()", "aswClose(false)"),
    # The Screen tab's floating window, open and minimised, over another tab.
    ("overlay screen float", "go('screen');scrFloat(true);go('media')", "scrFloat(false)"),
    ("overlay screen float min", "go('screen');scrFloat(true);scrMinimise();go('media')", "scrFloat(false)"),
    # Watch only: the picture alone, its bar and the stage's zoom buttons.
    ("overlay watch", "watchEnter()", "watchLeave();go('pad')"),
    ("toast", f"toast('{LONG} could not be saved because the board is full','bad')", ""),
]


def open_page(browser, vp, sim=None):
    """A fresh tab sized and behaving like the viewport's device, signed in, lists loaded."""
    sim = sim or crowded_board()
    ctx = browser.new_context(viewport={"width": vp.w, "height": vp.h}, device_scale_factor=vp.dpr,
                              is_mobile=vp.mobile, has_touch=vp.touch, reduced_motion="reduce")
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())
    # The dashboard's own fullscreen, without the browser's: iPhones never have
    # the API, and on Android it only gives the page the whole screen - which is
    # the "standalone" size of every device in the matrix. Headless Chromium's
    # real fullscreen resizes the window asynchronously and would be measured
    # half way.
    page.add_init_script("Element.prototype.requestFullscreen=undefined;"
                         "Element.prototype.webkitRequestFullscreen=undefined;")
    page.add_init_script(PROBE_JS)
    page.add_init_script(SETTLE_JS)
    page.add_init_script(f"window.__hidfiInsets={json.dumps(list(vp.insets))};"
                         f"try{{sessionStorage.setItem('pin','{sim.pin}')}}catch(e){{}}")
    if any(vp.insets):
        cdp = ctx.new_cdp_session(page)
        t, r, b, l = vp.insets
        cdp.send("Emulation.setSafeAreaInsetsOverride", {"insets": {"top": t, "right": r, "bottom": b, "left": l}})
    sim.attach(page, dashboard.extract_html())
    page.goto(sim.URL, timeout=60000)          # generous: the whole ui tier runs at once
    page.wait_for_function("() => document.getElementById('ltext').textContent === 'live'", timeout=20000)
    # The page re-sends the session PIN by itself; the lists are then loaded once
    # more on purpose, because of the reconnect_lists known issue.
    page.wait_for_timeout(300)
    page.evaluate("syncAll()")
    page.wait_for_function("() => VAULT.length > 0 && PCS.length > 0", timeout=6000)
    page.wait_for_timeout(150)
    return ctx, page, sim, errors


SETTLE_JS = """window.__hidfiSettle = async () => {
  const frame = () => new Promise(r => requestAnimationFrame(() => r()));
  await frame();            // ResizeObserver work (a keyboard changing tier) lands here
  // Under load a frame can pass before even a 0.01 ms animation has run, and the
  // view would be measured mid-fade at opacity 0. Wait for the real end.
  const running = document.getAnimations().filter(a => a.playState === 'running' &&
                                                  isFinite(a.effect?.getComputedTiming().endTime));
  if (running.length) {
    await Promise.race([Promise.all(running.map(a => a.finished.catch(() => {}))), new Promise(r => setTimeout(r, 1500))]);
    await frame();
  }
};
window.__hidfiState = async (name, enter, leave, waitFor) => {
  await (new Function(enter))();
  // Some sheets open on the board's reply; under a full parallel run that can take seconds.
  if (waitFor) for (let i = 0; i < 120 && !document.querySelector(waitFor); i++) await new Promise(r => setTimeout(r, 50));
  await window.__hidfiSettle();
  // A message from an earlier step (the sign-in's "Unlocked") would sit over the
  // top bar and read as covering it; messages are measured in their own state.
  if (name !== 'toast') document.querySelectorAll('#toasts .toast').forEach(t => t.remove());
  const r = window.__hidfiProbe(name);
  if (leave) { (new Function(leave))(); await new Promise(r => setTimeout(r, 0)); await window.__hidfiSettle(); }
  return r;
};"""


def settle(page):
    page.evaluate("() => window.__hidfiSettle()")


def walk(page, states=("page", "sheet", "overlay")):
    """Every state, measured. Returns {state: {"bad": [...], "info": {...}}}."""
    out = {}
    plan = []
    if "page" in states:
        plan += [(n, a, b) for n, a, b in PAGE_STATES]
    if "sheet" in states:
        plan += [(n, a, "closeSheet()") for n, a in SHEET_STATES]
    if "overlay" in states:
        plan += OVERLAY_STATES
    for name, enter, leave in plan:
        wait = "#sheet.on" if name.startswith("sheet") else ""     # some open on the board's reply
        out[name] = page.evaluate("([n, e, l, w]) => window.__hidfiState(n, e, l, w)", [name, enter, leave, wait])
    page.evaluate("go('pad')")
    return out
