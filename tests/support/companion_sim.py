"""The board's companion endpoint and commands, modelled (hid_fi/companion.h).

BoardSim delegates to this for `POST /api/companion`, `companion_list`,
`companion_pair`, `companion_unpair` and `screen_ticket`, and replies exactly as
the firmware does - same `reply`, same keys, same messages. `FakeApp` plays the
companion app's side, so scenarios can pair a computer and bring it online.

The crypto is standard-library Python plus RFC 7748 X25519 written out, so the
suite needs no new dependency. It is checked against
companion/vectors/pairing_v1.json (tests/unit/test_companion_vectors.py), the
same vectors the Rust app and the firmware's mbedTLS code are held to.
"""
import hashlib
import hmac
import os
import re

PAIRED_MAX, PENDING_MAX, NAME_MAX, BODY_MAX = 4, 4, 32, 768
PENDING_S, ONLINE_S, CHAL_S, CODE_TRIES = 120.0, 60.0, 30.0, 3
RATE_WINDOW_S, RATE_MAX = 5.0, 25
CHALS = 4                      # live challenges per computer (hid_fi/companion.h COMP_CHALS)

# ---- crypto -----------------------------------------------------------------------------
_P = 2 ** 255 - 19


def x25519(k: bytes, u: bytes = (9).to_bytes(32, "little")) -> bytes:
    """RFC 7748 section 5, Montgomery ladder. Clamps the scalar, masks the top bit of u."""
    kb = bytearray(k)
    kb[0] &= 248; kb[31] &= 127; kb[31] |= 64
    k_int = int.from_bytes(kb, "little")
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in reversed(range(255)):
        kt = (k_int >> t) & 1
        swap ^= kt
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = kt
        a, b = (x2 + z2) % _P, (x2 - z2) % _P
        aa, bb = a * a % _P, b * b % _P
        e = (aa - bb) % _P
        c, d = (x3 + z3) % _P, (x3 - z3) % _P
        da, cb = d * a % _P, c * b % _P
        x3, z3 = (da + cb) ** 2 % _P, x1 * (da - cb) ** 2 % _P
        x2, z2 = aa * bb % _P, e * (aa + 121665 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def h(*parts: bytes) -> bytes:
    return hashlib.sha256(b"".join(parts)).digest()


def commit(a_pub, n_a):
    return h(b"hidfi-commit", a_pub, n_a)


def sas(a_pub, b_pub, n_a, n_b):
    return int.from_bytes(h(b"hidfi-sas", a_pub, b_pub, n_a, n_b)[:4], "big") % 1_000_000


def hkdf32(ikm, salt, info):
    prk = hmac.new(salt, ikm, "sha256").digest()
    return hmac.new(prk, info + b"\x01", "sha256").digest()


def derive_key(shared, a_pub, b_pub, n_a, n_b):
    return hkdf32(shared, h(a_pub, b_pub, n_a, n_b), b"hidfi-companion-v1")


def mac(key, msg: str) -> str:
    return hmac.new(key, msg.encode(), "sha256").hexdigest()


_HEX = re.compile(r"^[0-9a-f]*$")


def hex_ok(s, n):
    """Exactly 2n lowercase hex digits, as comp_hex_parse() demands."""
    return isinstance(s, str) and len(s) == 2 * n and bool(_HEX.match(s))


# ---- the board ----------------------------------------------------------------------------
class CompanionBoard:
    def __init__(self, sim):
        self.sim = sim
        self.pending = []          # dicts, at most PENDING_MAX, in slot order
        self.paired = []           # dicts, at most PAIRED_MAX
        self.rate = {}             # ip -> [window_at, count]
        self.random = os.urandom   # a test may make it deterministic

    # time and expiry, as compTick() and compOnline() see it
    def _now(self):
        return self.sim.now()

    def tick(self):
        now = self._now()
        self.pending = [p for p in self.pending if now - p["at"] <= PENDING_S]
        for c in self.paired:
            c["chals"] = [(ch, at) for ch, at in c.get("chals", []) if now - at <= CHAL_S]

    def online(self, c):
        return c.get("seen") is not None and self._now() - c["seen"] < ONLINE_S

    def counts(self):
        self.tick()
        return {"comp_paired": len(self.paired),
                "comp_online": sum(1 for c in self.paired if self.online(c)),
                "comp_pending": sum(1 for p in self.pending if p["phase"] != "aborted")}

    def wipe(self):
        self.paired.clear()
        self.pending.clear()

    def _find_pending(self, id_):
        return next((p for p in self.pending if p["id"] == id_), None)

    def _find_paired(self, id_):
        return next((c for c in self.paired if c["id"] == id_), None)

    # ---- POST /api/companion: returns (http status, reply) -------------------------------
    @staticmethod
    def _refuse(code, reply, message):
        r = {"status": "error", "reply": reply, "message": message}
        if reply == "rate_limited":
            r["retry_ms"] = int(RATE_WINDOW_S * 1000)
        return code, r

    def _state(self, id_):
        p = self._find_pending(id_)
        if p:
            r = {"status": "ok", "state": p["phase"]}
            if p["phase"] == "confirm":
                r["b_pub"], r["n_b"] = p["b_pub"].hex(), p["n_b"].hex()
            return 200, r
        return 200, {"status": "ok", "state": "paired" if self._find_paired(id_) else "none"}

    def http(self, body: str, ip: str):
        self.tick()
        now = self._now()
        w = self.rate.get(ip)
        if not w or now - w[0] > RATE_WINDOW_S:
            w = self.rate[ip] = [now, 0]
        w[1] += 1
        if w[1] > RATE_MAX:
            return self._refuse(429, "rate_limited", "Too many requests")
        if not self.sim.pin:
            return self._refuse(403, "pin_required", "Set an access PIN on the board before sharing a screen")
        if not body:
            return self._refuse(400, "bad_request", "No body")
        if len(body.encode()) > BODY_MAX:
            return self._refuse(413, "bad_request", "Body too large")
        import json
        try:
            b = json.loads(body)
            if not isinstance(b, dict):
                raise ValueError
        except ValueError:
            return self._refuse(400, "bad_request", "Invalid JSON")
        op = b.get("op", "")
        if op == "hello":
            return self._hello(b, ip)
        if op == "state":
            if not hex_ok(b.get("id"), 16):
                return self._refuse(400, "bad_request", "Malformed id")
            p = self._find_pending(b["id"])
            if p and p["phase"] == "pending" and p["ip"] == ip:
                p["at"] = self._now()          # still asking: a waiting request stays
            return self._state(b["id"])
        if op == "reveal":
            return self._reveal(b)
        if op == "chal":
            return self._chal(b)
        if op == "beat":
            return self._beat(b, ip)
        if op == "ota":
            return self._ota(b, ip)
        return self._refuse(400, "bad_request", "Unknown op")

    def _hello(self, b, ip):
        name = b.get("name", "")
        if b.get("v") != 1:
            return self._refuse(400, "unsupported", "Protocol version not supported")
        if not (hex_ok(b.get("id"), 16) and hex_ok(b.get("a_pub"), 32) and hex_ok(b.get("commit"), 32)
                and isinstance(name, str) and len(name.encode()) <= NAME_MAX):
            return self._refuse(400, "bad_request", "Malformed hello")
        p = self._find_pending(b["id"])
        if p:
            if p["a_pub"].hex() == b["a_pub"] and p["commit"].hex() == b["commit"]:
                return self._state(b["id"])
            self.pending.remove(p)
        self.pending = [q for q in self.pending if q["ip"] != ip]
        if len(self.pending) >= PENDING_MAX:
            return self._refuse(503, "full", "Too many computers are asking to pair right now")
        clean = "".join(" " if (ord(ch) < 0x20 or ord(ch) == 0x7f) else ch for ch in (name or "Computer"))
        self.pending.append({"id": b["id"], "name": clean, "a_pub": bytes.fromhex(b["a_pub"]),
                             "commit": bytes.fromhex(b["commit"]), "ip": ip, "at": self._now(),
                             "phase": "pending", "tries": 0})
        self.sim._push({"event": "companion_pending", "name": clean})
        return self._state(b["id"])

    def _reveal(self, b):
        if not (hex_ok(b.get("id"), 16) and hex_ok(b.get("n_a"), 16)):
            return self._refuse(400, "bad_request", "Malformed reveal")
        p = self._find_pending(b["id"])
        if not p or p["phase"] != "confirm":
            return self._refuse(409, "not_ready", "Nothing to reveal yet")
        n_a = bytes.fromhex(b["n_a"])
        if not hmac.compare_digest(commit(p["a_pub"], n_a), p["commit"]):
            self.pending.remove(p)
            return self._refuse(400, "bad_mac", "The reveal does not match the commitment")
        shared = x25519(p.pop("b_sec"), p["a_pub"])
        if shared == bytes(32):
            self.pending.remove(p)
            return self._refuse(400, "bad_request", "Unusable key")
        p["code"] = sas(p["a_pub"], p["b_pub"], n_a, p["n_b"])
        p["key"] = derive_key(shared, p["a_pub"], p["b_pub"], n_a, p["n_b"])
        p["phase"], p["at"] = "code_shown", self._now()
        return self._state(b["id"])

    def _chal(self, b):
        if not hex_ok(b.get("id"), 16):
            return self._refuse(400, "bad_request", "Malformed id")
        c = self._find_paired(b["id"])
        if not c:
            return self._refuse(404, "unknown_pc", "This computer is not paired")
        # Up to CHALS live at once (compChalIssue): a free slot, else the oldest goes.
        now = self._now()
        live = [(ch, at) for ch, at in c.get("chals", []) if now - at <= CHAL_S]
        if len(live) >= CHALS:
            live.remove(min(live, key=lambda x: x[1]))
        new = self.random(16).hex()
        c["chals"] = live + [(new, now)]
        return 200, {"status": "ok", "chal": new}

    def _take_chal(self, c, chal):
        """compChalTake: one of the live challenges, used up whatever the proof."""
        now, hit = self._now(), False
        keep = []
        for ch, at in c.get("chals", []):
            if now - at <= CHAL_S and hmac.compare_digest(ch, chal):
                hit = True
            else:
                keep.append((ch, at))
        c["chals"] = keep
        return hit

    def _beat(self, b, ip):
        port = b.get("port", 0)
        if not (hex_ok(b.get("id"), 16) and hex_ok(b.get("chal"), 16) and hex_ok(b.get("mac"), 32)
                and hex_ok(b.get("fp"), 32) and hex_ok(b.get("tn"), 16)
                and isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535):
            return self._refuse(400, "bad_request", "Malformed heartbeat")
        c = self._find_paired(b["id"])
        if not c:
            return self._refuse(404, "unknown_pc", "This computer is not paired")
        fresh = self._take_chal(c, b["chal"])
        if not fresh:
            return self._refuse(401, "stale_chal", "Ask for a new challenge")
        want = mac(c["key"], f'beat|{b["id"]}|{port}|{b["fp"]}|{b["tn"]}|{b["chal"]}')
        if not hmac.compare_digest(want, b["mac"]):
            return self._refuse(401, "bad_mac", "The heartbeat proof is wrong")
        was = self.online(c)
        c.update(ip=ip, port=port, fp=b["fp"], tn=b["tn"], seen=self._now())
        if not was:
            self.sim._push({"event": "companion_online", "name": c["name"]})
        return 200, {"status": "ok"}

    def _ota(self, b, ip):
        """compOta (hid_fi/ota.h): the paired Companion's update request."""
        url, act = b.get("url", ""), b.get("act", "")
        if not (hex_ok(b.get("id"), 16) and hex_ok(b.get("chal"), 16) and hex_ok(b.get("mac"), 32)
                and isinstance(url, str) and len(url) <= 160 and isinstance(act, str)):
            return self._refuse(400, "bad_request", "Malformed update request")
        c = self._find_paired(b["id"])
        if not c:
            return self._refuse(404, "unknown_pc", "This computer is not paired")
        fresh = self._take_chal(c, b["chal"])
        if not fresh:
            return self._refuse(401, "stale_chal", "Ask for a new challenge")
        want = mac(c["key"], f'ota|{b["id"]}|{act}|{url}|{b["chal"]}')
        if not hmac.compare_digest(want, b["mac"]):
            return self._refuse(401, "bad_mac", "The update request's proof is wrong")
        o, done = self.sim.ota, None
        cmd = {"check": "ota_check", "from": "ota_check", "install": "ota_install", "cancel": "ota_cancel",
               "apply": "ota_apply"}.get(act)
        if act == "from":
            if not url.startswith(f"http://{ip}:"):
                return self._refuse(400, "bad_request", "A relay must be this computer")
            o["relay"] = url if url.endswith("/") else url + "/"
        if act == "download_mode":
            if o["state"] == "pending_verify":
                return self._refuse(409, "verifying", "Not now")
            if o["step"] != "idle":
                return self._refuse(409, "busy", "Not now")
            o["download_mode"] = True
        elif cmd:
            r = self.sim.ota_command(cmd, {}, "companion")
            if r.get("status") != "ok":
                if act == "from":
                    o["relay"] = ""
                return self._refuse(409, r.get("message", "refused"), "Not now")
            done = r.get("done")
        else:
            return self._refuse(400, "bad_request", "Unknown act")
        out = {"status": "ok", "act": act, "step": o["step"]}
        if done:
            out["done"] = done
        return 200, out

    # ---- dashboard commands -------------------------------------------------------------
    def command(self, c, b, transport):
        self.tick()
        E = lambda reply, msg, **kw: dict({"status": "error", "reply": reply, "message": msg}, **kw)
        bad_id = E("bad_request", "id must be 32 lowercase hex digits")
        if c == "companion_list":
            out = {"status": "ok", "reply": "companion_list", "pin_set": bool(self.sim.pin), "paired": [], "pending": []}
            for x in self.paired:
                o = {"id": x["id"], "name": x["name"], "online": self.online(x)}
                if x.get("seen") is not None:
                    o.update(ip=x["ip"], port=x["port"], seen_s=int(self._now() - x["seen"]))
                out["paired"].append(o)
            for p in self.pending:
                out["pending"].append({"id": p["id"], "name": p["name"], "state": p["phase"], "ip": p["ip"]})
            return out
        if not hex_ok(b.get("id"), 16):
            return bad_id
        id_ = b["id"]
        if c == "companion_unpair":
            p, x = self._find_pending(id_), self._find_paired(id_)
            if p:
                self.pending.remove(p)
            if x:
                self.paired.remove(x)
            return {"status": "ok", "reply": "companion_unpair", "removed": bool(p or x)}
        if c == "companion_pair":
            if not self.sim.pin:
                return E("pin_required", "Set an access PIN before pairing a computer")
            p = self._find_pending(id_)
            if not p:
                return E("not_found", "That computer is no longer asking to pair")
            code = b.get("code", "")
            if not code:
                if p["phase"] != "pending":
                    return E("not_ready", "Already approved")
                p["b_sec"], p["n_b"] = self.random(32), self.random(16)
                p["b_pub"] = x25519(p["b_sec"])
                p["phase"], p["at"] = "confirm", self._now()
                return {"status": "ok", "reply": "companion_pair", "state": "confirm"}
            if p["phase"] != "code_shown":
                return E("not_ready", "The computer has not shown its code yet")
            if not (isinstance(code, str) and len(code) == 6 and code.isdigit() and code.isascii()) \
                    or int(code) != p["code"]:
                p["tries"] += 1
                if p["tries"] >= CODE_TRIES:
                    for k in ("key", "code", "b_pub", "n_b"):
                        p.pop(k, None)
                    p["phase"], p["at"] = "aborted", self._now()
                    return E("aborted", "Wrong code three times. Start pairing again on the computer.")
                return E("wrong_code", "That is not the code on the computer", tries_left=CODE_TRIES - p["tries"])
            x = self._find_paired(id_)
            if not x and len(self.paired) >= PAIRED_MAX:
                return E("full", "Four computers are paired already. Remove one first.")
            if x:
                self.paired.remove(x)
            self.paired.append({"id": id_, "name": p["name"], "key": p["key"]})
            self.pending.remove(p)
            return {"status": "ok", "reply": "companion_pair", "state": "paired", "name": p["name"]}
        if c == "screen_ticket":
            if transport == "serial":
                return E("wifi_only", "Screens are only shown on a signed-in dashboard")
            if not self.sim.pin:
                return E("pin_required", "Set an access PIN before sharing a screen")
            x = self._find_paired(id_)
            if not x:
                return E("not_found", "That computer is not paired")
            if not self.online(x):
                return E("pc_offline", "The companion app on that computer is not running")
            vn = self.random(16).hex()
            return {"status": "ok", "reply": "screen_ticket", "id": id_, "name": x["name"], "ip": x["ip"],
                    "port": x["port"], "fp": x["fp"], "tn": x["tn"], "vn": vn,
                    "ticket": mac(x["key"], f'tkt|{x["tn"]}|{vn}')}
        return None


class RealBoardHttp:
    """A real board's /api/companion, in the shape FakeApp calls: http(body, ip)."""

    def __init__(self, board_ip):
        self.url = f"http://{board_ip}/api/companion"

    def http(self, body, _ip):
        import json
        import urllib.error
        import urllib.request
        req = urllib.request.Request(self.url, body.encode(), {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


# ---- the app's side, for scenarios --------------------------------------------------------
class FakeApp:
    """The companion app, speaking to the model the way the Rust app speaks to a board."""

    def __init__(self, board: CompanionBoard, name="Studio PC", ip="192.168.1.20", port=47800):
        self.board, self.name, self.ip, self.port = board, name, ip, port
        self.id = os.urandom(16).hex()
        self.secret, self.n_a = os.urandom(32), os.urandom(16)
        self.a_pub = x25519(self.secret)
        self.key = None
        self.code = None
        self.fp = os.urandom(32).hex()
        self.tn = None

    def _call(self, **b):
        import json
        return self.board.http(json.dumps(b), self.ip)

    def hello(self):
        return self._call(op="hello", v=1, id=self.id, name=self.name, a_pub=self.a_pub.hex(),
                          commit=commit(self.a_pub, self.n_a).hex())

    def advance(self):
        """One poll: hello while pending; once the board answers, reveal and show the code."""
        code, r = self.hello()
        if r.get("state") == "confirm":
            b_pub, n_b = bytes.fromhex(r["b_pub"]), bytes.fromhex(r["n_b"])
            self.code = "%06d" % sas(self.a_pub, b_pub, self.n_a, n_b)
            self.key = derive_key(x25519(self.secret, b_pub), self.a_pub, b_pub, self.n_a, n_b)
            return self._call(op="reveal", id=self.id, n_a=self.n_a.hex())
        return code, r

    def ota(self, act, url=""):
        """An update request, proven like a heartbeat (hid_fi/ota.h compOta)."""
        _, r = self._call(op="chal", id=self.id)
        if r.get("status") != "ok":
            return r
        m = mac(self.key, f"ota|{self.id}|{act}|{url}|{r['chal']}")
        return self._call(op="ota", id=self.id, act=act, url=url, chal=r["chal"], mac=m)[1]

    def beat(self):
        _, r = self._call(op="chal", id=self.id)
        if r.get("status") != "ok":
            return r
        self.tn = os.urandom(16).hex()
        m = mac(self.key, f"beat|{self.id}|{self.port}|{self.fp}|{self.tn}|{r['chal']}")
        return self._call(op="beat", id=self.id, port=self.port, fp=self.fp, tn=self.tn, chal=r["chal"], mac=m)[1]
