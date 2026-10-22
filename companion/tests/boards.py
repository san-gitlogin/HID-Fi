"""Boards moving between computers, and two boards on one computer - for
real: the real companion service (fake USB, test-pattern screen), two board
models over real HTTP, and real Google Chrome watching through tickets each
board signs.

Seen live (v4.0.1): a board that had been paired with a Windows laptop was
plugged into a Mac that already had another board; its dashboard showed only
the old laptop ("not reachable") and never this Mac. The app served one board
per computer, and re-pairing one board threw the other's pairing away.

  1. Board B was paired with another computer (now off). Plugged in here next
     to board A, B's dashboard lists the old computer AND this one, asking to
     pair; so does A's.
  2. Both pair; a viewer from EITHER dashboard gets the screen.
  3. B unplugged: B's viewer ends, B's new viewers are refused, A keeps going.
  4. B plugged back: online again with no new pairing.
  5. A removes this computer on its dashboard: A asks to pair again, B's
     pairing is untouched.

Run: cargo build --release -p hidfi-companion --example system_app
     python3 companion/tests/boards.py
"""
import json, os, pathlib, queue, re, socket, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = str(ROOT / "target/release/examples" / ("system_app.exe" if os.name == "nt" else "system_app"))
MAC_A, MAC_B = "B8:1F:3F:DA:1A:2C", "DC:B4:D9:09:F7:C8"


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.0.1", 9)); ip = s.getsockname()[0]
    finally:
        s.close()
    import ipaddress
    return ip if ipaddress.ip_address(ip).is_private and not ip.startswith("192.0.0.") else "127.0.0.1"


IP = lan_ip()
ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)


class Board:
    """A board model on its own port, with its own MAC."""
    def __init__(self, mac):
        self.mac = mac
        self.p = subprocess.Popen([sys.executable, str(ROOT / "tests" / "model_board.py"), "4826"],
                                  env=dict(os.environ, HOST=IP, MAC=mac), stdout=subprocess.PIPE, text=True)
        self.url = f"http://{IP}:{int(self.p.stdout.readline())}"

    def dash(self, **cmd):
        r = urllib.request.Request(self.url + "/__dash", data=json.dumps(cmd).encode(), method="POST")
        return json.loads(urllib.request.urlopen(r, timeout=5).read())


class App:
    """The real service, with these boards on its pretend USB."""
    def __init__(self, name, boards, store=None):
        self.store = store or tempfile.mkdtemp(prefix="hidfi-boards-")
        args = [APP, self.store]
        for b in boards:
            args += [b.url, b.mac]
        env = dict(os.environ, HIDFI_TEST_NAME=name)
        self.p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, env=env)
        self.lines = queue.Queue(); self.last = {}
        threading.Thread(target=lambda: [self.lines.put(json.loads(l)) for l in self.p.stdout], daemon=True).start()

    def say(self, cmd):
        self.p.stdin.write(cmd + "\n"); self.p.stdin.flush()

    def wait(self, pred, secs, what):
        end = time.time() + secs
        while time.time() < end:
            try:
                self.last = self.lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if pred(self.last):
                return self.last
        raise SystemExit(f"FAIL waiting for {what}; last view: {self.last}")

    def board(self, mac):
        m = mac.replace(":", "")
        return next((b for b in self.last.get("boards", []) if b["mac"] == m), None)

    def quit(self):
        try:
            self.say("quit"); self.p.wait(5)
        except Exception:
            self.p.kill()


def pair(app, board, name):
    """The dashboard's Pair, then the code the computer shows."""
    pend = board.dash(cmd="companion_list")["pending"]
    pc = next(p["id"] for p in pend if p["name"] == name)
    board.dash(cmd="companion_pair", id=pc)
    v = app.wait(lambda v: "ShowCode" in str((app.board(board.mac) or {}).get("session")), 10, "the code")
    code = re.search(r'"(\d{6})"', app.board(board.mac)["session"]).group(1)
    r = board.dash(cmd="companion_pair", id=pc, code=code)
    app.wait(lambda v: (app.board(board.mac) or {}).get("session") == "Online", 10, f"{board.mac} online")
    return pc, r.get("state") == "paired"


page_html = "<!doctype html><meta charset=utf-8><video id=v autoplay muted playsinline></video><script>" + \
            (ROOT / "web" / "viewer.js").read_text() + "</script>"


class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        b = page_html.encode(); self.send_response(200); self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def log_message(self, *a):
        pass


web = ThreadingHTTPServer((IP, 0), Page); threading.Thread(target=web.serve_forever, daemon=True).start()
A, B = Board(MAC_A), Board(MAC_B)
apps = []
try:
    # Board B, before: paired with another computer, which is now switched off.
    other = App("Old Laptop", [B]); apps.append(other)
    other.wait(lambda v: "WaitingForApproval" in str((other.board(MAC_B) or {}).get("session")), 15, "the old laptop asking")
    _, paired = pair(other, B, "Old Laptop")
    check("before: board B is paired with the old laptop", paired)
    other.quit()
    t_off = time.time()

    # 1. Both boards plugged into this computer.
    app = App("System Test PC", [A, B]); apps.append(app)
    app.wait(lambda v: len(v.get("boards", [])) == 2
             and all("WaitingForApproval" in b["session"] for b in v["boards"]), 20, "both boards asking to pair")
    check("this computer sees both boards plugged in", True, str([b["mac"] for b in app.last["boards"]]))
    lb = B.dash(cmd="companion_list")
    names_b = [p["name"] for p in lb.get("paired", [])] + [p["name"] for p in lb.get("pending", [])]
    check("board B's dashboard lists the old laptop AND this computer, asking to pair",
          "Old Laptop" in names_b and any(p["name"] == "System Test PC" for p in lb.get("pending", [])), str(lb))
    check("board A's dashboard lists this computer, asking to pair",
          any(p["name"] == "System Test PC" for p in A.dash(cmd="companion_list").get("pending", [])))

    # 2. Pair both; watch through each board's ticket.
    pc_a, pa = pair(app, A, "System Test PC")
    pc_b, pb = pair(app, B, "System Test PC")
    check("both boards pair with this computer", pa and pb)
    keys = sorted(f for f in os.listdir(app.store) if f.startswith("pairing-"))
    check("one pairing key per board", keys == [f"pairing-{MAC_A.replace(':', '')}.key", f"pairing-{MAC_B.replace(':', '')}.key"], str(keys))
    with sync_playwright() as p:
        br = p.chromium.launch(channel="chrome", args=["--autoplay-policy=no-user-gesture-required"])

        def watch(board, pc):
            pg = br.new_page(); pg.goto(f"http://{IP}:{web.server_address[1]}/")
            pg.evaluate("""t => { window.S = []; window.H = watchScreen(t, document.getElementById('v'), s => S.push(s)); }""",
                        board.dash(cmd="screen_ticket", id=pc))
            return pg

        va, vb = watch(A, pc_a), watch(B, pc_b)
        for name, pg in (("A", va), ("B", vb)):
            try:
                pg.wait_for_function("S.includes('live') && document.getElementById('v').videoWidth > 0", timeout=15000)
                check(f"a viewer from board {name}'s dashboard sees this screen", True)
            except Exception:
                check(f"a viewer from board {name}'s dashboard sees this screen", False, str(pg.evaluate("S")))

        # 3. Board B leaves this computer.
        app.say(f"unplug {MAC_B}")
        try:
            vb.wait_for_function("S.some(s => s.startsWith('ended'))", timeout=10000)
            check("B unplugged: B's viewer ends", True)
        except Exception:
            check("B unplugged: B's viewer ends", False, str(vb.evaluate("S")))
        app.wait(lambda v: len(v.get("boards", [])) == 1, 10, "only A left")
        f0 = va.evaluate("document.getElementById('v').getVideoPlaybackQuality().totalVideoFrames")
        time.sleep(1.5)
        f1 = va.evaluate("document.getElementById('v').getVideoPlaybackQuality().totalVideoFrames")
        check("...and A's viewer keeps playing", f1 > f0 and "ended" not in str(va.evaluate("S")), f"{f1 - f0} frames in 1.5 s")
        t = B.dash(cmd="screen_ticket", id=pc_b)
        if t.get("reply") == "screen_ticket":
            vb.evaluate("""t => { window.S = []; watchScreen(t, document.getElementById('v'), s => S.push(s)); }""", t)
            vb.wait_for_function("S.some(s => s.startsWith('refused') || s.startsWith('ended'))", timeout=15000)
            check("...and a new viewer from B is refused", any(s.startswith("refused") for s in vb.evaluate("S")), str(vb.evaluate("S")))
        else:
            check("...and a new viewer from B is refused", t.get("reply") == "pc_offline", str(t))

        # 4. Board B comes back: no new pairing.
        app.say(f"plug {MAC_B}")
        app.wait(lambda v: (app.board(MAC_B) or {}).get("session") == "Online", 20, "B back online")
        check("B plugged back: online again, no new pairing asked", not B.dash(cmd="companion_list").get("pending"))
        vb.evaluate("""t => { window.S = []; watchScreen(t, document.getElementById('v'), s => S.push(s)); }""",
                    B.dash(cmd="screen_ticket", id=pc_b))
        try:
            vb.wait_for_function("S.includes('live')", timeout=15000)
            check("...and B's viewers see this screen again", True)
        except Exception:
            check("...and B's viewers see this screen again", False, str(vb.evaluate("S")))
        br.close()

    # 5. A removes this computer: only A pairs again.
    A.dash(cmd="companion_unpair", id=pc_a)
    app.wait(lambda v: "WaitingForApproval" in str((app.board(MAC_A) or {}).get("session")), 25, "A asking to pair again")
    check("A removed this computer: A asks to pair again", True)
    check("...and B's pairing is untouched", (app.board(MAC_B) or {}).get("session") == "Online"
          and os.path.exists(os.path.join(app.store, f"pairing-{MAC_B.replace(':', '')}.key")), str(app.board(MAC_B)))
    # The board counts a computer online for 60 s after its last heartbeat
    # (COMP_ONLINE_MS): by then the old laptop shows as off, as it did live.
    while time.time() - t_off < 75:
        old = next((p for p in B.dash(cmd="companion_list").get("paired", []) if p["name"] == "Old Laptop"), {})
        if old and not old.get("online"):
            break
        time.sleep(2)
    check("board B shows the old laptop as off (not reachable) and keeps it listed",
          old and not old.get("online"), f"{time.time() - t_off:.0f} s after it went off: {old}")
finally:
    for a in apps:
        a.p.kill()
    A.p.kill(); B.p.kill(); web.shutdown()
sys.exit(0 if ok else 1)
