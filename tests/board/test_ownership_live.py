"""The ownership story on a real board, through the real dashboard in Chromium.

The simulated-board scenarios (tests/ui/bdd/features/ownership.feature) prove the
dashboard and the model agree. This proves the board itself does it, end to end,
the way an owner would: the page served by the board, its WebSocket, its PIN
checks, its vault, and recovery over the USB cable.

The story, in order - each step needs the ones before it:
  1. the first access PIN is refused without the WiFi password, and with a wrong one
  2. with the right one it is set, and the page never received that password
  3. a reload asks for the PIN; the right one signs in
  4. a vault PIN is added and a test secret stored, from the page
  5. the access PIN is changed from the page; the secret survives
  6. three wrong PINs lock out this device - and never the USB cable
  7. recovery over USB resets the access PIN; the vault PIN keeps the secret
  8. without a vault PIN, recovery refuses to erase the secret unless told to
  9. the WiFi password is changed over USB, and the new one is the proof
 10. the WiFi password is changed from the page, and read back over USB

Runs only on a bench board: no access PIN, nothing in the vault, and reachable
over the network given by --board-url (the home network, or the board's own
WiFi at 192.168.4.1 - then step 9-10 restart the access point under this
machine's feet, so they are skipped). Everything is put back: the PIN removed,
the vault emptied, the WiFi password restored. Nothing is printed that holds
the WiFi password; it is kept in memory for the run only.
"""
import json
import re
import secrets
import subprocess
import sys
import time

import pytest

from support import paths
from support.ui import TABS

pytestmark = pytest.mark.board

ROOT = paths.TESTS.parent
PIN_A, PIN_B, PIN_C, VAULT_PIN = "7316", "5284", "9047", "6193"
LABEL = "HIDFI-LIVE-TEST"


# ---- the run's state, shared by the steps -------------------------------------------------
class Story:
    def __init__(self):
        self.failed = None
        self.pin = ""           # the access PIN the board has now, as far as this run knows
        self.vault_pin = ""
        self.ap = None          # {"ssid", "password"} as found - restored at the end
        self.ap_now = ""        # the WiFi password now
        self.frames_in = []     # every text frame the page received
        self.toasts = []


def _step(story):
    if story.failed:
        pytest.skip(f"an earlier step failed ({story.failed})")


@pytest.fixture(scope="module")
def story(request, _board_holder):
    url = request.config.getoption("--board-url")
    if not url:
        pytest.skip("needs --board-url ws://<board>:81 (home-network address, or 192.168.4.1 on its WiFi)")
    pytest.importorskip("playwright.sync_api", reason="pip install playwright && playwright install chromium")
    board = _board_holder.get()
    st = board.status()
    if st.get("auth_set"):
        pytest.skip("this board has an access PIN - the live story needs a bench board without one")
    if board.cmd({"cmd": "vault_list"}).get("entries"):
        pytest.skip("this board stores secrets - the live story needs an empty vault")
    ap = board.cmd({"cmd": "ap_password"})
    if ap.get("status") != "ok":
        pytest.skip("firmware older than v3.10")
    if ap.get("open"):
        pytest.skip("the board's WiFi is open - the story is about a password-protected board")
    s = Story()
    s.host = re.sub(r"^wss?://", "", url).split(":")[0].split("/")[0]
    s.on_ap = s.host.startswith("192.168.4.")
    s.ap = {"ssid": ap["ssid"], "password": ap["password"]}
    s.generated = bool(ap.get("generated"))
    s.ap_now = ap["password"]
    s.holder = _board_holder
    yield s
    _put_back(s, _board_holder)


def _put_back(s, holder):
    """Leave the board as found, whatever happened. Order matters: the vault PIN
    needs the access PIN, and the access PIN is removed last."""
    b = holder.get()
    problems = []
    try:
        if s.vault_pin and s.pin:
            b.cmd({"cmd": "vault_pin", "pin": s.vault_pin, "new_pin": ""})
        b.cmd({"cmd": "vault_wipe"})
        if b.status().get("auth_set"):
            b.cmd({"cmd": "set_auth", "token": "", "old": s.pin})
        if b.cmd({"cmd": "ap_password"}).get("password") != s.ap["password"]:
            b.ser.write((json.dumps({"cmd": "ap_configure", "ssid": s.ap["ssid"],
                                                    "password": s.ap["password"], "open": False}) + "\n").encode())
            time.sleep(2.0)
        st = b.status()
        if st.get("auth_set"):
            problems.append("the access PIN is still set")
        vl = b.cmd({"cmd": "vault_list"})
        if vl.get("entries") or vl.get("vault_pin_set"):
            problems.append("the vault is not empty or still has a vault PIN")
        back = b.cmd({"cmd": "ap_password"})
        if bool(back.get("generated")) != s.generated:
            problems.append("the board no longer says whether its WiFi password is its own")
        if back.get("password") != s.ap["password"]:
            problems.append("the WiFi password was not restored - run ./flash_esp.sh --recover --show-password")
    except Exception as e:      # never print anything that could hold the password
        problems.append(f"restore failed: {type(e).__name__}")
    assert not problems, "board NOT left as found: " + "; ".join(problems)


@pytest.fixture(scope="module")
def chromium(story):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture(scope="module")
def page(story, chromium):
    ctx = chromium.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True)
    ctx.add_init_script("""
      window.__toasts = [];
      new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(n => {
        if (n.classList && n.classList.contains('toast')) window.__toasts.push(n.innerText);
      }))).observe(document, {childList: true, subtree: true});
    """)
    p = ctx.new_page()
    p.on("websocket", lambda ws: ws.on("framereceived",
                                       lambda f: story.frames_in.append(f if isinstance(f, str) else "")))
    yield p
    ctx.close()


# ---- page helpers -----------------------------------------------------------------------------
def _open(story, page):
    page.goto(f"http://{story.host}/", timeout=20000)
    page.wait_for_function("() => document.getElementById('ltext').textContent === 'live'", timeout=15000)
    page.wait_for_timeout(800)


def _close_sheet(page):
    if page.locator("#sheet.on").count():
        page.evaluate("closeSheet()")
        page.wait_for_timeout(250)


def _tab(page, name):
    _close_sheet(page)
    vid = TABS[name]
    page.locator(f'[data-v="{vid}"]:visible').first.click()
    page.wait_for_function(f"() => curView === '{vid}'")
    page.wait_for_timeout(200)


def _digits(page, row, pin):
    boxes = page.locator(f"{row} .pinbox")
    boxes.first.evaluate("e => e.scrollIntoView({block: 'center'})")
    boxes.first.click()
    page.wait_for_timeout(120)
    for d in pin:
        page.keyboard.type(d)
        page.wait_for_timeout(40)


def _secret(page, sel, value):
    """Type a secret into a field the way a person does - tap it first, which is
    what lifts the dashboard's anti-autofill readonly - without handing the value
    to Playwright's fill(): its error log prints what it was asked to type."""
    page.locator(sel).click()
    page.wait_for_timeout(80)
    page.eval_on_selector(sel, "(e, v) => { e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); }", value)


def _toast(page, text, timeout=6000):
    page.wait_for_function("t => window.__toasts.some(x => x.includes(t))", arg=text, timeout=timeout)


def _clear_toasts(page):
    page.evaluate("window.__toasts = []")


def _sign_in(page, pin):
    page.wait_for_selector("#sheet.on #pinAsk .pinbox", timeout=10000)
    _digits(page, "#sheet.on #pinAsk", pin)


def _board(story):
    return story.holder.get()


def _recover(story, *args):
    """Run the USB recovery tool as a person would. The tests' own serial session
    lets go of the port first; output is returned, never printed."""
    story.holder.release()
    try:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "recover.py"), "--port", story.holder.port, *args],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    finally:
        story.holder.get()
    return r.returncode, r.stdout + r.stderr


def _hide(story, text):
    """For assertion messages: the WiFi passwords never appear in a report."""
    for pw in {story.ap["password"], story.ap_now}:
        if pw:
            text = text.replace(pw, "<wifi-password>")
    return text


# ---- the story ----------------------------------------------------------------------------------
# Secrets never sit inside an assert expression: pytest prints the values of a
# failed comparison, so each check is reduced to a bool first.
@pytest.fixture(autouse=True)
def _track(request, story):
    _step(story)
    before = request.session.testsfailed
    yield
    if request.session.testsfailed > before:        # the call's report is logged before teardown
        story.failed = request.node.name


def test_01_the_first_pin_needs_the_wifi_password(story, page):
    _open(story, page)
    _tab(page, "Settings")
    assert page.locator("#pinProof").is_visible(), "the WiFi password field is not shown for the first PIN"
    _clear_toasts(page)
    _digits(page, "#pinSet", PIN_A)
    page.locator("#btnPin").click()
    _toast(page, "Enter this board's WiFi password first")
    _secret(page, "#pinProofPw", "not-the-password")
    _digits(page, "#pinSet", PIN_A)
    page.locator("#btnPin").click()
    _toast(page, "not this board's WiFi password")
    assert _board(story).status().get("auth_set") is False, "a PIN was set without the WiFi password"


def test_02_the_right_wifi_password_sets_it_and_never_comes_back(story, page):
    _clear_toasts(page)
    _secret(page, "#pinProofPw", story.ap["password"])
    _digits(page, "#pinSet", PIN_A)
    page.locator("#btnPin").click()
    _toast(page, "PIN set")
    story.pin = PIN_A
    assert _board(story).status().get("auth_set") is True
    assert page.locator("#pinProofPw").input_value() == "", "the WiFi password stayed in the field"
    page.wait_for_function("() => getComputedStyle(document.getElementById('pinProof')).display === 'none'",
                           timeout=5000)
    leaked = any(story.ap["password"] in f for f in story.frames_in)
    assert not leaked, "the board sent its WiFi password over WiFi"


def test_03_a_reload_asks_for_the_pin_and_the_right_one_signs_in(story, page):
    page.evaluate("sessionStorage.clear()")
    _open(story, page)
    _sign_in(page, PIN_A)
    page.wait_for_function("() => !document.getElementById('sheet').classList.contains('on')", timeout=8000)
    assert page.evaluate("authOK") is True


def test_04_a_vault_pin_and_a_stored_secret_from_the_page(story, page):
    _clear_toasts(page)
    _tab(page, "Keys")
    page.locator("#btnVaultPin").click()
    page.wait_for_selector("#sheet.on #vpCur")
    _digits(page, "#vpCur", PIN_A)
    _digits(page, "#vpNew", VAULT_PIN)
    page.locator("#vp_ok").click()
    _toast(page, "Vault PIN saved")
    story.vault_pin = VAULT_PIN
    page.wait_for_timeout(500)
    page.locator("#btnVaultNew").click()
    page.wait_for_selector("#sheet.on #v_l")
    page.fill("#v_l", LABEL)
    _secret(page, "#v_s", "live-" + secrets.token_hex(6))
    page.locator("#v_ok").click()
    page.wait_for_timeout(800)
    vl = _board(story).cmd({"cmd": "vault_list"})
    assert vl.get("vault_pin_set") is True, "the board has no vault PIN"
    assert [e.get("label") for e in vl.get("entries") or []] == [LABEL], "the secret was not stored"


def test_05_changing_the_access_pin_keeps_the_vault(story, page):
    _close_sheet(page)
    _clear_toasts(page)
    _tab(page, "Settings")
    _digits(page, "#pinSet", PIN_B)
    page.locator("#btnPin").click()
    _toast(page, "PIN set")
    story.pin = PIN_B
    assert not any("erased" in t for t in page.evaluate("window.__toasts")), "the page said the vault was erased"
    assert len(_board(story).cmd({"cmd": "vault_list"}).get("entries") or []) == 1, "the secret was lost"


def test_06_wrong_pins_lock_this_device_and_never_the_cable(story, page):
    page.evaluate("sessionStorage.clear()")
    _open(story, page)
    for _ in range(3):
        _sign_in(page, "0000")
        page.wait_for_timeout(500)
    page.wait_for_function("() => /Locked|Too many/.test((document.getElementById('pinAskMsg')||{}).textContent||'')",
                           timeout=6000)
    # The right PIN is refused from this device while it is locked...
    _sign_in(page, PIN_B)
    page.wait_for_timeout(700)
    assert page.evaluate("authOK") is not True, "the right PIN got in during the lockout"
    # ...while the cable is never locked out.
    assert _board(story).cmd({"cmd": "auth", "token": PIN_B}).get("reply") == "auth_ok"
    # The lock is short this early (5 s for the third wrong PIN); after it, the owner gets in.
    page.wait_for_timeout(16000)
    _open(story, page)
    _sign_in(page, PIN_B)
    page.wait_for_function("() => authOK === true", timeout=8000)


def test_07_recovery_over_usb_resets_the_pin_and_the_vault_pin_keeps_the_secret(story, page):
    code, out = _recover(story, "--reset-pin", PIN_C)
    kept = "kept" in out
    assert code == 0 and kept, _hide(story, out)
    story.pin = PIN_C
    vl = _board(story).cmd({"cmd": "vault_list"})
    assert [e.get("label") for e in vl.get("entries") or []] == [LABEL], "recovery lost the secret"
    # The page, holding the old PIN, is asked again and the new one works.
    page.evaluate("sessionStorage.clear()")
    _open(story, page)
    _sign_in(page, PIN_C)
    page.wait_for_function("() => authOK === true", timeout=8000)


def test_08_without_a_vault_pin_recovery_will_not_erase_unasked(story, page):
    b = _board(story)
    assert b.cmd({"cmd": "vault_pin", "pin": VAULT_PIN, "new_pin": ""}).get("reply") == "vault_pin_set"
    story.vault_pin = ""
    code, out = _recover(story, "--reset-pin", PIN_A)
    warned = "WILL BE ERASED" in out
    assert code == 1 and warned, _hide(story, out)
    assert _board(story).status().get("auth_set") is True
    assert len(_board(story).cmd({"cmd": "vault_list"}).get("entries") or []) == 1, "refused, yet the vault changed"
    code, out = _recover(story, "--reset-pin", PIN_A, "--erase-vault")
    erased = "erased, as warned" in out
    assert code == 0 and erased, _hide(story, out)
    story.pin = PIN_A
    assert not _board(story).cmd({"cmd": "vault_list"}).get("entries"), "asked to erase, yet the secret is there"


def test_09_a_new_wifi_password_over_usb_is_the_new_proof(story, page):
    if story.on_ap:
        pytest.skip("on the board's own WiFi - changing its password would drop this machine")
    new = "live-" + secrets.token_urlsafe(9)
    code, out = _recover(story, "--set-password", new)
    story.ap_now = new
    assert code == 0, _hide(story, out)
    code, out = _recover(story, "--show-password")
    reads_back = new in out
    assert code == 0 and reads_back, "the new password does not read back"
    # Back to no access PIN; the old WiFi password no longer proves anything, the new one does.
    assert _board(story).cmd({"cmd": "set_auth", "token": "", "old": story.pin}).get("status") == "ok"
    story.pin = ""
    page.evaluate("sessionStorage.clear()")
    _open(story, page)
    _tab(page, "Settings")
    _clear_toasts(page)
    _secret(page, "#pinProofPw", story.ap["password"])
    _digits(page, "#pinSet", PIN_A)
    page.locator("#btnPin").click()
    _toast(page, "not this board's WiFi password")
    assert _board(story).status().get("auth_set") is False, "the old WiFi password still worked"
    _clear_toasts(page)
    _secret(page, "#pinProofPw", new)
    _digits(page, "#pinSet", PIN_A)
    page.locator("#btnPin").click()
    _toast(page, "PIN set")
    story.pin = PIN_A


def test_10_the_wifi_password_changed_from_the_page(story, page):
    if story.on_ap:
        pytest.skip("on the board's own WiFi - changing its password would drop this machine")
    newer = "page-" + secrets.token_urlsafe(9)
    _close_sheet(page)
    _tab(page, "Settings")
    page.locator("#apSsid").click()                 # a tap lifts the anti-autofill readonly
    page.fill("#apSsid", story.ap["ssid"])
    _secret(page, "#apPass", newer)
    page.once("dialog", lambda d: d.accept())
    page.locator("#btnAp").click()
    story.ap_now = newer
    time.sleep(3)
    got = _board(story).cmd({"cmd": "ap_password"})
    landed = got.get("password") == newer and got.get("ssid") == story.ap["ssid"]
    assert landed, "the page's change did not land"
    leaked = any(newer in f for f in story.frames_in)
    assert not leaked, "the board echoed the new WiFi password over WiFi"
