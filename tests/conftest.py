"""Shared fixtures and the safety rules for every tier.

Hardware tiers are opt-in. A test whose prerequisites are missing is SKIPPED with
the reason, never silently passed. The board gets one serial session for the
whole run (opening the port reboots it), and whatever settings tests touch are
snapshotted first and restored at the end.
"""
import importlib.util
import json
import os
import platform
import shutil
import sys
from pathlib import Path

import webbrowser

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from support import paths  # noqa: E402

# The original standalone scripts live at tests/test_*.py and run on import, so
# pytest must not collect them directly - the tier wrappers run them instead.
collect_ignore_glob = ["test_*.py", "_*.py", ".build/*", ".reports/*"]


def pytest_addoption(parser):
    g = parser.getgroup("hidfi")
    g.addoption("--board", action="store_true", help="run tests that talk to a board on its COM port")
    g.addoption("--e2e", action="store_true", help="also type/move/click on this computer, into the test page")
    g.addoption("--perf", action="store_true", help="run performance and soak tests")
    g.addoption("--port", default=None, help="COM port of the board (default: auto-detect)")
    g.addoption("--sink", default="local", choices=("local", "remote"),
                help="local: the test page opens on this computer; remote: open the printed URL on the computer the board's USB is plugged into")
    g.addoption("--allow-input-source-switch", action="store_true",
                help="macOS: let the Unicode tests add and select 'Unicode Hex Input', then restore")
    g.addoption("--update-golden", action="store_true", help="rewrite golden files from the current code (review the diff!)")
    g.addoption("--strict-deps", action="store_true", help="fail, instead of warn, when installed versions differ from tests/deps.json")
    g.addoption("--soak-minutes", type=float, default=2.0, help="duration of the perf soak test")
    g.addoption("--board-url", default=os.environ.get("HIDFI_BOARD_URL"),
                help="the board's WebSocket, e.g. ws://192.168.4.1:81 - this machine must be on the board's network (or set HIDFI_BOARD_URL)")
    g.addoption("--board-pin", default=os.environ.get("HIDFI_PIN"),
                help="access PIN, if one is set - prefer the HIDFI_PIN environment variable; it is never stored or printed")


# ---- what each tier needs ----------------------------------------------------------
def _have_playwright():
    if importlib.util.find_spec("playwright") is None:
        return "playwright not installed (pip install -r tests/requirements.txt && python -m playwright install chromium)"
    return None


def _have_cli():
    return None if shutil.which("arduino-cli") else "arduino-cli not on PATH (see docs/FLASHING.md)"


def _have_cxx():
    return None if (shutil.which("clang++") or shutil.which("g++")) else "no C++ compiler (install Xcode CLT, build-essential, or MinGW)"


TIER_ORDER = ("perf", "linux", "e2e", "board", "hostsim", "build", "ui", "unit", "static", "manual")


# tryfirst: pytest-xdist turns the xdist_group marker into the item's group in its
# own collection hook, so the marker has to be on the item before that runs -
# otherwise the compile tiers spread across workers and race on tests/.build.
@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    reasons = {
        "ui": _have_playwright(),
        "build": _have_cli(),
        "unit": _have_cxx(),
    }
    # A test with no tier is never selected by run.py (it runs `-m <tiers>`):
    # unit/test_companion.py's 36 pairing tests and unit/test_host_detect.py
    # sat outside every run that way until v4.0.2. Refuse, loudly.
    untiered = sorted({i.nodeid.split("::")[0] for i in items
                       if not {m.name for m in i.iter_markers()} & set(TIER_ORDER)})
    if untiered:
        raise pytest.UsageError("tests with no tier marker (add pytestmark = pytest.mark.<tier>): " + ", ".join(untiered))
    for item in items:
        # Real markers only - item.keywords also holds parameter ids, so a test
        # parametrized with "linux" would be mistaken for a Linux-only test.
        kw = {m.name for m in item.iter_markers()}
        # Carried on every report, so run.py's summary knows the tier even when
        # the test ran on a pytest-xdist worker.
        item.user_properties.append(("tier", next((t for t in TIER_ORDER if t in kw), "other")))
        # Tiers that compile share tests/.build: under pytest-xdist they stay on
        # one worker, in order, while everything else spreads out.
        if kw & {"build", "unit", "hostsim"}:
            item.add_marker(pytest.mark.xdist_group("compile"))
        if "manual" in kw:
            item.add_marker(pytest.mark.skip(reason="manual: needs a person or a real secret - see docs/TESTING.md"))
            continue
        if "linux" in kw and platform.system() != "Linux":
            item.add_marker(pytest.mark.skip(reason="Linux-only (evdev) - run on a Linux machine with the board's USB plugged in"))
            continue
        for tier, why in reasons.items():
            if tier in kw and why:
                item.add_marker(pytest.mark.skip(reason=why))
        if "e2e" in kw and not config.getoption("--e2e"):
            item.add_marker(pytest.mark.skip(reason="types/moves on this computer - opt in with --e2e"))
        elif ("board" in kw or "e2e" in kw or "linux" in kw) and not (config.getoption("--board") or config.getoption("--e2e")):
            item.add_marker(pytest.mark.skip(reason="needs a board on COM - opt in with --board"))
        if "perf" in kw and not config.getoption("--perf"):
            item.add_marker(pytest.mark.skip(reason="performance test - opt in with --perf"))


# ---- the board ----------------------------------------------------------------------
class _BoardHolder:
    """Lazily opened, explicitly releasable, so legacy scripts can have the port."""
    def __init__(self, port):
        self.port, self.board, self.snap = port, None, None

    def get(self):
        from support.board import Board
        if self.board is None:
            self.board = Board(self.port).open()
            if self.snap is None:
                self.snap = self.board.snapshot()
        return self.board

    def release(self):
        if self.board is not None:
            self.board.close()
            self.board = None


@pytest.fixture(scope="session")
def board_port(request):
    from support.board import find_ports
    port = request.config.getoption("--port")
    if port:
        return port
    ports = find_ports()
    if not ports:
        pytest.skip("no board found on any COM port (CH343/CH340) - plug in the COM cable, or pass --port")
    if len(ports) > 1:
        pytest.skip(f"several boards found {ports} - pass --port")
    return ports[0]


@pytest.fixture(scope="session")
def _board_holder(board_port):
    h = _BoardHolder(board_port)
    yield h
    # Put back whatever the run changed, and say so.
    if h.snap is not None:
        b = h.get()
        drift = b.restore(h.snap)
        final = b.snapshot()
        h.release()
        paths.REPORTS.mkdir(parents=True, exist_ok=True)
        (paths.REPORTS / "board_restore.json").write_text(json.dumps({"restored": drift, "final": final}, indent=1))
        if final != h.snap:
            pytest.fail(f"board not left as found: before {h.snap} after {final}", pytrace=False)


@pytest.fixture
def board(_board_holder):
    """The shared serial session. Opens (one reboot) on first use."""
    return _board_holder.get()


@pytest.fixture
def board_released(_board_holder):
    """For legacy scripts that open the port themselves."""
    _board_holder.release()
    return _board_holder.port


# ---- golden files ---------------------------------------------------------------------
@pytest.fixture
def golden(request):
    """golden(name, data): compare with tests/golden/<name>.json, or rewrite it under --update-golden."""
    update = request.config.getoption("--update-golden")

    def _check(name, data):
        f = paths.GOLDEN / f"{name}.json"
        text = json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
        if update or not f.exists():
            paths.GOLDEN.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
            if not update:
                pytest.skip(f"created golden file {f.name} - commit it; it is the reference from now on")
            return
        want = json.loads(f.read_text(encoding="utf-8"))
        if want != data:
            raise AssertionError(_golden_diff(name, want, data))

    return _check


def _golden_diff(name, want, got):
    lines = [f"output differs from tests/golden/{name}.json. If the change is intended, rerun with "
             f"--update-golden and commit the diff; otherwise this is a regression."]
    if isinstance(want, dict) and isinstance(got, dict):
        for k in sorted(set(want) | set(got)):
            if want.get(k) != got.get(k):
                lines.append(f"  {k}:\n    golden {json.dumps(want.get(k), ensure_ascii=False)[:300]}\n    now    {json.dumps(got.get(k), ensure_ascii=False)[:300]}")
                if len(lines) > 12:
                    lines.append("  ...")
                    break
    return "\n".join(lines)


@pytest.fixture(scope="session")
def perf(request):
    from support.perf import Perf
    p = Perf(update=request.config.getoption("--update-golden"))
    yield p
    p.save()


# ---- the end-to-end test page (shared by e2e and perf) ---------------------------------
def _os_from_page(info):
    p = (info.get("platform") or "") + " " + (info.get("ua") or "")
    if "Mac" in p:
        return "mac"
    if "Win" in p:
        return "windows"
    if "Linux" in p or "X11" in p:
        return "linux"
    return "unknown"


@pytest.fixture(scope="session")
def sink(request, _board_holder):
    from support.sink import Sink
    remote = request.config.getoption("--sink") == "remote"
    board = _board_holder.get()
    st = board.wait_host()
    # HIDFI_SINK_PORT fixes the port, so the address can be given to the person at
    # the other computer before the run starts.
    s = Sink(port=int(os.environ.get("HIDFI_SINK_PORT") or 0), bind="0.0.0.0" if remote else "127.0.0.1")
    url = s.url(remote=remote)
    tw = request.config.get_terminal_writer()
    tw.line("")
    tw.line("=" * 72, yellow=True)
    if remote:
        tw.line(f"  Open {url} in a browser ON THE COMPUTER THE BOARD'S USB IS PLUGGED INTO,", yellow=True)
    else:
        tw.line(f"  Opening {url} in your browser.", yellow=True)
        webbrowser.open(url)
    tw.line("  Click Start, then leave that window in front. Do not touch the keyboard", yellow=True)
    tw.line("  or mouse until the run finishes - the board types and moves into that page.", yellow=True)
    tw.line("=" * 72, yellow=True)
    if not s.wait_ready(timeout=240):
        s.close()
        pytest.skip("the test page was not started within 4 minutes (open it and click Start)")
    s.host_os = _os_from_page(s.info()) if remote else \
        {"Darwin": "mac", "Windows": "windows", "Linux": "linux"}.get(platform.system(), "unknown")
    s.board_view = st
    yield s
    s.close()


@pytest.fixture
def host(sink):
    return sink.host_os


@pytest.fixture
def drive(board, sink):
    """drive(cmd, want=None): send one command, but only while the page has focus."""
    def _drive(obj, want=None, timeout=60):
        sink.assert_focused()
        return board.cmd(obj, want, timeout=timeout)
    return _drive


@pytest.fixture(scope="session")
def host_os_name():
    return {"Darwin": "mac", "Windows": "windows", "Linux": "linux"}.get(platform.system(), "unknown")


# ---- the dashboard coverage contract ------------------------------------------------------
# Every BDD scenario records which controls it touched and which dashboard
# functions ran (tests/ui/bdd/conftest.py writes one file per process). When the
# whole BDD suite ran, they are merged here - on the xdist controller too - and
# checked against tests/golden. A control nobody touches, or a new function that
# never runs, fails the session. See docs/TESTING.md#the-dashboard-coverage-contract.
def pytest_sessionstart(session):
    if not hasattr(session.config, "workerinput"):
        from support import responsive, ui_contract
        ui_contract.reset()
        responsive.reset()


def pytest_runtest_logreport(report):
    f = report.nodeid.split("::")[0].replace("\\", "/")
    if report.when == "call" and "ui/bdd/test_" in f:
        _bdd_ran.add(f.rsplit("/", 1)[-1])


_bdd_ran = set()


def pytest_sessionfinish(session, exitstatus):
    if hasattr(session.config, "workerinput"):
        return
    from support import responsive, ui_contract
    tr = session.config.pluginmanager.get_plugin("terminalreporter")
    for verdict in (ui_contract.check(session.config, _bdd_ran), responsive.finish(session.config)):
        if verdict is None:
            continue
        for line in verdict["lines"]:
            tr.write_line(line) if tr else print(line)
        if verdict["failed"] and session.exitstatus == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
