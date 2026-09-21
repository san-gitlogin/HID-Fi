"""The dashboard's reply queue must never drift. Needs no board.

The board answers commands in the order they arrive, one reply each, and
refuses everything with `auth_required` until an `auth` succeeds. The dashboard
matches replies to callbacks by position, so that only works if every command
takes exactly one slot and every reply consumes exactly one.

Both halves were once wrong: a command sent without a callback pushed no slot
but was still answered, and `auth_required` returned without consuming one.
Either one shifts every later reply onto the wrong handler for the rest of the
socket's life. What the user saw was an empty Keys tab while the board was
holding their passwords - the vault's answer was being delivered to another
card's callback - and no amount of refreshing fixed it, because the sequence is
identical on every load.

This test serves the real dashboard against a fake board that speaks
WebSocket and demands a PIN, which is the only way to reach that code path. An
HTTP-only stub cannot see it.

Requires: pip install playwright websockets  (and `playwright install chromium`)
"""
import asyncio
import http.server
import json
import os
import socketserver
import sys
import threading
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTTP_PORT = 8781
WS_PORT = 8181          # the page is told this via a patched location

SECRET = "correct-horse-battery"
STATE = {"auth": "1234", "vault_pin": "", "vault": {}, "pcs": {}}

STATUS = {
    "status": "ok", "reply": "status", "chip": "ESP32-S3", "firmware": "hid_fi_v3.8",
    "uptime_sec": 1893, "free_heap": 191932, "heap_floor": 181804, "heap_low": False,
    "total_psram": 8388608, "free_psram": 8380092, "flash_size": 16777216,
    "hid_ready": True, "usb_mounted": True, "usb_suspended": False,
    "power_mode": "balanced", "radio_awake": True, "ap_auto_off": False,
    "ap_running": True, "lan_exposed": False, "sta_phase": "idle", "sta_error": "",
    "sta_ip_mode": "dhcp", "sta_target": "", "cmd_count": 1, "wifi_cmd_count": 1,
    "wifi_connected": False, "wifi_ip": "", "wifi_ssid": "", "wifi_rssi": 0,
    "wifi_quality": "", "ap_ssid": "ESP32-HID-TEST", "ap_ip": "192.168.4.1",
    "ap_clients": 1, "channel": 1, "mac": "DC:B4:D9:00:00:01",
    "ap_mac": "DE:B4:D9:00:00:01", "ws_clients": 1, "mdns": "esp-hid-test",
    "pc_state": "unlocked", "host_os": "mac", "host_os_source": "auto",
    "host_os_detected": "mac", "detect_phase": "settled",
    "usb_str_reqs": 9, "usb_str_rereads": 4, "usb_str_seq": "0,0,1,1,2,2,3,3,4,0,0,0,0,0,0,0",
    "usb_set_idle": 1, "usb_ctrl_reqs": 1, "usb_led_reports": 0,
    "pointer_mode": "relative", "gamepad": False, "verbose": False,
    "wiggle_active": False, "wiggle_mode": "idle", "led_on": False,
    "boot_button": "released",
}


def reset():
    STATE.update({"auth": "1234", "vault_pin": ""})
    STATE["vault"].clear()
    STATE["vault"][0] = {"label": "someone.long@example.com", "type": "password",
                         "secret": SECRET}


def status():
    s = dict(STATUS)
    s["auth_set"] = bool(STATE["auth"])
    return s


def gate():
    return STATE["vault_pin"] or STATE["auth"]


def handle(req):
    """One reply per command, exactly as the firmware does."""
    cmd = req.get("cmd", "")
    if cmd == "status":
        return status()
    if cmd == "ping":
        return {"status": "ok", "reply": "pong", "uptime": 1}
    if cmd == "auth":
        return ({"status": "ok", "reply": "auth_ok"} if req.get("token") == STATE["auth"]
                else {"status": "error", "reply": "auth_failed", "message": "Wrong PIN"})
    if cmd == "set_auth":
        STATE["auth"] = req.get("token", "")
        return {"status": "ok", "reply": "auth_set", "auth_set": bool(STATE["auth"]),
                "vault_wiped": False}
    if cmd == "ui_get":
        return {"status": "ok", "reply": "ui", "ui": ""}
    if cmd == "macro_list":
        return {"status": "ok", "reply": "macro_list", "macros": [], "slots": 8}
    if cmd == "pc_list":
        return {"status": "ok", "reply": "pc_list", "auth_set": bool(STATE["auth"]),
                "slots": 8, "profiles": []}
    if cmd == "vault_list":
        return {"status": "ok", "reply": "vault_list", "slots": 12,
                "auth_set": bool(STATE["auth"]),
                "vault_pin_set": bool(STATE["vault_pin"]), "locked": False,
                "entries": [{"slot": k, "label": v["label"], "type": v["type"]}
                            for k, v in sorted(STATE["vault"].items())]}
    if cmd == "vault_save":
        label, secret = (req.get("label") or "").strip(), req.get("secret", "")
        kind, slot = req.get("type", "password"), req.get("slot", -1)
        if slot not in STATE["vault"]:
            slot = next((i for i in range(12) if i not in STATE["vault"]), -1)
            STATE["vault"][slot] = {"label": label, "type": kind, "secret": secret}
        else:
            e = STATE["vault"][slot]
            e["label"], e["type"] = label, kind
            if secret:
                e["secret"] = secret
        return {"status": "ok", "reply": "vault_saved", "slot": slot, "label": label,
                "type": kind}
    if cmd == "vault_get":
        if req.get("pin", "") != gate():
            return {"status": "error", "reply": "vault_denied", "message": "PIN required"}
        e = STATE["vault"].get(req.get("slot", -1))
        return ({"status": "ok", "reply": "vault_secret", "slot": req["slot"],
                 "label": e["label"], "type": e["type"], "secret": e["secret"]}
                if e else {"status": "error", "message": "Empty slot"})
    if cmd == "vault_pin":
        nxt = req.get("new_pin", "")
        knows_vault = STATE["vault_pin"] and req.get("pin", "") == STATE["vault_pin"]
        knows_access = req.get("access", "") and req.get("access", "") == STATE["auth"]
        if not knows_vault and not knows_access:
            return {"status": "error", "reply": "vault_denied", "message": "Wrong PIN"}
        STATE["vault_pin"] = nxt
        return {"status": "ok", "reply": "vault_pin_set", "vault_pin_set": bool(nxt),
                "vault_wiped": False}
    return {"status": "ok", "reply": cmd}


OPEN = {"ping", "auth"}


def extract_html():
    with open(os.path.join(REPO, "hid_fi", "web_ui.h"), encoding="utf-8") as f:
        t = f.read()
    s = t.index('R"rawliteral(') + len('R"rawliteral(')
    html = t[s:t.index(')rawliteral"', s)]
    # The page builds its socket URL from location; point it at the test port.
    return html.replace("':81/'", f"':{WS_PORT}/'")


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _j(self, o):
        b = json.dumps(o).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            return self._j(status())
        if self.path.startswith("/api/"):
            return self._j({"status": "ok"})
        b = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            req = {}
        return self._j(handle(req))


HTML = extract_html()


def start_servers():
    import websockets

    async def ws_handler(conn):
        authed = not STATE["auth"]
        async for raw in conn:
            if isinstance(raw, (bytes, bytearray)):
                continue
            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                continue
            cmd = req.get("cmd", "")
            if STATE["auth"] and not authed and cmd not in OPEN:
                await conn.send(json.dumps({"status": "error", "reply": "auth_required",
                                            "message": "Access PIN required"}))
                continue
            rep = handle(req)
            if rep.get("reply") == "auth_ok":
                authed = True
            await conn.send(json.dumps(rep))

    async def main():
        async with websockets.serve(ws_handler, "127.0.0.1", WS_PORT):
            await asyncio.Future()

    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(("127.0.0.1", HTTP_PORT), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    threading.Thread(target=lambda: asyncio.run(main()), daemon=True).start()
    time.sleep(1.0)
    return httpd


class Run:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, label, ok, detail=""):
        print(f"  [{'OK ' if ok else 'FAIL'}] {label}{(': ' + detail) if detail else ''}")
        if ok:
            self.passed += 1
        else:
            self.failed += 1


def type_pin(p, row, digits):
    for i, d in enumerate(digits):
        p.fill(f"#{row} .pinbox >> nth={i}", d)
        p.wait_for_timeout(60)


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed - skipping (pip install playwright)")
        return 0
    try:
        import websockets  # noqa: F401
    except ImportError:
        print("websockets is not installed - skipping (pip install websockets)")
        return 0

    reset()
    start_servers()
    r = Run()
    print("=" * 62)
    print("  Dashboard reply-queue alignment (no board needed)")
    print("=" * 62)

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 390, "height": 844},
                            device_scale_factor=2, is_mobile=True, has_touch=True,
                            color_scheme="dark")
        p = ctx.new_page()
        errors = []
        p.on("pageerror", lambda e: errors.append(str(e)))
        p.goto(f"http://127.0.0.1:{HTTP_PORT}/", wait_until="load")
        p.wait_for_timeout(2000)

        print("\n-- the board refuses everything until the PIN is given --")
        r.check("the socket is up", p.evaluate("wsUp") is True)
        r.check("the PIN prompt is showing", p.locator("#pinAsk .pinbox").count() == 4)

        type_pin(p, "pinAsk", "1234")
        p.wait_for_timeout(1500)
        r.check("the prompt closed once accepted", p.locator("#sheet.on").count() == 0)
        r.check("the page considers itself authed", p.evaluate("authOK") is True)

        print("\n-- every refusal must have consumed its slot --")
        q = p.evaluate("waiters.length")
        r.check("no callbacks stranded after the refusals", q == 0, f"waiters.length={q}")

        print("\n-- so the vault the board holds actually reaches the page --")
        p.evaluate("""()=>{const b=[...document.querySelectorAll('nav button')]
          .find(x=>x.textContent.trim().replace(/[^A-Za-z]/g,'')==='Keys'); if(b)b.click();}""")
        p.wait_for_timeout(800)
        r.check("the vault list arrived", p.evaluate("VAULT.length") == 1,
                f"VAULT.length={p.evaluate('VAULT.length')}")
        r.check("and is rendered", p.locator("[data-vtype]").count() == 1)
        r.check("auth state reached the vault card", p.evaluate("V_AUTH") is True)

        print("\n-- and stays aligned as the user works --")
        p.click("#btnVaultNew")
        p.wait_for_timeout(400)
        p.fill("#v_l", "Netflix")
        p.fill("#v_s", "hunter2")
        p.click("#v_ok")
        p.wait_for_timeout(1200)
        r.check("a save shows up without a reload", p.locator("[data-vtype]").count() == 2)
        r.check("queue aligned after a save", p.evaluate("waiters.length") == 0)

        p.click("#btnVaultPin")
        p.wait_for_timeout(400)
        type_pin(p, "vpCur", "1234")
        type_pin(p, "vpNew", "4321")
        p.click("#vp_ok")
        p.wait_for_timeout(1200)
        r.check("a vault PIN sticks in the UI", p.evaluate("V_PINSET") is True)
        r.check("queue aligned after a vault PIN change", p.evaluate("waiters.length") == 0)

        p.click("[data-vshow]")
        p.wait_for_timeout(500)
        type_pin(p, "vpinRow", "4321")
        p.wait_for_timeout(1000)
        r.check("a reveal returns the stored secret",
                p.locator("#vshow").count() == 1 and p.input_value("#vshow") == SECRET)

        r.check("no uncaught page errors", not errors, "; ".join(errors[:2]))
        ctx.close()
        b.close()

    print("=" * 62)
    print(f"  {r.passed} passed, {r.failed} failed")
    print("=" * 62)
    return 0 if r.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
