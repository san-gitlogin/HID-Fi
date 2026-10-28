"""Shared by the real-board update tests: install an image over the cable into the
slot that is not running (as the Companion's cable path will), read the person's
data as hashes only, wait for a status. docs/OTA.md.
"""
import hashlib
import json
import subprocess
import time

import pytest

from support import firmware, otadata as od

SLOT_OFFSET = {r["name"]: r["offset"] for r in firmware.partitions() if r["type"] == "app"}
DATA_CMDS = ["pc_list", "vault_list", "macro_list", "ui_get", "companion_list", "wifi_networks"]


def esptool(port, *args, timeout=300):
    exe = firmware.esptool_path()
    if not exe:
        pytest.skip("esptool not found (it comes with the ESP32 core)")
    r = subprocess.run([exe, "--chip", "esp32s3", "--port", port, *args], capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0, f"esptool {' '.join(args[:3])} failed:\n{(r.stdout + r.stderr)[-1500:]}"


def data_hashes(board):
    """The person's data as hashes only: each reply without its volatile fields."""
    out = {}
    for c in DATA_CMDS:
        r = board.cmd({"cmd": c})
        for k in ("reply", "online", "seen_s", "ip", "port", "current"):
            r.pop(k, None)
        for lst in r.values():
            if isinstance(lst, list):
                for item in lst:
                    if isinstance(item, dict):
                        for k in ("online", "seen_s", "ip", "port", "current"):
                            item.pop(k, None)
        out[c] = hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()
    return out


def wait_status(board, pred, timeout, what):
    end, st = time.time() + timeout, {}
    while time.time() < end:
        try:
            st = board.status()
            if pred(st):
                return st
        except Exception:
            pass
        time.sleep(1)
    raise AssertionError(f"{what} - not within {timeout}s; last: "
                         f"{ {k: st.get(k) for k in ('firmware', 'ota_slot', 'ota_state', 'ota_result', 'ota_step', 'ota_error', 'ota_bytes', 'uptime_sec')} }")


def install(_board_holder, image):
    """Write `image` into the slot that is not running and choose it - then start it
    with the one port open this scenario gets. Returns the open board."""
    board = _board_holder.get()
    running = board.status()["ota_slot"]
    target = "app1" if running == "app0" else "app0"
    port = _board_holder.port
    _board_holder.release()
    tmp = image.parent / "otadata-now.bin"
    esptool(port, "--after", "no-reset", "read-flash", "0xe000", hex(od.SIZE), str(tmp))
    cur = tmp.read_bytes()
    assert od.winner(cur) is None or od.winner(cur)[2] == int(running[-1]), \
        f"otadata says slot {od.winner(cur)}, the firmware says {running} - refusing to write"
    new = image.parent / "otadata-next.bin"
    new.write_bytes(od.switch_to(cur, slot=int(target[-1])))
    esptool(port, "--before", "no-reset", "--after", "no-reset", "--baud", "921600", "write-flash",
            hex(SLOT_OFFSET[target]), str(image), "0xe000", str(new))
    board = _board_holder.get()          # this open is the reset that starts the new image
    assert board.boots >= 1, ("opening the port did not restart the board - on this computer the port open "
                              "does not reset it; this test needs one that does (macOS) or an EN pulse")
    return board, running, target
