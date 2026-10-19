"""A behavioural model of the HID-Fi board, for driving the real dashboard in Chromium.

Every reply here is shaped like the firmware's (hid_fi/hid_fi.ino); where the
firmware has rules the dashboard depends on - the access PIN gate and its
lockout, the vault and its separate PIN, saved PCs needing a PIN, the WiFi join
phases, reboots for pointer mode and the gamepad, the access point restart -
they are modelled the same way. tests/board/test_sim_contract.py sends the same
commands to a real board and to this model and fails if the replies differ in
shape, so the model cannot drift from the firmware unnoticed.

Plugged into Playwright with page.route_web_socket(): the dashboard's own
WebSocket code runs unchanged and talks to this object.

    sim = BoardSim(pin="1234")
    sim.attach(page)                 # routes HTTP + WebSocket
    page.goto(sim.URL)
    ...
    sim.received("vault_save")       # what the dashboard sent
    sim.advance(60)                  # skip a lockout without waiting
"""
import json
import os
import re
import struct
import threading
import time

URL = "http://hidfi.test/"
WS_URL = "ws://hidfi.test:81/"
AP_URL = "http://192.168.4.1/"
AP_WS_URL = "ws://192.168.4.1:81/"

OP = {1: "move", 2: "button", 3: "abs", 4: "ping", 5: "gamepad", 6: "consumer", 7: "key"}
GESTURES = ["switch_app", "task_view", "desktop_left", "desktop_right", "show_desktop", "back",
            "forward", "close_tab", "new_tab", "copy", "paste", "undo", "zoom_in", "zoom_out"]
MEDIA = ["volume_up", "vol_up", "volume_down", "vol_down", "mute", "play_pause", "play", "next", "prev",
         "previous", "stop", "rewind", "fast_forward", "brightness_up", "brightness_down", "browser_back",
         "browser_forward", "search", "home", "calculator", "sleep", "power"]
BACKOFF = [0, 0, 5, 15, 60, 300]
VAULT_SLOTS, PC_SLOTS, MACRO_SLOTS = 12, 8, 8
MEDIA_MSG = ("Unknown media key (volume_up|volume_down|mute|play_pause|next|prev|stop|brightness_up|"
             "brightness_down|browser_back|browser_forward|search|home|calculator|sleep|power)")



def _load_actions():
    """The firmware's ACTIONS table, read from hid_fi.ino itself, so the model
    can never drift from it: name -> (label, mac, windows, linux)."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "hid_fi", "hid_fi.ino"), encoding="utf-8").read()
    body = src[src.index("static const char* const ACTIONS[][5]"):]
    body = body[:body.index("};")]
    rows = re.findall(r'\{\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)"\s*\}', body)
    return {r[0]: r[1:] for r in rows}


ACTIONS = _load_actions()
ACTION_COL = {"mac": 1, "windows": 2, "linux": 3}
ALSO = "open <app> | run <command or url> | type <text> | key <combo> | wait <ms> | click left|right|middle"

class _Lock:
    """The firmware's escalating lockout: one free retry, then 5s, 15s, 60s, 5 min."""
    def __init__(self, sim):
        self.sim, self.fails, self.until = sim, 0, 0.0

    def locked(self):
        return self.until > self.sim.now()

    def remaining(self):
        return int(self.until - self.sim.now()) + 1 if self.locked() else 0

    def fail(self):
        self.fails = min(self.fails + 1, 250)
        wait = BACKOFF[min(self.fails, 5)]
        if wait:
            self.until = self.sim.now() + wait

    def ok(self):
        self.fails, self.until = 0, 0.0


LAN_FAIL_BUDGET, LAN_FAIL_WINDOW, LAN_LOCK = 20, 600.0, 600.0
AP_IP, LAN_IP = "192.168.4.2", "192.168.1.50"


class BoardSim:
    URL = URL

    def __init__(self, pin="", host_os="mac", detected=None, firmware="hid_fi_v3.10"):
        self.lock = threading.RLock()
        self._offset = 0.0
        self.t0 = time.time()
        self.pin = pin
        self._reset_pin_guards()
        self.vault_pin = ""
        self.vlock = _Lock(self)
        self.vault = {}            # slot -> {label, type, secret}
        self.pcs = {}              # slot -> {name, os, password}
        self.macros = {}           # slot -> {name, steps:[...]}
        self.ui = ""
        self.firmware = firmware
        self.host_manual = None
        self.host_detected = detected or host_os
        self.unicode = {"mac": False, "linux": False}
        self.power_mode, self.ap_auto_off = "balanced", False
        # v3.10: a board that was never given a password makes its own at first start.
        self.ap_ssid, self.ap_pass, self.ap_open = "ESP32-HID-DA1A2C", "k7m2-x9qp-4tre", False
        self.ap_genpw = self.ap_pass       # the password this board made (apGenPassword)
        self.peer = None                   # who is asking: an IP, or None for USB serial
        self.outbox = []                   # every text frame sent to a page
        self.dropped_unsigned = 0
        self.auth_deadline = 20.0          # seconds a socket has to sign in (WS_AUTH_DEADLINE_MS)
        self.pointer_mode, self.gamepad = "relative", False
        self.pc_state = "unknown"
        self.wiggle = None         # {"mode":..., "interval_ms":..., "since":...}
        self.verbose = False
        self.ap_clients = [{"ip": "192.168.4.2", "mac": "AA:BB:CC:00:11:22", "rssi": -40,
                            "quality": "excellent", "dashboard": True}]
        self.host_asleep_unarmed = False
        self.phone_on_lan = False
        self.refusing = False              # new sockets are turned away
        self.refused = 0                   # ...and how many have been
        self.fail_next = {}                # cmd -> error message, used once
        self.actions_done = []             # (action, keys) the agent commands pressed, in order
        self.refuse_after_ap = False       # ...from the moment the AP restarts
        self.refuse_after_reboot = False   # ...from the moment it reboots          # the dashboard reached over the home network, not the AP   # host suspended the bus and did not arm USB wake
        self.sta = {"phase": "idle", "ssid": "", "ip": "", "error": "", "ip_mode": "dhcp", "since": 0.0, "want_ip": ""}
        self.networks = [{"ssid": "HomeNet", "rssi": -48, "quality": "excellent", "channel": 6, "encryption": "WPA2"},
                         {"ssid": "Cafe Guest", "rssi": -71, "quality": "fair", "channel": 11, "encryption": "Open"}]
        self.join_succeeds = {"HomeNet"}
        self.scan_calls = 0
        self.log = []              # (transport, decoded command or frame)
        self.sockets = []
        self.cmd_count = 0
        self.boots = 0
        self.http_status_calls = 0

    # ---- time ------------------------------------------------------------------------
    def now(self):
        return time.time() + self._offset

    def advance(self, seconds):
        """Fast-forward the board's clock - lockouts and join timeouts expire."""
        self._offset += seconds

    # ---- what the dashboard sent -------------------------------------------------------
    def received(self, cmd=None):
        with self.lock:
            return [c for t, c in self.log if isinstance(c, dict) and (cmd is None or c.get("cmd") == cmd)]

    def frames(self, op=None):
        with self.lock:
            return [c for t, c in self.log if isinstance(c, tuple) and (op is None or c[0] == op)]

    def clear_log(self):
        with self.lock:
            self.log.clear()

    # "Generated" is a fact about the password in use, not a flag: it is true while
    # that password is the one the board made (apPassGenerated in the firmware).
    @property
    def ap_gen(self):
        return len(self.ap_genpw) >= 8 and self.ap_pass == self.ap_genpw

    @ap_gen.setter
    def ap_gen(self, made_it):
        self.ap_genpw = self.ap_pass if made_it else ""

    # ---- status ------------------------------------------------------------------------
    def host_os(self):
        return self.host_manual or self.host_detected

    def status(self):
        up = int(self.now() - self.t0)
        online = self.sta["phase"] == "online"
        return {
            "status": "ok", "reply": "status", "chip": "ESP32-S3", "firmware": self.firmware, "uptime_sec": up,
            "free_heap": 225000, "heap_floor": 220000, "heap_low": False, "total_psram": 8388608,
            "free_psram": 8371508, "flash_size": 16777216, "hid_ready": True, "usb_mounted": True,
            "usb_suspended": False, "power_mode": self.power_mode, "radio_awake": self.power_mode != "saver",
            "ap_auto_off": self.ap_auto_off, "ap_running": True, "lan_exposed": online and not self.pin,
            "sta_phase": self.sta["phase"], "sta_error": self.sta["error"], "sta_ip_mode": self.sta["ip_mode"],
            "sta_target": self.sta["ssid"], "cmd_count": self.cmd_count, "wifi_cmd_count": self.cmd_count,
            "wifi_connected": online, "wifi_ip": self.sta["ip"], "wifi_ssid": self.sta["ssid"] if online else "",
            "wifi_rssi": -48 if online else 0, "wifi_quality": "excellent" if online else "",
            "ap_ssid": self.ap_ssid, "ap_ip": "192.168.4.1", "ap_clients": 1, "channel": 6,
            "mac": "B8:1F:3F:DA:1A:2C", "ap_mac": "BA:1F:3F:DA:1A:2C", "ws_clients": len(self.sockets),
            "mdns": "esp-hid-da1a2c", "pc_state": self.pc_state, "host_os": self.host_os(),
            "host_os_source": "manual" if self.host_manual else "auto", "host_os_detected": self.host_detected,
            "detect_phase": "settled", "unicode_mac": self.unicode["mac"], "unicode_linux": self.unicode["linux"],
            "usb_set_idle": 1, "usb_ctrl_reqs": 2, "usb_led_reports": 0, "usb_str_reqs": 10, "usb_str_rereads": 5,
            "usb_str_seq": "0,0,0,0,0,0,2,2,1,1,3,3,4,4,0,0", "pointer_mode": self.pointer_mode,
            "gamepad": self.gamepad, "auth_set": bool(self.pin), "verbose": self.verbose,
            "ap_open": self.ap_open, "ap_pass_generated": self.ap_gen and not self.ap_open,
            "ap_pass_default": self.ap_pass == "hid12345",
            "wiggle_active": self.wiggle is not None, "wiggle_mode": self.wiggle["mode"] if self.wiggle else "idle",
            "led_on": False, "boot_button": "released",
        }

    # ---- Playwright wiring --------------------------------------------------------------
    def attach(self, page, html, ip=None):
        # Both ways a phone reaches a real board: a name on the home network, and
        # the board's own access point, where the page knows it is at 192.168.4.1.
        # `ip` is this phone's address as the board sees it; by default an AP
        # client, or a home-network one when phone_on_lan is set.
        where = lambda: ip or (LAN_IP if self.phone_on_lan else AP_IP)
        for base, ws in ((URL, WS_URL), (AP_URL, AP_WS_URL)):
            page.route(base + "**", lambda route, req: self._http(route, req, html, where()))
            page.route_web_socket(ws, lambda w: self._ws(w, where()))

    def _http(self, route, req, html, ip=AP_IP):
        self.peer = ip
        from urllib.parse import urlsplit
        path = urlsplit(req.url).path or "/"
        if path in ("/", ""):
            return route.fulfill(body=html, content_type="text/html")
        if path.startswith("/api/status"):
            self.http_status_calls += 1
            return route.fulfill(json=self.status())
        if path.startswith("/api/ping"):
            return route.fulfill(json={"status": "ok", "reply": "pong"})
        if path.startswith("/api/scan"):
            # scanAllowed(): from the AP always; over the LAN only with the PIN, or
            # when the board is on no network at all.
            if self.phone_on_lan:
                ok = (self._pin_check(req.headers.get("x-auth", "")) and not self.pin_locked()) if self.pin \
                    else self.sta["phase"] != "online"
                if not ok:
                    return route.fulfill(status=403, json={"status": "error", "reply": "lan_locked",
                                         "message": "Set an access PIN before scanning over your home network."})
            return route.fulfill(json=self._scan())
        if path.startswith("/api/command"):
            try:
                body = json.loads(req.post_data or "{}")
            except json.JSONDecodeError:
                return route.fulfill(json={"status": "error", "message": "Invalid JSON"})
            with self.lock:
                self.log.append(("http", body))
            if self.pin and body.get("cmd") not in ("auth", "ping"):
                tok = body.get("token", "")
                ok = bool(tok) and not self.pin_locked() and self._pin_check(tok)
                if not ok:
                    r = {"status": "error", "reply": "auth_required", "message": "Access PIN required"}
                    if self.pin_locked():
                        r["retry_in"] = self.pin_remaining()
                    return route.fulfill(json=r)
            replies = self.handle(body, "http", ip=ip)
            return route.fulfill(json=replies[0] if replies else {})
        return route.fulfill(status=404, json={"status": "error", "message": "Not found. Dashboard at / or API at /api/command"})

    def _ws(self, ws, ip=AP_IP):
        if self.refusing:                  # e.g. the phone has not rejoined the new AP yet
            # A real network failure: hand the socket to the actual host, which
            # does not exist (hidfi.test), so the browser fails the connection
            # itself and fires onerror, as it does when a board is out of reach.
            self.refused += 1
            try:
                ws.connect_to_server()
            except Exception:
                pass
            return
        # Everything the board says to a page is kept, so a test can prove what
        # never travels over WiFi (the board's WiFi password, a stored secret).
        _send = ws.send

        def recorded(m):
            self.outbox.append(m if isinstance(m, str) else "")
            return _send(m)
        try:
            ws.send = recorded
        except Exception:
            pass
        sock = {"ws": ws, "authed": not self.pin, "open": True, "ip": ip, "opened": self.now()}
        with self.lock:
            self.sockets.append(sock)
        if self.pin:
            ws.send(json.dumps({"status": "error", "reply": "auth_required"}))
            # dropUnsignedSockets(): not signed in by the deadline, the board closes it
            def drop_if_unsigned():
                if sock["open"] and not sock["authed"] and self.pin:
                    sock["open"] = False
                    self.dropped_unsigned += 1
                    with self.lock:
                        if sock in self.sockets:
                            self.sockets.remove(sock)
                    try:
                        ws.close(code=1000, reason="did not sign in")
                    except Exception:
                        pass
            t = threading.Timer(self.auth_deadline, drop_if_unsigned)
            t.daemon = True
            t.start()

        def on_message(msg):
            if not sock["open"]:
                return
            if isinstance(msg, (bytes, bytearray)):
                return self._binary(sock, bytes(msg))
            try:
                body = json.loads(msg)
            except json.JSONDecodeError:
                return ws.send(json.dumps({"status": "error", "message": "Invalid JSON"}))
            with self.lock:
                self.log.append(("ws", body))
                self.peer = ip
            if self.pin and not sock["authed"] and body.get("cmd") not in ("auth", "ping"):
                r = {"status": "error", "reply": "auth_required", "message": "Access PIN required"}
                if self.pin_locked():
                    r["retry_in"] = self.pin_remaining()
                return ws.send(json.dumps(r))
            for r in self.handle(body, "ws", sock, ip=ip):
                ws.send(json.dumps(r))
            self._after(body)

        ws.on_message(on_message)

        def on_close(*_):
            sock["open"] = False
        try:
            ws.on_close(on_close)
        except Exception:
            pass

    def _binary(self, sock, data):
        op = data[0]
        if op == 4 and len(data) >= 5:                    # PING is answered even before auth
            return sock["ws"].send(bytes([0x81]) + data[1:5])
        if not sock["authed"]:
            return
        if self._lan_locked():                         # wsFromLan && no PIN && on a network
            return
        with self.lock:
            if op == 1 and len(data) >= 7:
                dx, dy, wh, pan = struct.unpack("<hhbb", data[1:7])
                self.log.append(("ws", ("move", dx, dy, wh, pan)))
            elif op == 2 and len(data) >= 4:
                self.log.append(("ws", ("button", data[1], data[2], data[3])))
            elif op == 3 and len(data) >= 5:
                self.log.append(("ws", ("abs",) + struct.unpack("<HH", data[1:5])))
            elif op == 5 and len(data) >= 10:
                self.log.append(("ws", ("gamepad",) + tuple(data[1:10])))
            elif op == 6 and len(data) >= 3:                 # [op][u16 usage] - firmware: len >= 3
                self.log.append(("ws", ("consumer", struct.unpack("<H", data[1:3])[0])))
            elif op == 7 and len(data) >= 3:
                self.log.append(("ws", ("key", bool(data[1]), data[2:].decode("utf-8", "replace"))))
            else:
                self.log.append(("ws", ("unknown", op, data.hex())))

    def _after(self, body):
        """Side effects the firmware performs after replying: reboots, AP restart."""
        c = body.get("cmd")
        if c in ("pointer_mode", "gamepad_enable") and getattr(self, "_reboot_pending", False):
            self._reboot_pending = False
            threading.Timer(0.6, self.reboot).start()
        if c == "ap_configure" and getattr(self, "_ap_restart_pending", False):
            self._ap_restart_pending = False
            if self.refuse_after_ap:
                self.refusing = True
            threading.Timer(0.3, self.drop_all).start()

    def drop_all(self):
        with self.lock:
            socks, self.sockets = list(self.sockets), []
        for s in socks:
            s["open"] = False
            try:
                s["ws"].close(code=1006, reason="board restarted")   # code given: Playwright needs it
            except Exception:
                try:
                    s["ws"].close(code=1000, reason="board restarted")
                except Exception:
                    pass

    def reboot(self):
        if self.refuse_after_reboot:   # stuck in the bootloader, or a bad cable
            self.refusing = True
        self.boots += 1
        self.t0 = self.now()
        self._reset_pin_guards()
        self.wiggle = None
        self.drop_all()

    # ---- the command surface ----------------------------------------------------------------
    # Wrong-PIN lockout per device (the firmware's PinGuard table), plus a budget
    # across the whole home network. The board's own AP is never under the budget.
    def _reset_pin_guards(self):
        self.guards, self.lan_fails, self.lan_window, self.lan_lock_until = {}, 0, 0.0, 0.0

    def _guard(self, ip):
        return self.guards.setdefault(ip or "", _Lock(self))

    @staticmethod
    def _on_ap(ip):
        return bool(ip) and ip.startswith("192.168.4.")

    def pin_locked(self, ip=None):
        ip = self.peer if ip is None else ip
        lan = not self._on_ap(ip) and self.lan_lock_until > self.now()
        return lan or self._guard(ip).locked()

    def pin_remaining(self, ip=None):
        ip = self.peer if ip is None else ip
        r = int(self.lan_lock_until - self.now()) + 1 if (not self._on_ap(ip) and self.lan_lock_until > self.now()) else 0
        return max(r, self._guard(ip).remaining())

    def _pin_check(self, tok):
        g = self._guard(self.peer)
        if self.pin and tok == self.pin:
            g.ok()
            return True
        g.fail()
        if not self._on_ap(self.peer):
            if self.now() - self.lan_window > LAN_FAIL_WINDOW:
                self.lan_window, self.lan_fails = self.now(), 0
            self.lan_fails += 1
            if self.lan_fails >= LAN_FAIL_BUDGET:
                self.lan_lock_until = self.now() + LAN_LOCK
        return False

    def _vault_gate(self):
        return self.vault_pin or self.pin

    def _vault_open(self, body):
        if not self._vault_gate():
            return {"status": "error", "reply": "vault_locked",
                    "message": "Set an access PIN first. Without one the vault would guard nothing."}
        if self.vlock.locked():
            return {"status": "error", "reply": "vault_locked", "retry_in": self.vlock.remaining(), "message": "Too many wrong PINs"}
        if body.get("pin", "") == self._vault_gate():
            self.vlock.ok()
            return None
        self.vlock.fail()
        r = {"status": "error", "reply": "vault_denied", "message": "PIN required"}
        if self.vlock.locked():
            r["retry_in"] = self.vlock.remaining()
        return r

    def _scan(self):
        self.scan_calls += 1
        if self.sta["phase"] == "connecting":
            return {"status": "ok", "reply": "scan", "scanning": False, "busy": True,
                    "message": "Still joining a network. Scanning will work once that finishes."}
        if self.scan_calls == 1:
            return {"status": "ok", "reply": "scan", "scanning": True}
        return {"status": "ok", "reply": "scan", "scanning": False, "count": len(self.networks), "networks": self.networks}

    def _tick_sta(self):
        s = self.sta
        if s["phase"] == "connecting" and self.now() - s["since"] > 1.5:
            if s["ssid"] in self.join_succeeds:
                s["phase"] = "online"
                s["ip"] = s["want_ip"] or "192.168.1.77"
            else:
                s["phase"] = "failed"
                s["error"] = "Wrong password, or the network refused the board"

    def handle(self, b, transport="serial", sock=None, ip=None):
        with self.lock:
            self.peer = ip if transport != "serial" else None
            self.cmd_count += 1
            self._tick_sta()
            r = self._dispatch(b, transport, sock)
        return [] if r is None else [r]

    LAN_SAFE = ("status", "ping", "wifi_status", "set_auth", "auth", "ui_get")

    def _lan_locked(self):
        """processCommand(): a phone reaching the board over the home network, with no
        PIN set, is refused everything but reading and setting a PIN. Since v3.9 the
        socket's binary frames (keys, pointer, buttons, media) are dropped the same
        way (wsFromLan in onWsEvent); before, they went straight through."""
        return self.phone_on_lan and not self.pin and self.sta["phase"] == "online"

    # ---- actions for agents (handleDo / handleActions / handleOpen / handleSteps) ---------
    def _os_of(self, b):
        o = b.get("os", "")
        return o if o in ACTION_COL else self.host_os()

    @staticmethod
    def _keys(name, os_):
        row = ACTIONS.get(name)
        return None if row is None else row[ACTION_COL.get(os_, 2)]   # unknown host: Windows keys

    def _agent(self, c, b, OK, E):
        os_ = self._os_of(b)
        osname = os_ if os_ in ACTION_COL else "unknown"
        if c == "actions":
            col = ACTION_COL.get(os_, 2)
            if b.get("keys"):
                a = {n: r[col] for n, r in ACTIONS.items() if r[col]}
            else:
                a = " ".join(n for n, r in ACTIONS.items() if r[col])
            return OK("actions", os=osname, a=a, also=ALSO)
        if c == "do":
            name = b.get("a") or b.get("action") or ""
            k = self._keys(name, os_)
            if k is None:
                return E("Not an action. {\"cmd\":\"actions\"} lists them.", reply="unknown_action")
            if not k:
                return E("No equivalent on this computer's OS", reply="no_equivalent", os=osname)
            self.actions_done.append((name, k))
            return OK("done", a=name, keys=k, os=osname)
        if c == "open":
            if b.get("app"):
                self.actions_done.append(("open", b["app"]))
                return OK("opened", via={"mac": "spotlight", "linux": "overview"}.get(os_, "start"), os=osname)
            if b.get("run"):
                if os_ == "mac":
                    return E("macOS has no run box - use app", reply="no_equivalent", os=osname)
                self.actions_done.append(("run", b["run"]))
                return OK("opened", via="run", os=osname)
            return dict(E("Give app (a name, as in the OS's search) or run (a command or URL)"), os=osname)
        # steps
        st = b.get("s")
        if not isinstance(st, list) or not 1 <= len(st) <= 32:
            return E("s is an array of 1 to 32 steps")
        waited = 0
        for i, ln in enumerate(st):
            verb, _, arg = str(ln).partition(" ")
            bad = None
            if verb == "do":
                k = self._keys(arg, os_)
                bad = "unknown action" if k is None else ("no equivalent on this OS" if not k else None)
            elif verb == "open":
                bad = None if arg else "open needs an app name"
            elif verb == "run":
                bad = None if (arg and os_ != "mac") else "run needs a line, and not on macOS"
            elif verb == "key":
                bad = None if arg else "key needs a combo"
            elif verb in ("type", "click"):
                pass
            elif verb == "wait":
                try:
                    ms = max(0, min(5000, int(arg)))
                except ValueError:
                    ms = 0
                if waited + ms > 10000:
                    bad = "more than 10 s of waiting"
                waited += ms
            else:
                bad = "unknown step"
            if bad:
                return E(bad, reply="steps_stopped", at=i, done=i)
            self.actions_done.append(("step", ln))
        return OK("steps_done", done=len(st), os=osname)

    def _dispatch(self, b, transport, sock):
        c = b.get("cmd")
        E = lambda msg, **kw: dict({"status": "error", "message": msg}, **kw)
        OK = lambda reply, **kw: dict({"status": "ok", "reply": reply}, **kw)
        if c in self.fail_next:            # a scenario asked for this one to be refused
            return E(self.fail_next.pop(c))
        if transport in ("ws", "http") and c not in self.LAN_SAFE and self._lan_locked():
            return {"status": "error", "reply": "lan_locked",
                    "message": "Set an access PIN before using this board over your home network. "
                               "Every device on that network can reach it, not just people in radio range."}

        if c == "status":
            return self.status()
        if c == "ping":
            return OK("pong", uptime=int(self.now() - self.t0), hid_ready=True, wifi_connected=self.sta["phase"] == "online",
                      cmd_count=self.cmd_count, pc_state=self.pc_state, wiggle_active=self.wiggle is not None)
        if c == "echo":
            d = b.get("data", "")
            return OK("echo", data=d, length=len(d.encode()))
        if c == "auth":
            if not self.pin:
                return OK("auth_ok", message="No PIN configured")
            if transport != "serial" and self.pin_locked():
                return E("Too many wrong PINs", reply="auth_locked", retry_in=self.pin_remaining())
            if self._pin_check(b.get("token", "")):
                if sock:
                    sock["authed"] = True
                return OK("auth_ok")
            r = E("Wrong PIN", reply="auth_failed")
            if self.pin_locked():
                r["retry_in"] = self.pin_remaining()
            return r
        if c == "set_auth":
            tk = b.get("token", "")
            if 0 < len(tk) < 4:
                return E("PIN must be at least 4 characters (or empty to disable)")
            if transport != "serial" and not self.pin and tk:
                given = b.get("ap_password", "")
                ok = (given == self.ap_pass) if not self.ap_open else self._on_ap(self.peer)
                if not ok:
                    msg = (("That is not this board's WiFi password." if given else
                            "Enter this board's WiFi password to set the first PIN.") if not self.ap_open else
                           "This board's WiFi is open. Connect to it to set the first PIN.")
                    return E(msg, reply="ap_password_required")
            wipe = not self.vault_pin and not (self.pin and b.get("old") == self.pin) and bool(self.vault)
            if wipe:
                self.vault.clear()
            self.pin = tk
            for s in self.sockets:
                s["authed"] = not tk
            if tk and sock:
                sock["authed"] = True
            return OK("auth_set", auth_set=bool(tk), vault_wiped=wipe)

        # --- keyboard -------------------------------------------------------------------
        if c == "type":
            t = b.get("text", "")
            if not t:
                return E("No text provided")
            from support.corpus import expect
            off, osk, _on, nsk = expect(t.encode())
            uni = self.unicode.get(self.host_os(), False)
            return OK("typed", length=len(t.encode()), skipped=nsk if uni else osk, enter=bool(b.get("enter")))
        if c == "press":
            k = b.get("keys", "")
            return OK("pressed", keys=k) if k else E("No keys specified")
        if c in ("do", "actions", "open", "steps"):
            return self._agent(c, b, OK, E)
        if c in ("key_down", "key_up"):
            k = b.get("key", "")
            if not k or (len(k) > 1 and not re.fullmatch(r"[A-Za-z0-9_]+", k)):
                return E("Unknown key", key=k)
            return OK(c, key=k)
        if c == "key_release_all":
            return OK("key_release_all")
        if c == "test_hid":
            return OK("test_hid", typed="HID OK")
        if c == "set_unicode":
            os_ = b.get("os", self.host_os())
            if os_ in ("mac", "macos", "linux"):
                self.unicode["mac" if os_.startswith("mac") else "linux"] = bool(b.get("on"))
                return OK("unicode_set", unicode_mac=self.unicode["mac"], unicode_linux=self.unicode["linux"])
            return E("Unicode typing works on mac or linux only")
        if c == "unicode_test":
            os_ = b.get("os", self.host_os())
            if os_ not in ("mac", "macos", "linux"):
                return E("Unicode typing works on mac or linux only")
            if (os_.startswith("mac") and self.host_detected in ("windows", "linux")) or (os_ == "linux" and self.host_detected == "mac"):
                return E("This computer looks like a PC - the macOS test would press Alt shortcuts here, so it was not sent"
                         if os_.startswith("mac") else "This computer looks like a Mac - the Linux test was not sent")
            return OK("unicode_test_sent", os="mac" if os_.startswith("mac") else "linux", sample="é\U0001F600")

        # --- pointer --------------------------------------------------------------------
        if c == "mouse_move":
            return OK("mouse_moved", dx=b.get("dx", 0), dy=b.get("dy", 0), wheel=b.get("wheel", 0), pan=b.get("pan", 0))
        if c == "mouse_click":
            return OK("mouse_clicked", button=b.get("button", "left"), count=b.get("count", 1), modifiers=b.get("modifiers", ""))
        if c in ("mouse_press", "mouse_release"):
            return OK({"mouse_press": "mouse_pressed", "mouse_release": "mouse_released"}[c], button=b.get("button", "left"))
        if c == "mouse_scroll":
            return OK("mouse_scrolled", amount=b.get("amount", 0), pan=b.get("pan", 0))
        if c == "mouse_abs":
            if self.pointer_mode != "absolute":
                return E("Pointer is in relative mode - send pointer_mode absolute first (reboots)")
            return OK("mouse_abs", x=b.get("x"), y=b.get("y"))
        if c == "pointer_mode":
            m = str(b.get("mode", "")).lower()
            if m not in ("relative", "absolute"):
                return E("Invalid mode (relative|absolute)")
            changed = m != self.pointer_mode
            self.pointer_mode = m
            reboot = b.get("reboot", True) and changed
            self._reboot_pending = reboot
            r = OK("pointer_mode", mode=m, changed=changed, rebooting=reboot)
            if changed and not reboot:
                r["note"] = "Saved - takes effect on next boot"
            return r
        if c == "wiggle":
            mode = b.get("mode", "single")
            if mode in ("interval", "timed") and self.wiggle and not b.get("force"):
                return E("A wiggle is already running — send wiggle_stop or pass force:true", reply="busy", wiggle_mode=self.wiggle["mode"])
            amp = max(1, min(100, int(b.get("amplitude", 5))))
            if mode == "single":
                return OK("wiggle_done", mode="single", amplitude=amp)
            if mode in ("interval", "timed"):
                self.wiggle = {"mode": mode, "interval_ms": max(50, int(b.get("interval_ms", 1000))), "since": self.now()}
                return OK("wiggle_started", mode=mode, amplitude=amp, interval_ms=self.wiggle["interval_ms"],
                          duration_s=b.get("duration_s", 0))
            return E("Invalid wiggle mode (use single|interval|timed)")
        if c == "wiggle_stop":
            if not self.wiggle:
                return OK("not_running", message="No wiggle is currently running")
            w, self.wiggle = self.wiggle, None
            return OK("wiggle_stopped", stopped_mode=w["mode"], count=1, elapsed_sec=int(self.now() - w["since"]))

        # --- media, system, gestures, presenter, gamepad -----------------------------------
        if c == "media":
            k = b.get("key", "")
            if k not in MEDIA and not b.get("usage"):
                return E(MEDIA_MSG)
            return OK("media_sent", key=k, usage=b.get("usage", 0))
        if c == "system":
            a = b.get("action", "")
            if a not in ("sleep", "standby", "wake", "power_off"):
                return E("Invalid action (sleep|wake|power_off)")
            r = OK("system_sent", action=a)
            if a == "wake":
                r.update(was_suspended=self.host_asleep_unarmed, remote_wakeup=not self.host_asleep_unarmed)
                if self.host_asleep_unarmed:
                    r["message"] = ("The host has not armed this device as a wake source. Enable 'Allow this device "
                                    "to wake the computer' on the HID keyboard in Device Manager, and allow USB wake in the BIOS.")
            return r
        if c == "gesture":
            n = b.get("name", "")
            if n not in GESTURES and not b.get("keys"):
                return E("Unknown gesture (" + "|".join(GESTURES) + ") or pass keys")
            return OK("gesture_done", name=n, keys=b.get("keys", ""))
        if c == "presenter":
            a = b.get("action", "")
            if a not in ("next", "prev", "start", "end", "black", "white"):
                return E("Invalid action (next|prev|start|end|black|white)")
            return OK("presenter_done", action=a)
        if c == "gamepad_enable":
            want = bool(b.get("on"))
            changed = want != self.gamepad
            self.gamepad = want
            reboot = b.get("reboot", True) and changed
            self._reboot_pending = reboot
            r = OK("gamepad_enable", on=want, changed=changed, rebooting=reboot)
            if changed and not reboot:
                r["note"] = "Saved - takes effect on next boot"
            return r
        if c == "gamepad":
            if not self.gamepad:
                return E("Gamepad disabled - send gamepad_enable {on:true} (reboots)")
            return OK("gamepad_sent")

        # --- session --------------------------------------------------------------------
        if c == "lock":
            if not b.get("force") and self.pc_state == "locked":
                return OK("already_locked", pc_state="locked", message="PC is already in locked state")
            self.pc_state = "locked"
            return OK("locked", pc_state="locked", combo="CTRL+GUI+Q" if self.host_os() == "mac" else "GUI+L")
        if c == "unlock":
            if b.get("profile") is not None:
                p = self.pcs.get(int(b["profile"]))
                if not p:
                    return E("No saved PC in that slot", pc_state=self.pc_state)
            elif not b.get("password"):
                return E("Password is required", pc_state=self.pc_state)
            if not b.get("force") and self.pc_state == "unlocked":
                return OK("already_unlocked", pc_state="unlocked", message="PC is already in unlocked state")
            self.pc_state = "unlocked"
            return OK("unlock_done", pc_state="unlocked")
        if c == "set_lock_state":
            s = b.get("state", "")
            if s not in ("locked", "unlocked", "unknown"):
                return E("Invalid state. Use: locked, unlocked, or unknown", pc_state=self.pc_state)
            self.pc_state = s
            return OK("state_set", pc_state=s)
        if c == "pc_list":
            return OK("pc_list", profiles=[{"slot": k, "name": v["name"], "os": v["os"]} for k, v in sorted(self.pcs.items())],
                      auth_set=bool(self.pin), slots=PC_SLOTS)
        if c == "pc_save":
            if not self.pin:
                return E("Set an access PIN first. Without one, anybody in radio range could unlock this PC with a saved password.")
            name, pw, slot = (b.get("name") or "").strip(), b.get("password", ""), b.get("slot", -1)
            occupied = slot in self.pcs
            if not name or (not pw and not occupied):
                return E("name and password are both required")
            if not (0 <= slot < PC_SLOTS):
                slot = next((i for i in range(PC_SLOTS) if i not in self.pcs), -1)
            if slot < 0:
                return E("All slots are in use")
            os_ = (b.get("os") or "").lower()
            if os_ not in ("mac", "windows", "linux", "auto"):
                os_ = self.pcs[slot]["os"] if occupied else self.host_os()
            self.pcs[slot] = {"name": name, "os": os_, "password": pw or self.pcs.get(slot, {}).get("password", "")}
            return OK("pc_saved", slot=slot, name=name, os=os_)
        if c == "pc_delete":
            s = b.get("slot", -1)
            if not (0 <= s < PC_SLOTS):
                return E("Invalid slot")
            self.pcs.pop(s, None)
            return OK("pc_deleted", slot=s)

        # --- vault ----------------------------------------------------------------------
        if c == "vault_list":
            r = OK("vault_list", entries=[{"slot": k, "label": v["label"], "type": v["type"]} for k, v in sorted(self.vault.items())],
                   slots=VAULT_SLOTS, auth_set=bool(self.pin), vault_pin_set=bool(self.vault_pin), locked=self.vlock.locked())
            if self.vlock.locked():
                r["retry_in"] = self.vlock.remaining()
            return r
        if c == "vault_save":
            if not self.pin:
                return E("Set an access PIN first. Without one, anybody in radio range could have these typed out.", reply="vault_locked")
            label, secret, slot = (b.get("label") or "").strip()[:32], b.get("secret", ""), b.get("slot", -1)
            occupied = slot in self.vault
            if not label or (not secret and not occupied):
                return E("label and secret are both required")
            if not (0 <= slot < VAULT_SLOTS):
                slot = next((i for i in range(VAULT_SLOTS) if i not in self.vault), -1)
            if slot < 0:
                return E("All vault slots are in use")
            kind = (b.get("type") or (self.vault[slot]["type"] if occupied else "password")).lower()
            kind = kind if kind in ("pin", "password") else "password"
            if kind == "pin" and secret and not secret.isdigit():
                return E("A PIN can only contain digits")
            self.vault[slot] = {"label": label, "type": kind, "secret": secret or self.vault[slot]["secret"]}
            return OK("vault_saved", slot=slot, label=label, type=kind)
        if c == "vault_get":
            deny = self._vault_open(b)
            if deny:
                return deny
            s = b.get("slot", -1)
            if not (0 <= s < VAULT_SLOTS):
                return E("Invalid slot")
            if s not in self.vault:
                return E("Empty slot")
            v = self.vault[s]
            return OK("vault_secret", slot=s, label=v["label"], type=v["type"], secret=v["secret"])
        if c == "vault_type":
            deny = self._vault_open(b)
            if deny:
                return deny
            s = b.get("slot", -1)
            if not (0 <= s < VAULT_SLOTS):
                return E("Invalid slot")
            if s not in self.vault:
                return E("Empty slot")
            return OK("vault_typed", slot=s, label=self.vault[s]["label"])
        if c == "vault_delete":
            s = b.get("slot", -1)
            if not (0 <= s < VAULT_SLOTS):
                return E("Invalid slot")
            self.vault.pop(s, None)
            return OK("vault_deleted", slot=s)
        if c == "vault_wipe":
            n = len(self.vault)
            self.vault.clear()
            return OK("vault_wiped", destroyed=n)
        if c == "vault_pin":
            if not self.pin:
                return E("Set an access PIN first.", reply="vault_locked")
            if self.vlock.locked():
                return E("Too many wrong PINs", reply="vault_locked", retry_in=self.vlock.remaining())
            nxt = b.get("new_pin", "")
            if nxt and (len(nxt) != 4 or not nxt.isdigit()):
                return E("The vault PIN is four digits (or empty to go back to the access PIN)")
            by_vault, by_access = b.get("pin", ""), b.get("access", "")
            have = bool(self.vault_pin)
            knows_vault = have and by_vault and by_vault == self.vault_pin
            knows_access = by_access and by_access == self.pin
            if not knows_vault and not knows_access:
                self.vlock.fail()
                r = E("Wrong PIN" if have else "Wrong access PIN", reply="vault_denied")
                if self.vlock.locked():
                    r["retry_in"] = self.vlock.remaining()
                return r
            self.vlock.ok()
            wipe = have and not knows_vault
            if wipe:
                self.vault.clear()
            self.vault_pin = nxt
            return OK("vault_pin_set", vault_pin_set=bool(nxt), vault_wiped=wipe)

        # --- macros and dashboard layout -------------------------------------------------------
        if c == "macro_list":
            return OK("macro_list", macros=[{"slot": k, "name": v["name"]} for k, v in sorted(self.macros.items())])
        if c in ("macro_save", "macro_get", "macro_run", "macro_delete"):
            s = b.get("slot", -1)
            if not isinstance(s, int) or not (0 <= s < MACRO_SLOTS):
                return E("slot must be 0-7")
            if c == "macro_save":
                steps = b.get("steps") or []
                if not steps:
                    return E("steps[] is required")
                if len("\n".join(steps)) > 1000:
                    return E("macro too long (max 1000 chars)")
                self.macros[s] = {"name": b.get("name", "macro"), "steps": list(steps)}
                return OK("macro_saved", slot=s, steps=len(steps))
            if c == "macro_get":
                m = self.macros.get(s, {"name": "", "steps": []})
                return OK("macro", slot=s, name=m["name"], steps=m["steps"])
            if c == "macro_run":
                if s not in self.macros:
                    return E("slot is empty")
                return OK("macro_done", slot=s, name=self.macros[s]["name"], steps=len(self.macros[s]["steps"]))
            self.macros.pop(s, None)
            return OK("macro_deleted", slot=s)
        if c == "ui_get":
            return OK("ui", data=self.ui)
        if c == "ui_save":
            d = b.get("data", "")
            if len(d) > 4096:
                return E("layout too large", max=4096)
            self.ui = d
            return OK("ui_saved", bytes=len(d))

        # --- network, power, settings ------------------------------------------------------
        if c == "wifi_status":
            s = self.sta
            # Like handleWifiStatus(): the link details exist only while connected.
            link = dict(sta_ssid=s["ssid"], sta_rssi=-52, sta_channel=6, sta_gateway="192.168.1.1",
                        sta_subnet="255.255.255.0", sta_dns="192.168.1.1") if s["phase"] == "online" else {}
            return OK("wifi_status", sta_connected=s["phase"] == "online", sta_enabled=True, sta_ip=s["ip"],
                      sta_phase=s["phase"], sta_error=s["error"], sta_ip_mode=s["ip_mode"], sta_target=s["ssid"],
                      want_ip=s["want_ip"], ap_ssid=self.ap_ssid, ap_ip="192.168.4.1", ap_clients=1,
                      mac="B8:1F:3F:DA:1A:2C", ap_mac="BA:1F:3F:DA:1A:2C", saved_ssid=s["ssid"], **link)
        if c == "wifi_configure":
            ssid = b.get("ssid", "")
            if not ssid:
                return E("SSID is required")
            ip, gw = b.get("ip", ""), b.get("gateway", "")
            if ip:
                if not re.fullmatch(r"(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})", ip) or any(int(x) > 255 for x in ip.split(".")):
                    return E("That is not a valid IPv4 address")
                if not gw:
                    return E("A static address needs a gateway, usually your router")
            self.sta.update(phase="connecting", ssid=ssid, ip="", error="", ip_mode="static" if ip else "dhcp",
                            since=self.now(), want_ip=ip)
            return OK("wifi_connecting", ssid=ssid, phase="connecting", ip_mode=self.sta["ip_mode"],
                      timeout_ms=15000, ap_still_active=True)
        if c == "wifi_disconnect":
            self.sta.update(phase="idle", ip="", error="")
            return OK("wifi_disconnected", ap_still_active=True)
        if c == "ap_configure":
            ssid, pw, open_ = b.get("ssid", ""), b.get("password", ""), bool(b.get("open"))
            n = len(ssid.encode())
            if n == 0:
                return E("AP SSID is required")
            if n > 32:
                return E("Network name is too long. The limit is 32 bytes, and an emoji uses four.", bytes=n, max_bytes=32)
            if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in ssid):
                return E("Network name must be valid text. Control characters are not allowed.")
            if 0 < len(pw.encode()) < 8:
                return E("AP password must be at least 8 characters. To remove it, send open:true.")
            if len(pw.encode()) > 63:
                return E("AP password can be at most 63 bytes")
            self.ap_ssid = ssid
            if open_:
                self.ap_pass, self.ap_open = "", True
            elif pw:
                self.ap_pass, self.ap_open = pw, False
            self._ap_restart_pending = True
            return None                                   # the firmware sends no reply here (see docs)
        if c == "power_mode":
            m = b.get("mode")
            if m is not None and m not in ("performance", "balanced", "saver"):
                return E("Unknown mode (performance|balanced|saver)")
            if m:
                self.power_mode = m
            if "ap_auto_off" in b:
                self.ap_auto_off = bool(b["ap_auto_off"])
            return OK("power_mode", mode=self.power_mode, ap_auto_off=self.ap_auto_off, ap_running=True,
                      radio_awake=self.power_mode != "saver", idle_after_ms=30000)
        if c == "set_host_os":
            os_ = str(b.get("os", "")).lower()
            table = {"auto": None, "mac": "mac", "macos": "mac", "windows": "windows", "win": "windows", "linux": "linux"}
            if os_ not in table:
                return E("Invalid os. Use: mac, windows, linux, or auto", host_os=self.host_os())
            self.host_manual = table[os_]
            return OK("host_os_set", host_os=self.host_os(), host_os_source="manual" if self.host_manual else "auto",
                      host_os_detected=self.host_detected)
        if c == "ap_password":
            if transport != "serial":
                return E("The WiFi password is only shown over the USB cable.", reply="usb_only")
            return OK("ap_password", ssid=self.ap_ssid, password="" if self.ap_open else self.ap_pass,
                      open=self.ap_open, generated=self.ap_gen)
        if c == "set_verbose":
            self.verbose = bool(b.get("on"))
            return OK("verbose_set", verbose=self.verbose)
        if c == "storage_info":
            return OK("storage_info",
                      nvs={"used_entries": 172, "free_entries": 458, "total_entries": 630, "namespaces": 7, "percent_used": 27},
                      hid_cfg={"pointer_mode": self.pointer_mode, "gamepad": self.gamepad, "pin_set": bool(self.pin),
                               "verbose": self.verbose, "power_mode": self.power_mode, "ap_auto_off": self.ap_auto_off,
                               "ui_bytes": len(self.ui)},
                      vault={"entries": len(self.vault), "slots": VAULT_SLOTS, "vault_pin_set": bool(self.vault_pin)},
                      wifi_cfg={"sta_ssid": self.sta["ssid"], "sta_pass_set": False, "ap_ssid": self.ap_ssid,
                                "ap_pass_set": bool(self.ap_pass), "ap_open": self.ap_open, "static_ip": "",
                                "static_gw": "", "static_mask": "", "static_dns": ""},
                      note="Macros are listed by macro_list, saved PCs by pc_list and vault entries by vault_list. "
                           "No password, secret or PIN is ever returned by any of them.")
        if c == "clients":
            return OK("clients", clients=list(self.ap_clients), count=len(self.ap_clients), attached=len(self.sockets),
                      note="Device names are not shown. A WiFi client is never required to tell the access point "
                           "its name, and most phones now send a random MAC as well.")
        if c in ("led_set", "led_blink", "led_status"):
            return OK({"led_set": "led_set", "led_blink": "led_blink_started", "led_status": "led_status"}[c],
                      on=False, r=0, g=0, b=0)
        return {"status": "error", "message": "Unknown command", "cmd": c}
