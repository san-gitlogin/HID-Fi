"""Which board gets flashed when several are plugged in.

The selection logic (tools/flash_ports.py) is checked on every arrangement a
developer's desk can have, and then the real flash_esp.sh is run end to end
against fake ports and a stand-in esptool that records what it was asked to
write - so "which port, how many times, which files" is proven without a board.
"""
import json
import os
import select
import subprocess
import sys
import time

import pytest

from support import paths

pytestmark = pytest.mark.unit

ROOT = paths.TESTS.parent
sys.path.insert(0, str(ROOT / "tools"))
import flash_ports as F  # noqa: E402

HIDFI = dict(vid=0x1A86, pid=0x55D3)


def P(device, vid=0, pid=0, serial="", location="", description=""):
    return F.Port(device, vid, pid, serial, location, description)


def board(dev, serial, loc="0-1"):
    return P(dev, serial=serial, location=loc, description="USB Single Serial", **HIDFI)


ARDUINO = P("/dev/cu.usbmodem14201", 0x2341, 0x0043, "75834", "0-2", "Arduino Uno")
DEVKIT = P("/dev/cu.SLAB_USBtoUART", 0x10C4, 0xEA60, "0001", "0-3", "CP2102 USB to UART")
ROM = P("/dev/cu.usbmodem1101", 0x303A, 0x1001, "48:27:E2:00:11:22", "0-4", "USB JTAG/serial debug unit")
NOISE = [P("/dev/cu.Bluetooth-Incoming-Port"), P("/dev/cu.debug-console")]


# ---- the rules ---------------------------------------------------------------------
def test_one_board_is_used():
    t, say, code = F.choose(NOISE + [board("/dev/cu.usbmodem5CBC4099421", "5CBC409942")])
    assert code == 0 and [p.device for p in t] == ["/dev/cu.usbmodem5CBC4099421"]


def test_other_boards_on_the_desk_are_left_alone_and_named():
    t, say, code = F.choose([ARDUINO, DEVKIT, board("/dev/cu.wchusbserial5", "5A"), *NOISE])
    assert code == 0 and [p.device for p in t] == ["/dev/cu.wchusbserial5"]
    text = "\n".join(say)
    assert "Ignoring 2 other" in text and "Arduino Uno" in text and "CP210x" in text


@pytest.mark.parametrize("n", [2, 3, 5])
def test_several_hidfi_boards_are_never_guessed_between(n):
    bs = [board(f"/dev/cu.wchusbserial{i}", f"SN{i}", f"0-{i}") for i in range(n)]
    t, say, code = F.choose(bs + [ARDUINO])
    assert code == 10
    assert [p.device for p in t] == [b.device for b in bs]          # offered as candidates, in order
    assert sum(1 for l in say if l.strip()[:2] in {f"{i}." for i in range(1, n + 1)}) == n
    assert any("--serial" in l and "--all" in l for l in say)


def test_all_flashes_every_hidfi_board_and_nothing_else():
    bs = [board(f"/dev/ttyUSB{i}", f"SN{i}", f"1-{i}") for i in range(4)]
    t, _, code = F.choose(bs + [ARDUINO, DEVKIT, ROM], all_=True)
    assert code == 0 and [p.device for p in t] == [b.device for b in bs]


def test_all_with_no_hidfi_board_flashes_nothing():
    t, _, code = F.choose([ARDUINO, DEVKIT], all_=True)
    assert code == 4 and t == []


@pytest.mark.parametrize("serial,want,code", [
    ("5CBC409942", "/dev/cu.wchusbserialB", 0),    # whole
    ("5cbc", "/dev/cu.wchusbserialB", 0),          # a unique start, any case
    ("5", None, 10),                               # matches two
    ("ZZZ", None, 4),                              # not connected
])
def test_a_board_can_be_named_by_its_usb_serial_number(serial, want, code):
    bs = [board("/dev/cu.wchusbserialA", "58A1B2C3D4", "0-1"), board("/dev/cu.wchusbserialB", "5CBC409942", "0-2")]
    t, say, c = F.choose(bs, serial=serial)
    assert c == code
    assert [p.device for p in t] == ([want] if want else [])


def test_one_board_with_two_macos_names_is_one_board():
    # WCH's driver and Apple's both publish the same CH343.
    names = [board("/dev/cu.usbserial-5CBC409942", "5CBC409942"), board("/dev/cu.wchusbserial5CBC409942", "5CBC409942")]
    t, _, code = F.choose(names)
    assert code == 0 and [p.device for p in t] == ["/dev/cu.wchusbserial5CBC409942"]     # WCH's name preferred
    # and either name, given by hand, is accepted
    for n in names:
        assert F.choose(names, port=n.device)[2] == 0


def test_boards_without_serial_numbers_are_told_apart_by_where_they_are_plugged():
    bs = [board("/dev/ttyUSB0", "", "1-1.2"), board("/dev/ttyUSB1", "", "1-1.3")]
    assert F.choose(bs)[2] == 10


def test_a_named_port_that_is_not_a_hidfi_board_is_refused_unless_forced():
    t, say, code = F.choose([ARDUINO, board("/dev/cu.wchusbserial5", "5A")], port=ARDUINO.device)
    assert code == 11 and t == [] and any("--force-port" in l for l in say)
    t, say, code = F.choose([ARDUINO], port=ARDUINO.device, force=True)
    assert code == 0 and t[0].device == ARDUINO.device and any("forced" in l for l in say)


def test_a_named_port_that_is_gone_is_reported():
    t, say, code = F.choose([board("/dev/cu.wchusbserial5", "5A")], port="/dev/cu.wchusbserial9")
    assert code == 4 and "not connected" in say[0]


def test_a_board_stuck_in_its_rom_bootloader_is_pointed_out_and_can_be_named():
    t, say, code = F.choose([ROM])
    assert code == 4 and any(f"-p {ROM.device}" in l for l in say)
    t, say, code = F.choose([ROM], port=ROM.device)
    assert code == 0 and any("ROM bootloader" in l for l in say)


def test_nothing_plugged_in_says_what_to_check():
    t, say, code = F.choose(NOISE)
    assert code == 4 and any("CH343" in l for l in say) and any("data cable" in l for l in say)


def test_bluetooth_and_debug_consoles_are_never_boards():
    assert F.one_per_device(NOISE) == []


# ---- the command the shell reads ------------------------------------------------------
def _fake(tmp_path, ports):
    f = tmp_path / "ports.json"
    f.write_text(json.dumps([dict(device=p.device, vid=p.vid, pid=p.pid, serial=p.serial, location=p.location,
                                  description=p.description) for p in ports]), encoding="utf-8")
    return str(f)


def _helper(tmp_path, ports, *args, env=None):
    e = dict(os.environ, HIDFI_FAKE_PORTS=_fake(tmp_path, ports), **(env or {}))
    e.pop("HIDFI_PORT", None) if "HIDFI_PORT" not in (env or {}) else None
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "flash_ports.py"), *args], capture_output=True,
                       text=True, env=e, timeout=30)
    return r.stdout.splitlines()


def test_the_helper_output_ends_with_the_code(tmp_path):
    out = _helper(tmp_path, [board("/dev/ttyUSB0", "SN0")])
    assert out[-1] == "CODE 0" and "TARGET /dev/ttyUSB0 SN0" in out


def test_a_default_board_can_come_from_the_environment(tmp_path):
    bs = [board("/dev/ttyUSB0", "AAA1", "1-1"), board("/dev/ttyUSB1", "BBB2", "1-2")]
    out = _helper(tmp_path, bs, env={"HIDFI_SERIAL": "BBB"})
    assert "TARGET /dev/ttyUSB1 BBB2" in out and out[-1] == "CODE 0"


# ---- both flashers, end to end, with a stand-in esptool ---------------------------------
# flash_esp.sh on macOS/Linux, flash_esp.ps1 wherever PowerShell 7 is installed
# (Windows, or `pwsh` anywhere; HIDFI_PWSH names one that is not on the PATH).
import shutil  # noqa: E402

PWSH = os.environ.get("HIDFI_PWSH") or shutil.which("pwsh")
PS_FLAGS = {"--all": "-All", "-s": "-Serial", "-p": "-Port", "--force-port": "-ForcePort", "--which": "-Which",
            "-e": "-Erase", "-f": "-Force", "-n": "-NoVerify"}
FLASHERS = [
    pytest.param("sh", marks=pytest.mark.skipif(sys.platform == "win32", reason="flash_esp.sh is the macOS/Linux flasher")),
    pytest.param("ps1", marks=pytest.mark.skipif(not PWSH, reason="PowerShell 7 (pwsh) not installed - runs on Windows CI")),
]
needs_bash = pytest.mark.parametrize("flasher", FLASHERS)


@pytest.fixture
def rig(tmp_path, flasher):
    build = tmp_path / "build"
    build.mkdir()
    for f in ("hid_fi.ino.bin", "hid_fi.ino.bootloader.bin", "hid_fi.ino.partitions.bin"):
        (build / f).write_bytes(b"\0")
    (tmp_path / "boot_app0.bin").write_bytes(b"\0")
    log = tmp_path / "esptool.log"
    esptool = tmp_path / "esptool"
    # Records every call; fails for a port listed in FAIL_PORT, as a board in a bad state would.
    esptool.write_text(f"""#!/bin/sh
echo "$@" >> "{log}"
case " $* " in *" $FAIL_PORT "*) [ -n "$FAIL_PORT" ] && exit 2 ;; esac
exit 0
""", encoding="utf-8")
    esptool.chmod(0o755)
    if sys.platform == "win32":                 # the same stand-in, as Windows runs it
        esptool = tmp_path / "esptool.cmd"
        esptool.write_text(f"""@echo off\r
echo %*>>"{log}"\r
if "%FAIL_PORT%"=="" exit /b 0\r
echo %* | findstr /C:" %FAIL_PORT% " >nul && exit /b 2\r
exit /b 0\r
""", encoding="utf-8")

    def run(ports, *args, stdin=None, env=None, tty_input=None):
        e = dict(os.environ, HIDFI_FAKE_PORTS=_fake(tmp_path, ports), HIDFI_ESPTOOL=str(esptool),
                 HIDFI_BUILD_DIR=str(build), HIDFI_BOOT_APP0=str(tmp_path / "boot_app0.bin"), FAIL_PORT="")
        e.update(env or {})
        for k in ("HIDFI_PORT", "HIDFI_SERIAL", "CI"):
            if k not in (env or {}):
                e.pop(k, None)
        if flasher == "sh":
            cmd = [str(ROOT / "flash_esp.sh"), "-n", *args]
        else:
            cmd = [PWSH, "-NoProfile", "-File", str(ROOT / "flash_esp.ps1"), "-NoVerify",
                   *[PS_FLAGS.get(a, a) for a in args]]
        if tty_input is not None:
            return _run_in_tty(cmd, e, tty_input) + (_calls(log),)
        r = subprocess.run(cmd, capture_output=True, text=True, env=e, timeout=60, stdin=subprocess.DEVNULL)
        return r.returncode, r.stdout + r.stderr, _calls(log)
    return run


def _calls(log):
    if not log.exists():
        return []
    calls = [l.split() for l in log.read_text(encoding="utf-8").splitlines()]
    log.unlink()
    return calls


def _port_of(call):
    return call[call.index("--port") + 1]


def _run_in_tty(cmd, env, answer):
    """The script asks only when a person is there: give it a terminal, then answer."""
    pty = pytest.importorskip("pty", reason="a pseudo-terminal needs macOS or Linux")
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(cmd[0], cmd, env)       # noqa: the child becomes the flasher
    out, sent, end = b"", False, time.time() + 60
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            if not sent and b"Which one?" in out:
                os.write(fd, answer.encode() + b"\n")
                sent = True
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), out.decode(errors="replace")


@needs_bash
def test_script_flashes_the_one_board_with_the_four_partitions_only(rig):
    code, out, calls = rig([ARDUINO, board("/dev/cu.wchusbserial5", "5A")])
    assert code == 0, out
    assert len(calls) == 1 and _port_of(calls[0]) == "/dev/cu.wchusbserial5"
    args = calls[0]
    assert "write-flash" in args and [a for a in args if a.startswith("0x")] == ["0x0", "0x8000", "0xe000", "0x10000"]
    assert not any("merged" in a for a in args)


@needs_bash
def test_script_stops_without_flashing_when_it_would_have_to_guess(rig):
    code, out, calls = rig([board("/dev/ttyUSB0", "AAA", "1-1"), board("/dev/ttyUSB1", "BBB", "1-2")])
    assert code == 10 and calls == [] and "/dev/ttyUSB0" in out and "/dev/ttyUSB1" in out


@needs_bash
def test_script_flashes_them_all_and_reports_each(rig):
    bs = [board(f"/dev/ttyUSB{i}", f"S{i}", f"1-{i}") for i in range(3)]
    code, out, calls = rig(bs + [ARDUINO], "--all")
    assert code == 0, out
    assert [_port_of(c) for c in calls] == ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2"]
    assert out.count("ok      /dev/ttyUSB") == 3


@needs_bash
def test_one_failing_board_does_not_stop_the_others_and_fails_the_run(rig):
    bs = [board(f"/dev/ttyUSB{i}", f"S{i}", f"1-{i}") for i in range(3)]
    code, out, calls = rig(bs, "--all", env={"FAIL_PORT": "/dev/ttyUSB1"})
    assert code == 1
    assert [_port_of(c) for c in calls] == ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2"]
    assert "FAILED  /dev/ttyUSB1" in out and "ok      /dev/ttyUSB2" in out


@needs_bash
def test_script_picks_by_serial_number(rig):
    bs = [board("/dev/ttyUSB0", "AAA1", "1-1"), board("/dev/ttyUSB1", "BBB2", "1-2")]
    code, out, calls = rig(bs, "-s", "BBB")
    assert code == 0 and [_port_of(c) for c in calls] == ["/dev/ttyUSB1"]


@needs_bash
def test_script_refuses_a_port_that_is_not_a_hidfi_board(rig):
    code, out, calls = rig([ARDUINO], "-p", ARDUINO.device)
    assert code == 11 and calls == []
    code, out, calls = rig([ARDUINO], "-p", ARDUINO.device, "--force-port")
    assert code == 0 and [_port_of(c) for c in calls] == [ARDUINO.device]


@needs_bash
def test_which_only_says(rig):
    code, out, calls = rig([board("/dev/ttyUSB0", "AAA")], "--which")
    assert code == 0 and calls == [] and "Using /dev/ttyUSB0" in out


@needs_bash
def test_erasing_every_board_erases_each_before_writing_it(rig):
    bs = [board(f"/dev/ttyUSB{i}", f"S{i}", f"1-{i}") for i in range(2)]
    code, out, calls = rig(bs, "--all", "-e", "-f")
    assert code == 0, out
    assert [(c[c.index("--baud") + 2], _port_of(c)) for c in calls] == [
        ("erase-flash", "/dev/ttyUSB0"), ("write-flash", "/dev/ttyUSB0"),
        ("erase-flash", "/dev/ttyUSB1"), ("write-flash", "/dev/ttyUSB1")]


@needs_bash
@pytest.mark.parametrize("answer,want,code", [("2", ["/dev/ttyUSB1"], 0), ("a", ["/dev/ttyUSB0", "/dev/ttyUSB1"], 0),
                                              ("", [], 10), ("9", [], 10)])
def test_at_a_terminal_it_asks_which(rig, answer, want, code):
    bs = [board("/dev/ttyUSB0", "AAA", "1-1"), board("/dev/ttyUSB1", "BBB", "1-2")]
    c, out, calls = rig(bs, tty_input=answer)
    assert c == code, out
    assert [_port_of(x) for x in calls] == want


@needs_bash
def test_in_ci_it_never_waits_for_an_answer(rig):
    bs = [board("/dev/ttyUSB0", "AAA", "1-1"), board("/dev/ttyUSB1", "BBB", "1-2")]
    c, out, calls = rig(bs, tty_input="1", env={"CI": "true"})
    assert c == 10 and calls == []


@pytest.mark.skipif(sys.platform == "win32", reason="flash_esp.sh is the macOS/Linux flasher")
def test_without_pyserial_two_macos_names_of_one_board_are_still_one(tmp_path):
    awk = subprocess.run(["bash", "-c", "sed -n '/^legacy_ports()/,/^}/p' " + str(ROOT / "flash_esp.sh")],
                         capture_output=True, text=True).stdout
    prog = awk[awk.index("awk '") + 5:awk.rindex("'")]
    names = "/dev/cu.wchusbserial5CBC409942\n/dev/cu.usbserial-5CBC409942\n/dev/cu.usbmodem14201\n"
    r = subprocess.run(["awk", prog], input=names, capture_output=True, text=True)
    assert r.stdout.split() == ["/dev/cu.wchusbserial5CBC409942", "/dev/cu.usbmodem14201"]
