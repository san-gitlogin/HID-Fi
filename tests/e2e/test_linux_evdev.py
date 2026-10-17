"""Linux, end to end, exact: the input events the kernel produces from the board.

Run on a Linux machine with the board's USB AND COM plugged into it:

    pip install evdev                       # plus tests/requirements.txt
    sudo -E python tests/run.py --e2e       # or add yourself to the 'input' group

Every one of the board's input devices is GRABBED before anything is sent, so
the desktop never sees these events: sleep, power, Ctrl+Alt+Del, Super+L,
F11, Esc, PrtSc all reach this test and nothing else. If any device cannot be
grabbed, the whole module is skipped - it never sends ungrabbed.

Expected codes are derived from the sources (support/linux_keys.py), not typed.
"""
import platform
import select
import time

import pytest

from support import linux_keys
from support.perf import pct, summary

pytestmark = [pytest.mark.linux, pytest.mark.e2e]

EV_KEY, EV_REL, EV_ABS = 1, 2, 3
REL_X, REL_Y, REL_HWHEEL, REL_WHEEL = 0, 1, 6, 8
BTN = {"left": 272, "right": 273, "middle": 274}
CTRL, SHIFT, ALT, GUI = 29, 42, 56, 125


class Grabbed:
    def __init__(self, devices):
        self.devs = devices

    def drain(self):
        for d in self.devs:
            try:
                while d.read_one() is not None:
                    pass
            except BlockingIOError:
                pass

    def collect(self, quiet=0.3, timeout=10):
        out, last, end = [], time.time(), time.time() + timeout
        fds = {d.fd: d for d in self.devs}
        while time.time() < end:
            r, _, _ = select.select(list(fds), [], [], 0.05)
            for fd in r:
                for e in fds[fd].read():
                    if e.type in (EV_KEY, EV_REL, EV_ABS):
                        out.append((e.type, e.code, e.value, e.timestamp()))
                        last = time.time()
            if time.time() - last >= quiet:
                break
        return out


@pytest.fixture(scope="module")
def grabbed(_board_holder):
    if platform.system() != "Linux":
        pytest.skip("Linux only")
    evdev = pytest.importorskip("evdev", reason="pip install evdev")
    board = _board_holder.get()
    board.wait_host()
    devs = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except PermissionError:
            pytest.skip("no permission to read /dev/input - run with sudo -E, or join the 'input' group")
        if d.info.vendor == 0x303A:
            devs.append(d)
    if not devs:
        pytest.skip("none of this machine's input devices is the board (VID 303a) - plug its USB port into THIS computer")
    grabbed = []
    try:
        for d in devs:
            d.grab()
            grabbed.append(d)
    except OSError as e:
        for d in grabbed:
            d.ungrab()
        pytest.skip(f"could not grab {d.path} ({e}) - refusing to send anything ungrabbed")
    g = Grabbed(devs)
    yield g
    board.cmd({"cmd": "key_release_all"})
    time.sleep(0.2)
    for d in devs:
        try:
            d.ungrab()
        except OSError:
            pass


def run(board, g, *cmds, quiet=0.3):
    g.drain()
    for c in cmds:
        board.cmd(c, timeout=30)
    return g.collect(quiet=quiet)


def keys(events):
    return [(c, v) for t, c, v, _ in events if t == EV_KEY and v in (0, 1)]


def no_stuck_keys(events):
    state = {}
    for t, c, v, _ in events:
        if t == EV_KEY:
            state[c] = v
    return [c for c, v in state.items() if v]


# ---- keyboard ----------------------------------------------------------------------------------
def test_every_key_name_arrives_as_its_linux_key(board, grabbed):
    expect = linux_keys.evdev_for_names()
    wrong = []
    for name, code in expect.items():
        ev = keys(run(board, grabbed, {"cmd": "key_down", "key": name}, {"cmd": "key_up", "key": name}, quiet=0.15))
        if (code, 1) not in ev or (code, 0) not in ev:
            wrong.append(f"{name}: want {code}, got {ev}")
    assert not wrong, "\n".join(wrong)


def test_every_printable_character_types_as_the_right_key(board, grabbed):
    want = linux_keys.ascii_to_evdev()
    text = "".join(want)
    ev = run(board, grabbed, {"cmd": "type", "text": text}, quiet=0.5)
    downs = [(c, v) for c, v in keys(ev)]
    shift = False
    got = []
    for c, v in downs:
        if c in (SHIFT, 54):
            shift = bool(v)
        elif v == 1:
            got.append((c, shift))
    assert got == [want[ch] for ch in text], "typed key sequence differs from the en_US table"
    assert not no_stuck_keys(ev)


@pytest.mark.parametrize("combo,codes", [
    ("CTRL+ALT+DELETE", {CTRL, ALT, 111}), ("GUI+L", {GUI, 38}), ("ALT+F4", {ALT, 62}),
    ("CTRL+SHIFT+ESC", {CTRL, SHIFT, 1}), ("GUI+D", {GUI, 32}),
])
def test_dangerous_combos_arrive_whole_and_release(board, grabbed, combo, codes):
    ev = run(board, grabbed, {"cmd": "press", "keys": combo})
    down = {c for c, v in keys(ev) if v == 1}
    assert codes <= down, f"{combo}: pressed {down}"
    assert not no_stuck_keys(ev), f"{combo} left keys held"


def test_media_keys(board, grabbed):
    wrong = []
    for name, usage in linux_keys.media_names().items():
        want = linux_keys.CONSUMER.get(usage)
        ev = keys(run(board, grabbed, {"cmd": "media", "key": name}, quiet=0.2))
        if (want, 1) not in ev or (want, 0) not in ev:
            wrong.append(f"{name}: want {linux_keys.EV_NAMES.get(want, want)}, got {ev}")
    assert not wrong, "\n".join(wrong)


@pytest.mark.parametrize("action", ["sleep", "power_off", "wake"])
def test_system_keys_reach_only_this_test(board, grabbed, action):
    ev = keys(run(board, grabbed, {"cmd": "system", "action": action}))
    want = linux_keys.SYSTEM[action]
    assert (want, 1) in ev and (want, 0) in ev, ev


GESTURES_PC = {"switch_app": {ALT, 15}, "task_view": {GUI, 15}, "desktop_left": {CTRL, GUI, 105},
               "desktop_right": {CTRL, GUI, 106}, "show_desktop": {GUI, 32}, "back": {ALT, 105},
               "forward": {ALT, 106}, "close_tab": {CTRL, 17}, "new_tab": {CTRL, 20},
               "copy": {CTRL, 46}, "paste": {CTRL, 47}, "undo": {CTRL, 44}}


@pytest.mark.parametrize("gesture", sorted(GESTURES_PC))
def test_gestures_send_the_pc_shortcut(board, grabbed, gesture):
    if board.status().get("host_os") == "mac":
        pytest.skip("board believes this is a Mac - pin linux or auto")
    ev = run(board, grabbed, {"cmd": "gesture", "name": gesture})
    down = {c for c, v in keys(ev) if v == 1}
    assert GESTURES_PC[gesture] <= down, f"{gesture}: {down}"
    assert not no_stuck_keys(ev)


@pytest.mark.parametrize("action,code", [("next", 106), ("prev", 105), ("start", 63), ("end", 1), ("black", 48), ("white", 17)])
def test_presenter(board, grabbed, action, code):
    ev = keys(run(board, grabbed, {"cmd": "presenter", "action": action}))
    assert (code, 1) in ev, ev


def test_lock_sends_super_l_and_unlock_types_the_password(board, grabbed):
    try:
        board.cmd({"cmd": "set_lock_state", "state": "unknown"})
        ev = run(board, grabbed, {"cmd": "lock"})
        assert {GUI, 38} <= {c for c, v in keys(ev) if v == 1}
        ev = run(board, grabbed, {"cmd": "unlock", "password": "Ab1!"}, quiet=1.0)
        want = [linux_keys.ascii_to_evdev()[ch][0] for ch in "Ab1!"]
        pressed = [c for c, v in keys(ev) if v == 1 and c not in (SHIFT, 54)]
        # The wake step may press other keys first; the password must then
        # appear in order, and Enter after it.
        it = iter(enumerate(pressed))
        positions = [next((i for i, c in it if c == w), None) for w in want]
        assert None not in positions, f"password keys {want} not typed in order: {pressed}"
        assert 28 in pressed[positions[-1] + 1:], "unlock must press Enter after the password"
    finally:
        board.cmd({"cmd": "set_lock_state", "state": "unknown"})


def test_unicode_entry_sequence_on_linux(board, grabbed):
    st = board.status()
    keep = (st["host_os"] if st["host_os_source"] == "manual" else "auto", st["unicode_linux"])
    try:
        board.cmd({"cmd": "set_host_os", "os": "linux"}, "host_os_set")
        board.cmd({"cmd": "set_unicode", "os": "linux", "on": True}, "unicode_set")
        ev = run(board, grabbed, {"cmd": "type", "text": "é"})
        k = keys(ev)
        assert (CTRL, 1) in k and (SHIFT, 1) in k and (22, 1) in k, "Ctrl+Shift+U missing"
        assert (57, 1) in k, "commit Space missing"
        assert not no_stuck_keys(ev)
    finally:
        board.cmd({"cmd": "set_unicode", "os": "linux", "on": keep[1]}, "unicode_set")
        board.cmd({"cmd": "set_host_os", "os": keep[0]}, "host_os_set")


# ---- pointer ------------------------------------------------------------------------------------
def rel(events, code):
    return sum(v for t, c, v, _ in events if t == EV_REL and c == code)


@pytest.mark.parametrize("dx,dy", [(1, 0), (0, -1), (127, 127), (400, -300), (-999, 0), (2000, 1500)])
def test_relative_motion_is_exact(board, grabbed, dx, dy):
    ev = run(board, grabbed, {"cmd": "mouse_move", "dx": dx, "dy": dy})
    assert (rel(ev, REL_X), rel(ev, REL_Y)) == (dx, dy)
    run(board, grabbed, {"cmd": "mouse_move", "dx": -dx, "dy": -dy})


@pytest.mark.parametrize("amount,pan", [(3, 0), (-3, 0), (0, 2), (0, -2), (1, 1)])
def test_scroll_and_pan_are_exact(board, grabbed, amount, pan):
    ev = run(board, grabbed, {"cmd": "mouse_scroll", "amount": amount, "pan": pan})
    assert abs(rel(ev, REL_WHEEL)) == abs(amount) and abs(rel(ev, REL_HWHEEL)) == abs(pan), ev


@pytest.mark.parametrize("button", ["left", "right", "middle"])
def test_buttons(board, grabbed, button):
    ev = keys(run(board, grabbed, {"cmd": "mouse_click", "button": button}))
    assert (BTN[button], 1) in ev and (BTN[button], 0) in ev


def test_click_with_modifier(board, grabbed):
    ev = keys(run(board, grabbed, {"cmd": "mouse_click", "button": "left", "modifiers": "CTRL"}))
    assert (CTRL, 1) in ev and (BTN["left"], 1) in ev and (CTRL, 0) in ev


def test_drag(board, grabbed):
    ev = run(board, grabbed, {"cmd": "mouse_press", "button": "left"}, {"cmd": "mouse_move", "dx": 50, "dy": 0},
             {"cmd": "mouse_move", "dx": -50, "dy": 0}, {"cmd": "mouse_release", "button": "left"})
    k = keys(ev)
    assert k[0] == (BTN["left"], 1) and k[-1] == (BTN["left"], 0) and rel(ev, REL_X) == 0


def test_wiggle_is_net_zero(board, grabbed):
    ev = run(board, grabbed, {"cmd": "wiggle", "mode": "single", "amplitude": 8}, quiet=0.6)
    assert [e for e in ev if e[0] == EV_REL], "wiggle moved nothing"
    assert (rel(ev, REL_X), rel(ev, REL_Y)) == (0, 0)


# ---- latency, from the kernel's own timestamps ---------------------------------------------------
@pytest.mark.perf
def test_key_latency_kernel_timestamps(board, grabbed, perf):
    lat = []
    for _ in range(50):
        grabbed.drain()
        t0 = time.time()
        board.cmd({"cmd": "key_down", "key": "q"}, "key_down")
        ev = grabbed.collect(quiet=0.05, timeout=2)
        board.cmd({"cmd": "key_up", "key": "q"}, "key_up")
        grabbed.collect(quiet=0.05, timeout=2)
        downs = [ts for t, c, v, ts in ev if t == EV_KEY and v == 1]
        if downs:
            lat.append((downs[0] - t0) * 1000)
    assert len(lat) >= 45
    perf.check("linux_key_latency_p95", pct(lat, 95), "ms", 40, detail=summary(lat))


@pytest.mark.perf
def test_pointer_latency_kernel_timestamps(board, grabbed, perf):
    lat = []
    for i in range(50):
        grabbed.drain()
        t0 = time.time()
        board.cmd({"cmd": "mouse_move", "dx": 1 if i % 2 else -1, "dy": 0})
        ev = grabbed.collect(quiet=0.05, timeout=2)
        mv = [ts for t, c, v, ts in ev if t == EV_REL]
        if mv:
            lat.append((mv[0] - t0) * 1000)
    assert len(lat) >= 45
    perf.check("linux_pointer_latency_p95", pct(lat, 95), "ms", 40, detail=summary(lat))
