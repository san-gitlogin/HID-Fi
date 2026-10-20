"""Two homes, for real: the board moves between two saved networks by itself.

The second "home" is a phone's hotspot. A person turns it on and off when a
dialog on this Mac asks; everything else is driven over the USB cable. The home
network is never forgotten or changed - only staged as absent for a minute -
and the hotspot is forgotten again at the end.

    HIDFI_SECOND_SSID=<hotspot name> python tests/run.py --tier board --board -k two_homes

The hotspot's password is asked for in a hidden dialog, sent to the board over
the cable and never printed. iPhone: turn on Maximize Compatibility (the board
is 2.4 GHz only).
"""
import os
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.board            # skips itself unless HIDFI_SECOND_SSID names a hotspot


def _ask(text, hidden=False):
    if sys.platform != "darwin":
        return input(text + " ") if hidden else (input(text + " [Enter] ") or "ok")
    script = (f'text returned of (display dialog "{text}" default answer "" with hidden answer '
              f'with title "HID-Fi: two homes" buttons {{"Cancel", "OK"}} default button "OK")') if hidden else \
             (f'button returned of (display dialog "{text}" with title "HID-Fi: two homes" '
              f'buttons {{"Cancel", "OK"}} default button "OK")')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("cancelled by the person running it")
    return r.stdout.strip()


def _wait_on(board, ssid, timeout, what):
    t0 = time.time()
    st = {}
    while time.time() - t0 < timeout:
        st = board.status()
        if st.get("wifi_connected") and st.get("wifi_ssid") == ssid:
            return time.time() - t0
        time.sleep(2)
    raise AssertionError(f"{what}: not on {ssid} within {timeout}s - on {st.get('wifi_ssid') or 'nothing'}, "
                         f"phase {st.get('sta_phase')}, away {st.get('sta_away')}, {st.get('sta_error')}")


def test_two_homes(board):
    other = os.environ.get("HIDFI_SECOND_SSID", "")
    if not other:
        pytest.skip("set HIDFI_SECOND_SSID to a phone hotspot's name to run this with a person")
    st = board.status()
    home = st.get("wifi_ssid")
    if not st.get("wifi_connected") or not home:
        pytest.skip("the board must be on its home network to start")
    assert other != home
    saved = [n["ssid"] for n in board.cmd({"cmd": "wifi_networks"}, "wifi_networks")["networks"]]
    if len(saved) >= 4:
        pytest.skip("four or more networks saved - adding one could push the owner's out")
    took = {}
    try:
        _ask(f"Turn your phone's hotspot '{other}' ON (iPhone: Maximize Compatibility on), then click OK.")
        pw = _ask(f"The hotspot's password (sent to the board over the cable, never shown):", hidden=True)
        r = board.cmd({"cmd": "wifi_configure", "ssid": other, "password": pw})
        pw = None
        assert r.get("reply") == "wifi_connecting", r.get("message")
        took["joins the new home"] = _wait_on(board, other, 90, "joining the hotspot")
        nets = board.cmd({"cmd": "wifi_networks"}, "wifi_networks")["networks"]
        assert [n["ssid"] for n in nets][:2] == [other, home] and all(n["proven"] for n in nets[:2]), nets

        _ask(f"Turn the hotspot OFF now, then click OK. The board should go back to '{home}' by itself.")
        took["back home when it leaves"] = _wait_on(board, home, 150, "going back home")

        _ask(f"Turn the hotspot ON again, then click OK. '{home}' will be staged as gone for 90 s.")
        board.cmd({"cmd": "net_fault", "kind": "vanish", "s": 90, "ssid": home})
        took["finds the other home"] = _wait_on(board, other, 90, "finding the saved hotspot with home gone")

        board.cmd({"cmd": "net_fault", "kind": "clear"})
        _ask(f"Last one: turn the hotspot OFF, then click OK.")
        took["home again"] = _wait_on(board, home, 150, "coming home again")
    finally:
        board.cmd({"cmd": "net_fault", "kind": "clear"})
        board.cmd({"cmd": "wifi_forget", "ssid": other})
    after = [n["ssid"] for n in board.cmd({"cmd": "wifi_networks"}, "wifi_networks")["networks"]]
    assert other not in after and home in after
    print("\n  " + "\n  ".join(f"{k}: {v:.0f} s" for k, v in took.items()))
