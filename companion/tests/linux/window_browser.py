"""The Companion's window on Linux: the real app serves it to a browser from
its loopback server (opener.rs), as the tray's "Open window" does. Chromium
loads it; the page draws this computer, the long poll answers, and a click
reaches the app - and a page from another site cannot press anything.

Run (inside.sh): python3 tests/linux/window_browser.py ./target/release/hidfi-companion
"""
import os, pathlib, subprocess, sys, tempfile, time, urllib.request
from playwright.sync_api import sync_playwright

exe = sys.argv[1]
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("ok  " if cond else "FAIL"), name, detail, flush=True)


home = pathlib.Path(tempfile.mkdtemp(prefix="hidfi-home-"))
url_file = home / "window-url"
env = dict(os.environ, HIDFI_HOME=str(home), HIDFI_NO_AUTOSTART="1", RUST_LOG="info")
app = subprocess.Popen([exe, "--window-url-file", str(url_file)], env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    for _ in range(100):
        if url_file.exists() and url_file.read_text().strip():
            break
        time.sleep(0.1)
    url = url_file.read_text().strip() if url_file.exists() else ""
    check("the app says where its window is", url.startswith("http://127.0.0.1:") and url.endswith("/window"), url)
    base = url.rsplit("/window", 1)[0]
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 900, "height": 700})
        errs, posts = [], []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("response", lambda r: r.request.method == "POST" and posts.append((r.url, r.status)))
        pg.goto(url)
        pg.wait_for_function("window.__S && __S.m", timeout=15000)
        check("the window draws this computer", pg.locator(".computer").inner_text().strip() != "")
        check("no board here: it says what to do", "Plug HID-Fi into this computer" in pg.locator("body").inner_text()
              or "Looking for HID-Fi" in pg.locator("body").inner_text())
        pg.get_by_role("button", name="Check for updates", exact=True).click()
        pg.wait_for_timeout(800)
        check("a click reaches the app", (base + "/act", 200) in posts, posts)
        check("no page error", not errs, errs)
        # Another site, knowing the path, cannot press a button.
        req = urllib.request.Request(base + "/act", data=b"act:check-updates", method="POST",
                                     headers={"Origin": "http://evil.example"})
        try:
            code = urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as e:
            code = e.code
        check("a POST from another site is refused", code == 404, code)
        b.close()
finally:
    app.terminate()
    try:
        out, _ = app.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        app.kill()
        out, _ = app.communicate()
sys.exit(0 if ok else 1)
