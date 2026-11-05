"""Recovery over the USB COM cable - every case from the ownership table.

The recovery tools (tools/recover.py behind flash_esp.sh --recover, and
flash_esp.ps1 -Recover) are run end to end against a simulated board on a real
serial device (support/fake_serial.py), so what they send, what they refuse and
what they change on the board is checked without a board and without risking
anyone's stored passwords. The board model is the one the dashboard scenarios
use, itself held to the firmware by tests/board/test_sim_contract.py.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

from support import paths

pytestmark = [pytest.mark.unit, pytest.mark.skipif(sys.platform == "win32", reason="needs a pseudo-terminal")]

ROOT = paths.TESTS.parent
PWSH = os.environ.get("HIDFI_PWSH") or shutil.which("pwsh")
TOOLS = [
    pytest.param("py", id="flash_esp.sh --recover"),
    pytest.param("ps1", id="flash_esp.ps1 -Recover",
                 marks=pytest.mark.skipif(not PWSH, reason="PowerShell 7 (pwsh) not installed - runs on Windows CI")),
]


@pytest.fixture
def board(tmp_path):
    from support.board_sim import BoardSim
    from support.fake_serial import FakeSerialBoard
    boards = []

    def make(sim=None, drop=(), extra_ports=()):
        sim = sim or BoardSim()
        fb = FakeSerialBoard(sim, drop=drop)
        boards.append(fb)
        ports = [{"device": fb.path, "vid": 0x1A86, "pid": 0x55D3, "serial": f"RCV{len(boards)}"}] + list(extra_ports)
        f = tmp_path / "ports.json"
        f.write_text(json.dumps(ports), encoding="utf-8")
        return fb, str(f)
    yield make
    for fb in boards:
        fb.close()


PS_FLAGS = {"--show-password": "-RecoverShow", "--set-password": "-RecoverSetPassword",
            "--reset-pin": "-RecoverResetPin", "--erase-vault": "-EraseVault", "--port": "-Port", "--serial": "-Serial",
            "--keep-open": "-KeepOpen"}


def run(tool, ports, *args, stdin=None):
    env = dict(os.environ, HIDFI_FAKE_PORTS=ports, HIDFI_RECOVER_SETTLE="0")
    for k in ("HIDFI_PORT", "HIDFI_SERIAL", "CI"):
        env.pop(k, None)
    if tool == "py":
        cmd = [str(ROOT / "flash_esp.sh"), "--recover", *args]
    else:
        cmd = [PWSH, "-NoProfile", "-File", str(ROOT / "flash_esp.ps1"), "-Recover", *[PS_FLAGS.get(a, a) for a in args]]
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=90,
                       input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL)
    return r.returncode, r.stdout + r.stderr


def sim_with(**kw):
    from support.board_sim import BoardSim
    s = BoardSim(pin=kw.pop("pin", ""))
    for k, v in kw.items():
        setattr(s, k, v)
    return s


VAULT = {0: {"label": "Bank", "type": "password", "secret": "s3cret"}}


# ---- the WiFi password ---------------------------------------------------------------------
@pytest.mark.parametrize("tool", TOOLS)
def test_forgot_the_wifi_password_it_is_shown_over_the_cable(board, tool):
    fb, ports = board(sim_with(pin="4826", vault=dict(VAULT)))
    code, out = run(tool, ports, "--show-password")
    assert code == 0, out
    assert "k7m2-x9qp-4tre" in out and "ESP32-HID-DA1A2C" in out and "made this password for itself" in out
    assert fb.sim.pin == "4826" and fb.sim.vault, "showing must change nothing"


@pytest.mark.parametrize("tool", TOOLS)
def test_forgot_the_boards_home_address_it_is_shown_too(board, tool):
    sim = sim_with()
    sim.sta.update(phase="online", ssid="HomeNet", ip="192.168.1.100")
    fb, ports = board(sim)
    code, out = run(tool, ports, "--show-password")
    assert code == 0 and "http://192.168.1.100/" in out and "esp-hid-da1a2c.local" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_with_the_home_network_away_it_says_where_the_board_was(board, tool):
    sim = sim_with()
    sim.sta.update(phase="reconnecting", ssid="HomeNet", ip="")
    sim.net["last_ip"] = "192.168.1.107"
    fb, ports = board(sim)
    code, out = run(tool, ports, "--show-password")
    assert code == 0 and "away - it was at http://192.168.1.107/" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_an_open_network_says_so(board, tool):
    fb, ports = board(sim_with(ap_open=True, ap_pass=""))
    code, out = run(tool, ports, "--show-password")
    assert code == 0 and "network is open" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_a_new_wifi_password_is_set_and_read_back(board, tool):
    fb, ports = board(sim_with(pin="4826", vault=dict(VAULT)))
    code, out = run(tool, ports, "--set-password", "my-own-board-pass")
    assert code == 0, out
    assert fb.sim.ap_pass == "my-own-board-pass" and fb.sim.ap_gen is False
    assert fb.sim.pin == "4826" and fb.sim.vault, "the PIN and the vault are not touched"


@pytest.mark.parametrize("tool", TOOLS)
def test_putting_the_boards_own_password_back_makes_it_its_own_again(board, tool):
    # "Generated" describes the password in use; changing it and putting it back
    # must not leave the board saying the person chose it.
    fb, ports = board()
    assert run(tool, ports, "--set-password", "a-temporary-one")[0] == 0 and fb.sim.ap_gen is False
    code, out = run(tool, ports, "--set-password", "k7m2-x9qp-4tre")
    assert code == 0 and fb.sim.ap_gen is True, out


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("bad", ["short", "x" * 64])
def test_a_wifi_password_the_radio_cannot_use_is_refused(board, tool, bad):
    fb, ports = board()
    code, out = run(tool, ports, "--set-password", bad)
    assert code == 1 and "8 to 63" in out and fb.sim.ap_pass == "k7m2-x9qp-4tre", out


# ---- the access PIN -------------------------------------------------------------------------
@pytest.mark.parametrize("tool", TOOLS)
def test_forgot_the_pin_nothing_stored(board, tool):
    fb, ports = board(sim_with(pin="4826"))
    code, out = run(tool, ports, "--reset-pin", "1357")
    assert code == 0 and fb.sim.pin == "1357", out
    assert "nothing stored" in out


@pytest.mark.parametrize("tool", TOOLS)
def test_forgot_the_pin_the_vault_survives_when_it_has_its_own_vault_pin(board, tool):
    fb, ports = board(sim_with(pin="4826", vault_pin="7391", vault=dict(VAULT)))
    code, out = run(tool, ports, "--reset-pin", "1357")
    assert code == 0, out
    assert fb.sim.pin == "1357" and fb.sim.vault == VAULT, "the vault must be kept"
    assert "kept" in out


@pytest.mark.parametrize("tool", TOOLS)
def test_forgot_the_pin_an_unguarded_vault_is_never_erased_without_saying_so(board, tool):
    fb, ports = board(sim_with(pin="4826", vault=dict(VAULT)))
    code, out = run(tool, ports, "--reset-pin", "1357")
    assert code == 1 and "WILL BE ERASED" in out, out
    assert fb.sim.pin == "4826" and fb.sim.vault == VAULT, "refused: nothing may change"


@pytest.mark.parametrize("tool", TOOLS)
def test_forgot_the_pin_erasing_the_vault_when_explicitly_asked(board, tool):
    fb, ports = board(sim_with(pin="4826", vault=dict(VAULT)))
    code, out = run(tool, ports, "--reset-pin", "1357", "--erase-vault")
    assert code == 0 and fb.sim.pin == "1357" and not fb.sim.vault, out
    assert "erased, as warned" in out


@pytest.mark.parametrize("tool", TOOLS)
def test_turning_the_pin_off_over_the_cable(board, tool):
    fb, ports = board(sim_with(pin="4826"))
    code, out = run(tool, ports, "--reset-pin", "off")
    assert code == 0 and fb.sim.pin == "" and "turned off" in out, out


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("bad", ["12", "abcd", "12345"])
def test_a_pin_that_is_not_four_digits_is_refused(board, tool, bad):
    fb, ports = board(sim_with(pin="4826"))
    code, out = run(tool, ports, "--reset-pin", bad)
    assert code == 1 and fb.sim.pin == "4826" and "four digits" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_forgot_both_wifi_password_then_pin(board, tool):
    fb, ports = board(sim_with(pin="4826", vault_pin="7391", vault=dict(VAULT)))
    assert run(tool, ports, "--show-password")[0] == 0
    code, out = run(tool, ports, "--reset-pin", "2468")
    assert code == 0 and fb.sim.pin == "2468" and fb.sim.vault == VAULT, out


# ---- the menu, as a person uses it --------------------------------------------------------------
@pytest.mark.parametrize("tool", TOOLS)
def test_menu_reset_pin_typing_erase_confirms(board, tool):
    if tool == "ps1":
        pytest.skip("PowerShell's Read-Host needs a console, not a pipe - covered by the flag form")
    fb, ports = board(sim_with(pin="4826", vault=dict(VAULT)))
    code, out = _menu(ports, ["3", "1357", "ERASE"])
    assert code == 0 and fb.sim.pin == "1357" and not fb.sim.vault, out


def test_menu_anything_but_erase_changes_nothing(board):
    fb, ports = board(sim_with(pin="4826", vault=dict(VAULT)))
    code, out = _menu(ports, ["3", "1357", "erase"])                  # wrong case: not a yes
    assert code == 1 and fb.sim.pin == "4826" and fb.sim.vault == VAULT, out


def test_menu_new_wifi_password_typed_twice_must_match(board):
    fb, ports = board()
    code, out = _menu(ports, ["2", "first-password", "other-password"])
    assert code == 1 and "did not match" in out and fb.sim.ap_pass == "k7m2-x9qp-4tre", out
    code, out = _menu(ports, ["2", "same-password", "same-password"])
    assert code == 0 and fb.sim.ap_pass == "same-password", out


def _menu(ports, answers):
    """The tool reads its answers from a terminal; give it one and type them."""
    import pty
    import select
    import time
    env = dict(os.environ, HIDFI_FAKE_PORTS=ports, HIDFI_RECOVER_SETTLE="0")
    env.pop("CI", None)
    # A terminal pair and an ordinary child process: forking a process that runs
    # threads (the fake board's) is unsafe, spawning one is not.
    fd, slave = pty.openpty()
    proc = subprocess.Popen([str(ROOT / "flash_esp.sh"), "--recover"], stdin=slave, stdout=slave, stderr=slave,
                            env=env, start_new_session=True)
    os.close(slave)
    out, i, end = b"", 0, time.time() + 60
    prompts = (b"Choose 1-4", b"New 4 digit", b"Type ERASE", b"New WiFi password", b"Type it again")
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if not r:
            continue
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        out += chunk
        while i < len(answers) and sum(out.count(p) for p in prompts) > i:
            os.write(fd, answers[i].encode() + b"\n")
            i += 1
    code = proc.wait(timeout=30)
    os.close(fd)
    return code, out.decode(errors="replace")


# ---- which board -------------------------------------------------------------------------------
@pytest.mark.parametrize("tool", TOOLS)
def test_with_two_boards_it_never_guesses(board, tool):
    other = {"device": "/dev/does-not-exist", "vid": 0x1A86, "pid": 0x55D3, "serial": "OTHER"}
    fb, ports = board(sim_with(pin="4826"), extra_ports=[other])
    code, out = run(tool, ports, "--reset-pin", "1357")
    assert code == 10 and fb.sim.pin == "4826", out


@pytest.mark.parametrize("tool", TOOLS)
def test_with_two_boards_the_serial_number_picks_one(board, tool):
    other = {"device": "/dev/does-not-exist", "vid": 0x1A86, "pid": 0x55D3, "serial": "OTHER"}
    fb, ports = board(sim_with(pin="4826"), extra_ports=[other])
    code, out = run(tool, ports, "--serial", "RCV", "--show-password")
    assert code == 0 and "k7m2-x9qp-4tre" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_a_device_that_is_not_a_hidfi_board_is_refused(board, tool):
    arduino = {"device": "/dev/does-not-exist", "vid": 0x2341, "pid": 0x0043, "serial": "UNO"}
    fb, ports = board(extra_ports=[arduino])
    code, out = run(tool, ports, "--port", "/dev/does-not-exist", "--show-password")
    assert code == 11 and "Arduino" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_older_firmware_is_told_to_update_first(board, tool):
    fb, ports = board(sim_with(pin="4826"), drop={"ap_password"})
    code, out = run(tool, ports, "--reset-pin", "1357")
    assert code == 1 and "older than v3.10" in out and fb.sim.pin == "4826", out


# ---- recovery mode: no COM cable (docs/RECOVERY_MODE.md) ------------------------------------
# A board in recovery mode shows its native USB as a serial port: Espressif's VID:PID,
# its MAC as the serial number. The tools prove it by asking (status "recovery": true),
# use it before any COM port (opening a COM port resets the board, closing recovery),
# and close recovery when done.
MAC_A, MAC_B = "DCB4D909F7C8", "B81F3FDA1A2C"


@pytest.fixture
def rec(tmp_path):
    from support.board_sim import BoardSim
    from support.fake_serial import FakeSerialBoard
    made = []

    def make(boards):
        """boards: [("recovery", sim, mac) | ("com", sim, serial) | ("rom", None, mac) | ("jtag", sim, mac)]"""
        ports = []
        for kind, sim, ident in boards:
            if kind == "rom":                                  # answers nothing: a ROM loader, another firmware
                ports.append({"device": "/dev/does-not-exist-rom", "vid": 0x303A, "pid": 0x1001, "serial": ident,
                              "description": "ESP32S3_DEV"})
                made.append(None)
                continue
            if kind != "com":                                  # a real link's serial number IS the board's MAC
                sim.mac = ":".join(ident[i:i + 2] for i in range(0, 12, 2))
            fb = FakeSerialBoard(sim, link=kind != "com")
            made.append(fb)
            if kind == "com":
                ports.append({"device": fb.path, "vid": 0x1A86, "pid": 0x55D3, "serial": ident})
            else:
                ports.append({"device": fb.path, "vid": 0x303A, "pid": 0x1001, "serial": ident,
                              "description": "USB JTAG/serial debug unit" if kind == "jtag" else "ESP32S3_DEV"})
        f = tmp_path / "ports.json"
        f.write_text(json.dumps(ports), encoding="utf-8")
        return made, str(f)
    yield make
    for fb in made:
        if fb:
            fb.close()


def in_recovery(**kw):
    s = sim_with(**kw)
    s.enter_recovery()
    return s


def cmds(fb):
    return [b.get("cmd") for b in fb.seen]


@pytest.mark.parametrize("tool", TOOLS)
def test_recovery_mode_shows_the_wifi_password_without_a_com_cable(rec, tool):
    (fb,), ports = rec([("recovery", in_recovery(), MAC_A)])
    code, out = run(tool, ports, "--show-password")
    assert code == 0 and "k7m2-x9qp-4tre" in out and "recovery mode" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_recovery_mode_is_closed_when_the_tool_is_done(rec, tool):
    (fb,), ports = rec([("recovery", in_recovery(), MAC_A)])
    code, out = run(tool, ports, "--show-password")
    assert code == 0 and cmds(fb)[-1] == "recovery_exit" and "Recovery mode closed" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_keep_open_leaves_recovery_mode_open(rec, tool):
    (fb,), ports = rec([("recovery", in_recovery(), MAC_A)])
    code, out = run(tool, ports, "--show-password", "--keep-open")
    assert code == 0 and "recovery_exit" not in cmds(fb) and fb.sim.recovery and "stays open" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_recovery_mode_resets_the_pin_keeping_a_guarded_vault(rec, tool):
    sim = in_recovery(pin="4826", vault=dict(VAULT), vault_pin="2468")
    (fb,), ports = rec([("recovery", sim, MAC_A)])
    code, out = run(tool, ports, "--reset-pin", "1357")
    assert code == 0 and sim.pin == "1357" and sim.vault, out


@pytest.mark.parametrize("tool", TOOLS)
def test_recovery_mode_never_erases_an_unguarded_vault_without_saying_so(rec, tool):
    sim = in_recovery(pin="4826", vault=dict(VAULT))
    (fb,), ports = rec([("recovery", sim, MAC_A)])
    code, out = run(tool, ports, "--reset-pin", "1357")
    assert code == 1 and sim.pin == "4826" and sim.vault and "WILL BE ERASED" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_recovery_mode_sets_a_new_wifi_password(rec, tool):
    sim = in_recovery()
    (fb,), ports = rec([("recovery", sim, MAC_A)])
    code, out = run(tool, ports, "--set-password", "a-new-pass-1")
    assert code == 0 and sim.ap_pass == "a-new-pass-1", out


@pytest.mark.parametrize("tool", TOOLS)
def test_a_board_in_recovery_is_used_before_a_com_port(rec, tool):
    """Opening the COM port would reset the board and close recovery: it is never touched."""
    (r, c), ports = rec([("recovery", in_recovery(), MAC_A), ("com", sim_with(), "RCV1")])
    code, out = run(tool, ports, "--show-password")
    assert code == 0 and cmds(r) and not cmds(c), out


@pytest.mark.parametrize("tool", TOOLS)
def test_a_named_com_port_is_used_as_named(rec, tool):
    (r, c), ports = rec([("recovery", in_recovery(), MAC_A), ("com", sim_with(), "RCV1")])
    code, out = run(tool, ports, "--port", c.path, "--show-password")
    assert code == 0 and cmds(c) and not cmds(r), out


@pytest.mark.parametrize("tool", TOOLS)
def test_an_espressif_port_that_does_not_answer_is_not_a_board_in_recovery(rec, tool):
    (_,), ports = rec([("rom", None, MAC_A)])
    code, out = run(tool, ports, "--show-password")
    assert code == 4 and "Hold its BOOT button for 8 seconds" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_the_rom_loader_is_never_opened(rec, tool):
    (j,), ports = rec([("jtag", in_recovery(), MAC_A)])
    code, out = run(tool, ports, "--show-password")
    assert code == 4 and not cmds(j), out


@pytest.mark.parametrize("tool", TOOLS)
def test_a_running_board_without_recovery_does_not_count(rec, tool):
    (n,), ports = rec([("recovery", sim_with(), MAC_A)])     # answers, but "recovery": false
    code, out = run(tool, ports, "--show-password")
    assert code == 4 and cmds(n) == ["status"], out


@pytest.mark.parametrize("tool", TOOLS)
def test_with_no_board_it_says_what_to_press(rec, tool):
    _, ports = rec([])
    code, out = run(tool, ports, "--show-password")
    assert code == 4 and "No COM cable?" in out and "Hold its BOOT button for 8 seconds" in out, out


@pytest.mark.parametrize("tool", TOOLS)
def test_two_boards_in_recovery_are_never_guessed_between(rec, tool):
    (a, b), ports = rec([("recovery", in_recovery(pin="4826"), MAC_A), ("recovery", in_recovery(pin="4826"), MAC_B)])
    code, out = run(tool, ports, "--reset-pin", "1357")
    assert code == 10 and a.sim.pin == b.sim.pin == "4826", out


@pytest.mark.parametrize("tool", TOOLS)
def test_the_mac_picks_one_board_in_recovery(rec, tool):
    (a, b), ports = rec([("recovery", in_recovery(), MAC_A), ("recovery", in_recovery(), MAC_B)])
    code, out = run(tool, ports, "--serial", MAC_B[:6], "--show-password")
    assert code == 0 and cmds(b) and "recovery_exit" not in cmds(a), out


def test_the_com_port_is_opened_with_its_lines_low_and_the_recovery_link_with_dtr_up(monkeypatch):
    """DTR on the COM cable is BOOT held; the recovery link answers only with DTR up."""
    import serial
    sys.path.insert(0, str(ROOT / "tools"))
    import recover
    seen = []

    class FakeSerial:
        def __init__(self):
            self.port = self.baudrate = self.timeout = None
            self.dtr = self.rts = None

        def open(self):
            seen.append((self.dtr, self.rts))

        def reset_input_buffer(self):
            pass

    monkeypatch.setattr(serial, "Serial", FakeSerial)
    monkeypatch.setenv("HIDFI_RECOVER_SETTLE", "0")
    recover.BoardLink("/dev/com", recovery=False)
    recover.BoardLink("/dev/rec", recovery=True)
    assert seen == [(False, False), (True, False)]
