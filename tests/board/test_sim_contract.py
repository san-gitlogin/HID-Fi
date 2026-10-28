"""The simulated board must answer like the real one.

The dashboard's BDD scenarios run against support/board_sim.py so they can run
anywhere, including CI. That is only worth something while the model replies
the way the firmware does - so this sends the same commands to the real board
(over serial) and to the model, and compares reply, status and the exact set of
fields. Values are not compared (uptime, heap and so on differ by nature).

Every command here is read-only, or changes something and puts it back. None of
them is a PIN attempt: a wrong vault or access PIN counts towards the board's
lockout (up to 5 minutes), so the PIN gates are checked against the model in the
ui tier instead, never against a board someone is using.
"""
import json
import time

import pytest

from support.board_sim import BoardSim

pytestmark = pytest.mark.board

READ_ONLY = [
    {"cmd": "status"}, {"cmd": "ping"}, {"cmd": "wifi_status"}, {"cmd": "storage_info"}, {"cmd": "clients"},
    {"cmd": "macro_list"}, {"cmd": "vault_list"}, {"cmd": "pc_list"}, {"cmd": "ui_get"}, {"cmd": "echo", "data": "x"},
    {"cmd": "definitely_not_a_command"}, {"cmd": "media", "key": "nope"}, {"cmd": "mouse_abs", "x": 1, "y": 1},
    {"cmd": "gamepad", "button": 0, "pressed": True}, {"cmd": "macro_run", "slot": 32},
    {"cmd": "pc_delete", "slot": 99}, {"cmd": "ap_configure", "ssid": ""},
    {"cmd": "ap_configure", "ssid": "x" * 40}, {"cmd": "ap_configure", "ssid": "ok", "password": "short"},
    {"cmd": "wifi_configure", "ssid": ""}, {"cmd": "wifi_configure", "ssid": "x", "ip": "999.1.1.1", "gateway": "1.1.1.1"},
    {"cmd": "wifi_configure", "ssid": "x", "ip": "192.168.1.50"}, {"cmd": "set_lock_state", "state": "bogus"},
    {"cmd": "presenter", "action": "bogus"}, {"cmd": "gesture", "name": "bogus"}, {"cmd": "press", "keys": ""},
    {"cmd": "wiggle_stop"}, {"cmd": "set_host_os", "os": "bogus"}, {"cmd": "power_mode", "mode": "bogus"},
    {"cmd": "pointer_mode", "mode": "bogus"}, {"cmd": "unlock"}, {"cmd": "key_down", "key": "NOPE!"},
    {"cmd": "set_unicode", "os": "windows", "on": True}, {"cmd": "unicode_test", "os": "beos"},
    {"cmd": "type", "text": ""}, {"cmd": "set_auth", "token": "12"},
    {"cmd": "pc_save", "name": "", "password": ""}, {"cmd": "vault_save", "label": "", "secret": ""},
    {"cmd": "macro_save", "slot": 32, "steps": ["text x"]}, {"cmd": "macro_save", "slot": 0, "steps": []},
    {"cmd": "system", "action": "bogus"},
    {"cmd": "ap_password"},                                  # USB only: shape compared, never printed
    # Actions for agents: the lists, and refusals that press nothing.
    {"cmd": "actions"}, {"cmd": "actions", "keys": True}, {"cmd": "do", "a": "definitely_not_an_action"},
    {"cmd": "open"}, {"cmd": "steps", "s": []}, {"cmd": "steps", "s": ["dance"]},
    # Saved networks: the list, and forgetting one that is not saved.
    {"cmd": "wifi_networks"}, {"cmd": "wifi_forget", "ssid": "definitely-not-a-saved-network"},
    # Screen sharing: the list, and refusals that change nothing (an id no board has).
    {"cmd": "companion_list"}, {"cmd": "companion_unpair", "id": "0f" * 16},
    {"cmd": "companion_pair", "id": "0f" * 16}, {"cmd": "companion_pair", "id": "NOT-HEX"},
    {"cmd": "screen_ticket", "id": "0f" * 16},               # serial: always wifi_only
    # Updates: where the board stands, and the refusals that change nothing when
    # nothing is in progress (hid_fi/ota.h).
    {"cmd": "ota_status"}, {"cmd": "ota_cancel"}, {"cmd": "ota_install"}, {"cmd": "ota_apply"},
    {"cmd": "ota_settings"}, {"cmd": "ota_source"},          # no url: only reads it
]


def shape(r):
    return {"status": r.get("status"), "reply": r.get("reply"), "keys": sorted(r.keys()),
            "message": r.get("message") if r.get("status") == "error" else None}


@pytest.fixture(scope="module")
def model(_board_holder):
    board = _board_holder.get()
    st = board.status()
    # Opening the port can reboot the board (macOS toggles the lines anyway), and
    # a board with a saved network then spends a few seconds joining it: the
    # replies differ by phase, so mirror the phase it settles in, not a passing one.
    end = time.time() + 30
    while st.get("sta_phase") in ("connecting", "reconnecting") and time.time() < end:
        time.sleep(1)
        st = board.status()
    sim = BoardSim(pin="9999" if st.get("auth_set") else "", host_os=st.get("host_os", "mac"),
                   detected=st.get("host_os_detected", "mac"))
    sim.host_manual = st["host_os"] if st.get("host_os_source") == "manual" else None
    # The owner's own switches change replies (09F7C8 has the gamepad on): mirror them.
    sim.gamepad = bool(st.get("gamepad"))
    if st.get("wifi_connected"):        # replies differ online: wifi_status adds the link details
        sim.sta.update(phase="online", ssid=st.get("wifi_ssid") or "home", ip=st.get("wifi_ip") or "192.168.1.50")
    # The board's network history shows in status (sta_prev_ip once an address
    # changed, as tests/board/test_home_network.py makes it do): mirror it.
    sim.net.update(last_ip=st.get("sta_last_ip", ""), prev_ip=st.get("sta_prev_ip", ""),
                   changes=st.get("sta_ip_changes", 0), drops=st.get("sta_drops", 0))
    return sim


@pytest.mark.parametrize("cmd", READ_ONLY, ids=[json.dumps(c, sort_keys=True)[:60] for c in READ_ONLY])
def test_model_replies_like_the_board(board, model, cmd):
    real = board.cmd(cmd, timeout=6)
    fake = model.handle(dict(cmd), "serial")
    assert fake, f"the model sent no reply to {cmd}, the board sent {real}"
    assert shape(fake[0]) == shape(real), f"\n board: {shape(real)}\n model: {shape(fake[0])}"


def test_stateful_round_trips_reply_like_the_board(board, model):
    """Changes that are put straight back: a macro in a free slot, the Unicode
    switch, the lock state, the power mode."""
    st = board.status()
    slot = board.free_macro_slot()
    seq = [
        {"cmd": "macro_save", "slot": slot, "name": "contract", "steps": ["text hi", "key ENTER"]},
        {"cmd": "macro_get", "slot": slot}, {"cmd": "macro_list"}, {"cmd": "macro_delete", "slot": slot},
        {"cmd": "set_unicode", "os": "mac", "on": not st["unicode_mac"]},
        {"cmd": "set_unicode", "os": "mac", "on": st["unicode_mac"]},
        {"cmd": "set_lock_state", "state": "locked"}, {"cmd": "lock"},
        {"cmd": "set_lock_state", "state": st.get("pc_state", "unknown")},
        {"cmd": "power_mode", "mode": st["power_mode"]},
        {"cmd": "set_verbose", "on": not st.get("verbose", False)},
        {"cmd": "set_verbose", "on": st.get("verbose", False)},
        {"cmd": "set_host_os", "os": st["host_os"] if st.get("host_os_source") == "manual" else "auto"},
    ]
    model.pc_state = st.get("pc_state", "unknown")
    model.unicode["mac"] = st["unicode_mac"]
    model.power_mode = st["power_mode"]
    model.verbose = st.get("verbose", False)
    diffs = []
    for c in seq:
        real = board.cmd(c, timeout=6)
        fake = model.handle(dict(c), "serial")[0]
        if shape(fake) != shape(real):
            diffs.append(f"{c}\n   board: {shape(real)}\n   model: {shape(fake)}")
    assert not diffs, "\n".join(diffs)
