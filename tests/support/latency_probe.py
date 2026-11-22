"""Latency on a real board, with and without a temperature watcher polling it.

The owner's rule for the chip temperature (DECISIONS.md D-13): watching it must never
cost the board latency. This measures the two paths a person feels - the WebSocket
ping the dashboard's ms pill shows (the input hot path) and the serial command round
trip - in alternating phases, quiet and with a watcher hammering the board far harder
than tools/thermal_watch.py ever may (status every 2 s and temp_log every 10 s, against
its 10 s floor), so slow drift of the WiFi cannot pass for a difference.

    python3 tests/support/latency_probe.py --label v4.3.1     (writes tests/.reports/latency-<label>.json)

Used by tests/board/test_thermal_hw.py. HIDFI_PIN from the environment only.
"""
import argparse
import json
import os
import statistics
import struct
import threading
import time
import urllib.request
from pathlib import Path


def pct(xs, p):
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def stats(xs):
    return {"n": len(xs), "p50": round(pct(xs, 50), 2), "p95": round(pct(xs, 95), 2), "p99": round(pct(xs, 99), 2),
            "mean": round(statistics.fmean(xs), 2), "max": round(max(xs), 2)}


class Ws:
    """A signed-in dashboard socket that only pings (binary opcode 4, as web_ui.h)."""

    def __init__(self, url, pin):
        import websocket
        self.c = websocket.create_connection(url, timeout=5)
        self.c.settimeout(0.8)
        try:
            greet = self.c.recv()
            needs = isinstance(greet, str) and "auth_required" in greet
        except websocket.WebSocketTimeoutException:
            needs = False
        self.c.settimeout(5)
        if needs:
            if not pin:
                raise RuntimeError("the board has an access PIN: set HIDFI_PIN")
            self.c.send(json.dumps({"cmd": "auth", "token": pin}))
            end = time.time() + 5
            while time.time() < end:
                m = self.c.recv()
                if isinstance(m, str) and "auth_" in m:
                    if "auth_ok" not in m:
                        raise RuntimeError("the PIN was not accepted")   # never retried: lockout
                    break
        self.n = 0

    def ping(self):
        self.n += 1
        self.c.send_binary(struct.pack("<BI", 4, self.n))
        t0 = time.perf_counter()
        while True:
            m = self.c.recv()
            if isinstance(m, bytes) and len(m) == 5 and m[0] == 0x81 and struct.unpack("<I", m[1:])[0] == self.n:
                return (time.perf_counter() - t0) * 1000

    def close(self):
        self.c.close()


class WatcherLoad:
    """What a watcher does to the board, at five times the rate the tool allows."""

    def __init__(self, host, status_every=2.0, log_every=10.0, pin=""):
        self.host, self.status_every, self.log_every, self.pin = host, status_every, log_every, pin
        self.stop = threading.Event()
        self.requests, self.errors, self.status_ms = 0, 0, []
        self.t = threading.Thread(target=self._run, daemon=True)

    def _get(self, path, body=None):
        req = urllib.request.Request(f"http://{self.host}{path}", data=body,
                                     headers={"Content-Type": "application/json"} if body else {})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
        return (time.perf_counter() - t0) * 1000

    def _run(self):
        last_log = 0.0
        while not self.stop.is_set():
            try:
                self.status_ms.append(self._get("/api/status"))
                self.requests += 1
                if self.pin and time.time() - last_log >= self.log_every:
                    self._get("/api/command", json.dumps({"cmd": "temp_log", "token": self.pin}).encode())
                    self.requests += 1
                    last_log = time.time()
            except OSError:
                self.errors += 1
            self.stop.wait(self.status_every)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):
        self.stop.set()
        self.t.join(10)


def measure(host, pin, phases=4, phase_s=30, gap_s=0.05):
    """Alternate quiet / loaded phases; WebSocket pings throughout. Returns the samples."""
    ws = Ws(f"ws://{host}:81", pin)
    out = {"quiet": [], "loaded": [], "load": {}}
    try:
        for i in range(phases):
            kind = "loaded" if i % 2 else "quiet"
            ctx = WatcherLoad(host, pin=pin) if kind == "loaded" else None
            if ctx:
                ctx.__enter__()
            end = time.time() + phase_s
            while time.time() < end:
                out[kind].append(ws.ping())
                time.sleep(gap_s)
            if ctx:
                ctx.__exit__()
                out["load"] = {"requests": out["load"].get("requests", 0) + ctx.requests,
                               "errors": out["load"].get("errors", 0) + ctx.errors,
                               "status_ms": out["load"].get("status_ms", []) + ctx.status_ms}
    finally:
        ws.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("HIDFI_HOST", ""))
    ap.add_argument("--label", required=True)
    ap.add_argument("--phases", type=int, default=4)
    ap.add_argument("--phase-s", type=float, default=30)
    a = ap.parse_args()
    r = measure(a.host, os.environ.get("HIDFI_PIN", ""), a.phases, a.phase_s)
    res = {"label": a.label, "quiet": stats(r["quiet"]), "loaded": stats(r["loaded"]),
           "load": {"requests": r["load"].get("requests", 0), "errors": r["load"].get("errors", 0),
                    "status_ms": stats(r["load"]["status_ms"]) if r["load"].get("status_ms") else None}}
    out = Path(__file__).resolve().parents[1] / ".reports" / f"latency-{a.label}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
