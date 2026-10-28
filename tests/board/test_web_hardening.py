"""The dashboard's web server survives hostile requests (GHSA-8cmm-3887-r32j).

Every HID-Fi up to v4.0.4 shipped ESP32 core 3.3.1, whose WebServer copies a
multipart boundary of any length into a stack array in `_parseForm()`. That runs
for any POST before HID-Fi's handlers, so neither the access PIN nor the LAN gate
sees it: anyone on the board's WiFi, or on the home network while the board is
joined, could crash it (and the advisory says possibly run code). Core 3.3.8+
refuses boundaries over 70 characters (RFC 2046). Found by tools/dep_watch.py
(AGENTS.md, "Outside facts and dependencies").

How it is checked: over the network the board is reachable on, send that request
- harmless on a fixed board, a reboot on a vulnerable one - then read the board's
uptime over serial. A board that rebooted has a smaller uptime than before, and
fails. Nothing is stored or changed; the request carries no command.

Runs with --board when the board is reachable over a network from this machine
(the home network it joined, or its own access point given as --board-url).
"""
import socket
import time
from urllib.parse import urlparse

import pytest

from support.board import lan_url

pytestmark = pytest.mark.board

BOUNDARY_LEN = 9000          # past the 8192-byte loopTask stack


def _http_host(request, board):
    # Opening the COM port resets the board; it rejoins the home network in about
    # 10 s. Deciding "no network" before then would be a skip that hides the check.
    end = time.time() + 40
    while not request.config.getoption("--board-url") and time.time() < end:
        if board.status().get("wifi_connected"):
            break
        time.sleep(1)
    url = lan_url(request.config.getoption("--board-url"), board)
    if not url:
        pytest.skip("the board is on no network this machine can reach - join it to the home network, "
                    "or pass --board-url ws://192.168.4.1:81 with this machine on its WiFi")
    return urlparse(url).hostname


def _post_multipart(host, path, boundary):
    part = (f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="f"; filename="x.bin"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
            "0123456789\r\n"
            f"--{boundary}--\r\n")
    head = (f"POST {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n"
            f"Content-Type: multipart/form-data; boundary={boundary}\r\n"
            f"Content-Length: {len(part)}\r\n\r\n")
    with socket.create_connection((host, 80), timeout=10) as s:
        s.sendall(head.encode() + part.encode())
        try:
            return s.recv(256)
        except OSError:
            return b""                          # a crashing board answers nothing


def _survived(board, before_uptime, before_clock):
    """Did the board keep running since `before`? A reboot shows two ways: its
    uptime has not advanced by the time that really passed (comparing uptime to
    the old value alone is not enough - a crashed board can leave the socket
    waiting out its timeout while the restarted board counts past the old
    value; that made this test pass against a crashing board once), and the
    board prints a boot event on serial."""
    seen = len(board.events)
    end = time.time() + 15
    while time.time() < end:
        try:
            st = board.status()
        except Exception:
            time.sleep(1)
            continue
        elapsed = time.time() - before_clock
        booted = any(e.get("event") == "boot" for e in board.events[seen:])
        return st["uptime_sec"] >= before_uptime + elapsed - 2 and not booted, st, elapsed
    return False, {}, time.time() - before_clock


@pytest.mark.parametrize("path", ["/api/command", "/api/companion", "/no-such-page"])
def test_a_huge_multipart_boundary_does_not_crash_the_board(board, request, path):
    host = _http_host(request, board)
    before, clock = board.status()["uptime_sec"], time.time()
    _post_multipart(host, path, "a" * BOUNDARY_LEN)
    time.sleep(2)                                # a crash reboots within a second
    alive, st, elapsed = _survived(board, before, clock)
    assert alive, (f"the board restarted after a POST to {path} with a {BOUNDARY_LEN}-character multipart "
                   f"boundary (uptime {before} s, {elapsed:.0f} s later {st.get('uptime_sec')} s) - the WebServer "
                   f"stack overflow GHSA-8cmm-3887-r32j; the core must be 3.3.8 or newer")


def test_the_dashboard_still_answers_after_hostile_requests(board, request):
    """Not only "does not reboot": the same server keeps serving. Asked twice,
    with the board's own uptime proving no restart happened in between."""
    host = _http_host(request, board)
    before, clock = board.status()["uptime_sec"], time.time()
    for boundary in ("b" * BOUNDARY_LEN, "c" * 71):           # far over, and one past RFC 2046's 70
        _post_multipart(host, "/api/command", boundary)
    time.sleep(2)
    with socket.create_connection((host, 80), timeout=10) as s:
        s.sendall(f"GET /api/status HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        first = s.recv(64)
    alive, st, elapsed = _survived(board, before, clock)
    assert alive, f"the board restarted (uptime {before} s, {elapsed:.0f} s later {st.get('uptime_sec')} s)"
    assert first.startswith(b"HTTP/1.1 200"), f"/api/status after the hostile requests: {first!r}"
