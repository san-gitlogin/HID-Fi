"""A release server on this computer, for update tests: serves a manifest, its
signature and an image the way GitHub does (200 or 206 with Range), and can be
told to misbehave - cut the connection once at a byte, ignore Range, crawl so a
test can cancel mid-download, or hand out other bytes than the signed ones.

    with ReleaseServer({"hidfi-ota.json": m, "hidfi-ota.sig": s, "hid_fi-4.0.99.bin": img}) as srv:
        srv.url_for(board_ip)          # http://<this machine's address toward the board>:<port>/
        srv.faults["cut_at"] = 400000  # next image request stops after this many bytes
"""
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ReleaseServer:
    def __init__(self, files):
        self.files = dict(files)
        self.faults = {"cut_at": None, "ignore_range": False, "chunk_delay": 0.0, "swap": None}
        self.log = []                       # (path, range header, status, bytes sent)
        self.clients = []                   # (client address, path, status) - who asked
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                name = self.path.rsplit("/", 1)[-1]
                # Only at the root - the one place url_for points a board. Anywhere
                # else is "no release here" (a test's wrong source must look wrong).
                body = srv.files.get(name) if self.path == "/" + name else None
                if body is None:
                    self.send_response(404); self.end_headers()
                    srv.log.append((name, None, 404, 0))
                    srv.clients.append((self.client_address[0], name, 404))
                    return
                if srv.faults["swap"] and name.endswith(".bin"):
                    body = srv.faults["swap"]
                rng = self.headers.get("Range")
                start = 0
                m = re.match(r"bytes=(\d+)-$", rng or "")
                if m and not srv.faults["ignore_range"]:
                    start = int(m.group(1))
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body) - start))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                sent, cut = 0, srv.faults["cut_at"] if name.endswith(".bin") else None
                try:
                    for i in range(start, len(body), 4096):
                        chunk = body[i:i + 4096]
                        if cut is not None and i + len(chunk) > cut:
                            chunk = body[i:max(i, cut)]
                            self.wfile.write(chunk); sent += len(chunk)
                            srv.faults["cut_at"] = None          # once
                            self.connection.shutdown(socket.SHUT_RDWR)
                            break
                        self.wfile.write(chunk); sent += len(chunk)
                        if srv.faults["chunk_delay"]:
                            import time
                            time.sleep(srv.faults["chunk_delay"])
                except OSError:
                    pass
                srv.log.append((name, rng, 206 if (m and not srv.faults["ignore_range"]) else 200, sent))
                srv.clients.append((self.client_address[0], name, srv.log[-1][2]))

        self.httpd = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    def url_for(self, board_ip):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((board_ip, 80))
            me = s.getsockname()[0]
        finally:
            s.close()
        return f"http://{me}:{self.port}/"
