"""The board model on real sockets, for a real device on the same WiFi: an old iPad,
an Android tablet, a Raspberry Pi. The tests drive the model through Playwright;
this serves the same model to any browser, so a device the suite cannot emulate
can still open the real dashboard and be driven and inspected (docs/LEGACY_DEVICES.md).

    python3 tests/support/live_board.py            # http://<this computer>:80, socket on :81
    python3 tests/support/live_board.py --pin 4826 --crowded

Nothing here is used by the suite itself. It never talks to a real board.
"""
import argparse
import http.server
import json
import pathlib
import socket
import sys
import threading
import time
from urllib.parse import urlsplit

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from support import dashboard                     # noqa: E402
from support.board_sim import BoardSim            # noqa: E402

from websockets.sync.server import serve          # noqa: E402


class _Req:
    """What BoardSim._http reads from a Playwright request."""
    def __init__(self, h, body):
        self.url = "http://board" + h.path
        self.method = h.command
        self.headers = {k.lower(): v for k, v in h.headers.items()}
        self.post_data = body.decode("utf-8", "replace") if body else ""


class _Route:
    """What BoardSim._http calls on a Playwright route."""
    def __init__(self, h):
        self.h = h

    def fulfill(self, status=200, body=None, content_type=None, json=None):
        import json as _j
        if json is not None:
            body, content_type = _j.dumps(json), "application/json"
        data = (body or "").encode() if isinstance(body, str) else (body or b"")
        self.h.send_response(status)
        if content_type:
            self.h.send_header("Content-Type", content_type + ("; charset=utf-8" if "text/html" in content_type else ""))
        self.h.send_header("Content-Length", str(len(data)))
        self.h.send_header("Cache-Control", "no-store")
        self.h.end_headers()
        self.h.wfile.write(data)


class _Sock:
    """What BoardSim._ws calls on a Playwright WebSocketRoute."""
    def __init__(self, conn):
        self.conn, self._msg, self._close = conn, None, None

    def send(self, m):
        try:
            self.conn.send(m)
        except Exception:
            pass

    def close(self, code=1000, reason=""):
        try:
            self.conn.close(code, reason)
        except Exception:
            pass

    def connect_to_server(self):             # refused: drop it, as an unreachable board would
        self.close(1011, "refused")

    def on_message(self, fn):
        self._msg = fn

    def on_close(self, fn):
        self._close = fn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pin", default=None, help="an access PIN the page asks for (default: none)")
    ap.add_argument("--crowded", action="store_true", help="every list full, every name as long as it may be")
    ap.add_argument("--http", type=int, default=80)
    ap.add_argument("--ws", type=int, default=81, help="the page always opens its socket on :81")
    ap.add_argument("--html", default=None, help="serve this file instead of the dashboard in hid_fi/web_ui.h")
    a = ap.parse_args()
    if a.crowded:
        from support.responsive_run import crowded_board
        sim = crowded_board()
    else:
        sim = BoardSim(pin=a.pin)
    sim.phone_on_lan = True
    html = pathlib.Path(a.html).read_text(encoding="utf-8") if a.html else dashboard.extract_html()

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _go(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            sim._http(_Route(self), _Req(self, body), html, self.client_address[0])

        do_GET = do_POST = _go

        def log_message(self, *args):
            pass

    def ws_handler(conn):
        s = _Sock(conn)
        sim._ws(s, conn.remote_address[0])
        try:
            for m in conn:
                if s._msg:
                    s._msg(m)
        finally:
            if s._close:
                s._close()

    def ticker():
        while True:
            sim.tick()
            time.sleep(0.1)

    threading.Thread(target=ticker, daemon=True).start()
    wss = serve(ws_handler, "0.0.0.0", a.ws)
    threading.Thread(target=wss.serve_forever, daemon=True).start()
    ip = socket.gethostbyname(socket.gethostname())
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        pass
    print(f"Board model live: open http://{ip}{'' if a.http == 80 else ':' + str(a.http)}/ on the device", flush=True)
    http.server.ThreadingHTTPServer(("0.0.0.0", a.http), H).serve_forever()


if __name__ == "__main__":
    main()
