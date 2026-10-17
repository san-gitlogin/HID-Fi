"""The original serial suites, run unchanged against the board on COM.

Each script opens the port itself (which reboots the board), so the shared
session is released first. They are written to be reversible; the session's
snapshot/restore at the end of the run is the backstop that proves it.
"""
import subprocess
import sys

import pytest

from support import paths

pytestmark = pytest.mark.board

SUITES = [
    # (script, extra args, what it covers)
    ("test_v34_features.py", [], "pointer split, pan, media (volume down/up), latency"),
    ("test_power_health.py", [], "power modes, heap reporting, input bounds"),
    ("test_inventory.py", [], "MACs, stored settings, AP clients, ap_configure rejects"),
    ("test_secrets.py", [], "no command hands back a stored secret"),
    ("test_vault.py", [], "vault storage and its PIN gate (no typing)"),
    ("test_host_os.py", [], "OS detection settles; pin/auto round trip"),
    ("test_wifi_join.py", [], "join state machine; loop() never blocks"),
    ("test_mouse_led.py", ["--skip-clicks"], "mouse moves, wiggle, LED (no clicks)"),
]


@pytest.mark.parametrize("script,args,covers", SUITES, ids=[s[0][5:-3] for s in SUITES])
def test_serial_suite(board_released, script, args, covers):
    r = subprocess.run([sys.executable, str(paths.TESTS / script), board_released, *args],
                       capture_output=True, text=True, timeout=900)
    out = r.stdout + r.stderr
    # Suites that would destroy something real refuse up front and change nothing
    # (e.g. test_vault on a board with an access PIN). That is a precondition, not a failure.
    if "Refusing to run" in out:
        why = next((l.strip() for l in out.splitlines() if "Refusing to run" in l), "")
        pytest.skip(f"{script} refused (board in real use, nothing changed): {why[:160]}")
    fails = [l.strip() for l in out.splitlines() if "[FAIL]" in l]
    assert r.returncode == 0, f"{script} ({covers}) failed:\n  " + "\n  ".join(fails[:15] or out.splitlines()[-15:])


@pytest.mark.manual
def test_lock_state_needs_a_person():
    """test_lock_state.py really locks the computer and types its password to
    unlock it. Run it by hand: python tests/test_lock_state.py <port> --password ..."""
