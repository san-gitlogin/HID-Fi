"""The board model (tests/support/board_sim.py) over real HTTP, for the Rust
agent's integration tests. Prints the port it listens on, then serves:

  GET  /api/status       the model's status, as the firmware's route
  POST /api/companion    the companion endpoint, as the firmware's route
  POST /__dash           {"cmd": ...} as a signed-in dashboard would send it
  POST /__advance        {"s": seconds}: move the board's clock on
  POST /__reboot         the board restarts: waiting requests are gone, pairings stay
  POST /__pin            {"token": ..., "old": ...}: set_auth over serial

Listens on 127.0.0.1, or $HOST. Only for tests.
"""
import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "tests"))
from support.board_sim import BoardSim  # noqa: E402

import os  # noqa: E402
# MAC=...: a second board (a computer can have two plugged in).
# PENDING_S: a short pairing-request expiry, so a test can show a waiting
# request outlives it while the app keeps asking (the board's is 2 minutes).
if os.environ.get("PENDING_S"):
    from support import companion_sim as _cs  # noqa: E402
    _cs.PENDING_S = float(os.environ["PENDING_S"])
sim = BoardSim(pin=sys.argv[1] if len(sys.argv) > 1 else "4826", mac=os.environ.get("MAC", "B8:1F:3F:DA:1A:2C"))
lock = threading.Lock()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode("utf-8", "replace")

    def do_GET(self):
        if self.path == "/api/status":
            with lock:
                return self._send(200, sim.status())
        self._send(404, {"status": "error", "message": "Not found"})

    def do_POST(self):
        body = self._body()
        with lock:
            if self.path == "/api/companion":
                return self._send(*sim.comp.http(body, self.client_address[0]))
            if self.path == "/__dash":
                return self._send(200, sim.handle(json.loads(body), "ws", ip="192.168.1.30")[0])
            if self.path == "/__reboot":
                sim.reboot()
                return self._send(200, {"ok": True})
            if self.path == "/__advance":
                sim.advance(json.loads(body)["s"])
                return self._send(200, {"ok": True})
            if self.path == "/__pin":
                return self._send(200, sim.handle(dict(json.loads(body), cmd="set_auth"), "serial")[0])
        self._send(404, {"status": "error", "message": "Not found"})


import os
srv = ThreadingHTTPServer((os.environ.get("HOST", "127.0.0.1"), 0), H)
print(srv.server_address[1], flush=True)
srv.serve_forever()
