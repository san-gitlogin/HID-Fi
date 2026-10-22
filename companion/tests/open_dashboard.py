"""The app's "Open the dashboard", in real Chrome.

Seen live: the menu opened http://192.168.0.101/#screen and Chrome said
ERR_ADDRESS_UNREACHABLE - macOS kept Chrome off the local network while the
app was allowed on it. The menu now opens the app's own page on 127.0.0.1
(crates/hidfi-companion/src/opener.rs), which:

  1. goes to the board when the browser reaches it;
  2. prefers the board's own name (first) when both it and the address answer;
  3. says the browser is kept off the network, names it, and has the app open
     the settings page, when nothing answers - instead of a dead tab.

macOS's own block cannot be switched on in a test; an address that refuses
stands in for it (the browser sees the same failed request).

Run: cargo build --release -p hidfi-companion --example opener_page
     python3 companion/tests/open_dashboard.py
"""
import os, pathlib, subprocess, sys, time
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
EX = str(ROOT / "target/release/examples" / ("opener_page.exe" if os.name == "nt" else "opener_page"))
DEAD = "http://127.0.0.1:9"          # refuses at once, like a browser kept off the network
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)


def board(mac):
    p = subprocess.Popen([sys.executable, str(ROOT / "tests" / "model_board.py"), "4826"],
                         env=dict(os.environ, HOST="127.0.0.1", MAC=mac), stdout=subprocess.PIPE, text=True)
    return p, f"http://127.0.0.1:{int(p.stdout.readline())}"


def opener(*urls, browser=None):
    env = dict(os.environ, **({"OPENER_BROWSER": browser} if browser else {}))
    p = subprocess.Popen([EX, *urls], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
    return p, p.stdout.readline().strip()


A, a_url = board("02:00:5E:10:00:0A")
B, b_url = board("02:00:5E:10:00:0B")
procs = [A, B]
try:
    with sync_playwright() as pw:
        br = pw.chromium.launch(channel="chrome")
        pg = br.new_page()

        o, page = opener(DEAD, a_url); procs.append(o)
        pg.goto(page)
        pg.wait_for_url(a_url + "/#screen", timeout=10000)
        check("reachable: goes straight to the board's dashboard", pg.url == a_url + "/#screen", pg.url)

        o, page = opener(b_url, a_url); procs.append(o)
        pg.goto(page)
        pg.wait_for_url("**/#screen", timeout=10000)
        check("both answer: the first (the board's own name) wins", pg.url == b_url + "/#screen", pg.url)

        o, page = opener(DEAD, "http://127.0.0.1:1"); procs.append(o)
        t0 = time.time()
        pg.goto(page)
        pg.wait_for_selector("#fail:not([hidden])", timeout=10000)
        text = pg.inner_text("#fail")
        check("nothing answers: it says the browser is kept off the network",
              "This browser cannot reach the board" in text and "Google Chrome" in text, f"{time.time() - t0:.1f} s")
        if sys.platform == "darwin":
            check("...names macOS's switch", "Local Network" in text)
            pg.click("#settings")
            line = o.stdout.readline().strip()
            check("...and its button has the app open the settings page", line == "settings", line)
        check("...and lists the addresses for another browser", "127.0.0.1:9" in text)

        # Which browser it names. Arc, Vivaldi and Chromium all say "Chrome" in
        # their user agent: the app tells the page which browser it opened.
        def named(default, ua=None):
            o, page = opener(DEAD, browser=default); procs.append(o)
            ctx = br.new_context(user_agent=ua) if ua else br.new_context()
            p = ctx.new_page(); p.goto(page)
            p.wait_for_selector("#fail:not([hidden])", timeout=10000)
            r = (p.inner_text("#who"), p.is_visible("#settings"), p.inner_text("#fail"))
            ctx.close()
            return r
        w, _, _ = named("Arc")
        check("default browser Arc: the page says Arc, not the Chrome its user agent claims", w == "Arc", w)
        w, _, _ = named("Firefox")
        check("link pasted into Chrome while Firefox is the default: it says Google Chrome", w == "Google Chrome", w)
        if sys.platform == "darwin":
            safari_ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                         "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
            w, btn, text = named("Safari", safari_ua)
            check("Safari: not sent to a switch macOS does not give it", w == "Safari" and not btn
                  and "without asking" in text and "Switch on" not in text, text[:120])
        if sys.platform == "darwin":       # a fresh CI Windows or Linux may have no default set
            o, page = opener(DEAD, browser="auto"); procs.append(o)
            p = br.new_page(); p.goto(page); p.wait_for_selector("#fail:not([hidden])", timeout=10000)
            check("this computer's default browser, read from the OS", p.inner_text("#who") not in ("", "your browser"),
                  p.inner_text("#who"))
        br.close()
finally:
    for p in procs:
        p.kill()
sys.exit(0 if ok else 1)
