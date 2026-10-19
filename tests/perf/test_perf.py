"""Performance and soak. Opt in with --perf (plus --board / --e2e / --board-url).

Budgets are product requirements, not measurements of this machine:
  serial round trip p95  < 40 ms     the serial/dashboard command path stays interactive
  boot to banner         < 6 s       a replug is usable quickly
  OS detection           < 5 s       Mac/PC switch happens before the user notices
  heap after a soak      loses < 2 KB, floor never below 40 KB
  key to host p95        < 60 ms     typing feels immediate (over serial)
  typing throughput      >= 20 char/s (the firmware paces at 10 ms down + 10 ms up)
  pointer to host p95    < 60 ms     (over serial; the WiFi stream is measured separately)
  WiFi ping p95          < 80 ms     the latency the dashboard's ms pill shows
"""
import json
import struct
import subprocess
import sys
import time

import pytest

from support import paths
from support.perf import pct, summary

pytestmark = pytest.mark.perf


# ---- the board alone -------------------------------------------------------------------------
@pytest.mark.board
def test_serial_round_trip(board, perf):
    lat = []
    for _ in range(200):
        t0 = time.perf_counter()
        board.cmd({"cmd": "ping"}, "pong")
        lat.append((time.perf_counter() - t0) * 1000)
    perf.check("serial_rtt_p95", pct(lat, 95), "ms", 40, detail=summary(lat))
    perf.check("serial_cmds_per_s", 1000 / (sum(lat) / len(lat)), "/s", 20, lower_is_better=False)


@pytest.mark.board
def test_boot_time(_board_holder, perf):
    _board_holder.release()
    b = _board_holder.get()              # re-open = one reboot, timed
    if b.boot_s is None:
        pytest.skip("did not see the boot banner (some hosts do not reset the board on open)")
    perf.check("boot_to_banner", b.boot_s, "s", 6.0)


@pytest.mark.board
def test_os_detection_time(board, perf):
    st = board.status()
    if not st.get("usb_mounted"):
        pytest.skip("the board's USB side is not plugged into a computer")
    pin = st["host_os"] if st.get("host_os_source") == "manual" else "auto"
    t0 = time.time()
    board.cmd({"cmd": "set_host_os", "os": "auto"}, "host_os_set")      # re-arms detection
    took = None
    while time.time() - t0 < 10:
        if board.status().get("detect_phase") == "settled":
            took = time.time() - t0
            break
        time.sleep(0.1)
    board.cmd({"cmd": "set_host_os", "os": pin}, "host_os_set")
    assert took is not None, "detection never settled"
    perf.check("os_detection", took, "s", 5.0)


@pytest.mark.board
@pytest.mark.slow
def test_memory_soak_script(board_released):
    r = subprocess.run([sys.executable, str(paths.TESTS / "test_memory_soak.py"), board_released],
                       capture_output=True, text=True, timeout=1800)
    assert r.returncode == 0, r.stdout[-3000:]


@pytest.mark.board
@pytest.mark.slow
def test_heap_under_sustained_load(board, perf, request):
    """Minutes of mixed traffic, net zero on the host. Free heap must come back."""
    minutes = request.config.getoption("--soak-minutes")
    start = board.status()
    end_t = time.time() + 60 * minutes
    n = 0
    while time.time() < end_t:
        board.cmd({"cmd": "status"}, "status")
        board.cmd({"cmd": "mouse_move", "dx": 3, "dy": 0})
        board.cmd({"cmd": "mouse_move", "dx": -3, "dy": 0})
        board.cmd({"cmd": "macro_list"})
        board.cmd({"cmd": "echo", "data": "x" * 200}, "echo")
        n += 5
    time.sleep(1)
    endst = board.status()
    lost = start["free_heap"] - endst["free_heap"]
    perf.check("soak_heap_lost", max(0, lost), "B", 2048, detail={"commands": n, "minutes": minutes,
               "free_start": start["free_heap"], "free_end": endst["free_heap"], "floor": endst.get("heap_floor")})
    assert endst.get("heap_floor", 10**9) >= 40000, f"heap floor fell to {endst.get('heap_floor')}"


# ---- the host side, through the test page ------------------------------------------------------
def _same_clock(sink):
    if sink.info().get("value") is None:
        pytest.skip("test page not running")
    return True


@pytest.mark.e2e
def test_key_to_host_latency(board, sink, perf, request):
    if request.config.getoption("--sink") == "remote":
        pytest.skip("latency needs the page and the runner on one clock - run locally")
    sink.set_mode("keys")
    lat = []
    for i in range(40):
        sink.clear()
        sink.assert_focused()
        t0 = time.time() * 1000
        board.cmd({"cmd": "key_down", "key": "q"}, "key_down")
        board.cmd({"cmd": "key_up", "key": "q"}, "key_up")
        _, ev = sink.settle(quiet=0.15)
        downs = [e["ts"] for e in ev if e.get("k") == "keydown" and e.get("code") == "KeyQ"]
        if downs:
            lat.append(downs[0] - t0)
    assert len(lat) >= 35, f"only {len(lat)}/40 key presses arrived"
    perf.check("key_to_host_p95", pct(lat, 95), "ms", 60, detail=summary(lat))


@pytest.mark.e2e
def test_typing_throughput(board, sink, perf):
    text = ("the quick brown fox jumps over the lazy dog 0123456789 " * 4)[:200]
    sink.set_mode("text")
    sink.clear()
    sink.assert_focused()
    board.cmd({"cmd": "type", "text": text}, "typed", timeout=120)
    got, ev = sink.settle()
    assert got == text
    ins = [e["ts"] for e in ev if e.get("k") == "input"]
    rate = (len(ins) - 1) / ((ins[-1] - ins[0]) / 1000)
    perf.check("typing_chars_per_s", rate, "/s", 20, lower_is_better=False, detail={"chars": len(ins)})


@pytest.mark.e2e
def test_pointer_to_host_latency(board, sink, perf, request):
    if request.config.getoption("--sink") == "remote":
        pytest.skip("latency needs the page and the runner on one clock - run locally")
    if not sink.info().get("locked"):
        pytest.skip("pointer lock not active")
    sink.set_mode("keys")
    lat = []
    for i in range(40):
        sink.clear()
        sink.assert_focused()
        d = 5 if i % 2 == 0 else -5
        t0 = time.time() * 1000
        board.cmd({"cmd": "mouse_move", "dx": d, "dy": 0})
        _, ev = sink.settle(quiet=0.15)
        mv = [e["ts"] for e in ev if e.get("k") == "move"]
        if mv:
            lat.append(mv[0] - t0)
    assert len(lat) >= 35, f"only {len(lat)}/40 moves arrived"
    perf.check("pointer_to_host_p95", pct(lat, 95), "ms", 60, detail=summary(lat))


# ---- the WiFi hot path ----------------------------------------------------------------------------
@pytest.fixture
def ws(request, board):
    from support.board import lan_url
    url = lan_url(request.config.getoption("--board-url"), board)
    if not url:
        pytest.skip("WiFi tests need --board-url ws://<board-ip>:81 and this machine on the board's network")
    websocket = pytest.importorskip("websocket", reason="pip install websocket-client")
    conn = websocket.create_connection(url, timeout=5)
    # A board with a PIN greets every new socket with auth_required and then
    # silently ignores pointer frames until auth succeeds.
    conn.settimeout(0.8)
    conn.needs_auth = False
    try:
        greet = conn.recv()
        conn.needs_auth = isinstance(greet, str) and "auth_required" in greet
    except websocket.WebSocketTimeoutException:
        pass
    conn.settimeout(5)
    conn.authed = not conn.needs_auth
    pin = request.config.getoption("--board-pin")
    if conn.needs_auth and pin:
        conn.send(json.dumps({"cmd": "auth", "token": pin}))
        end = time.time() + 5
        while time.time() < end:
            m = conn.recv()
            if isinstance(m, str) and ("auth_ok" in m or "auth_failed" in m or "auth_locked" in m):
                conn.authed = "auth_ok" in m
                break
    yield conn
    conn.close()


def _ping(conn, n):
    conn.send_binary(struct.pack("<BI", 4, n))
    t0 = time.perf_counter()
    while True:
        m = conn.recv()
        if isinstance(m, bytes) and len(m) == 5 and m[0] == 0x81 and struct.unpack("<I", m[1:])[0] == n:
            return (time.perf_counter() - t0) * 1000


def test_wifi_ping_round_trip(ws, perf):
    lat = [_ping(ws, i) for i in range(100)]
    perf.check("wifi_ping_p95", pct(lat, 95), "ms", 80, detail=summary(lat))


def test_wifi_pointer_stream_keeps_up(ws, perf, request):
    """60 Hz of pointer frames for 5 s (net zero), with pings interleaved: the
    board must keep answering promptly while it streams - the 'laggy cursor' check."""
    if not ws.authed:
        pytest.skip("the board has an access PIN - pass it with HIDFI_PIN (or --board-pin) so pointer frames are accepted")
    lat = []
    t_end = time.time() + 5
    i = 0
    while time.time() < t_end:
        d = 2 if i % 2 == 0 else -2
        ws.send_binary(struct.pack("<Bhhbb", 1, d, 0, 0, 0))
        if i % 6 == 0:
            lat.append(_ping(ws, 10_000 + i))
        i += 1
        time.sleep(1 / 60)
    perf.check("wifi_ping_under_stream_p95", pct(lat, 95), "ms", 120, detail=summary(lat) | {"frames": i})
