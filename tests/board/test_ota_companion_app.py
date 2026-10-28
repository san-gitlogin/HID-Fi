"""The real Companion updating a real board - end to end.

The Companion's service (companion/crates/hidfi-companion, example system_app,
built with the feature "test-ota-key" so it trusts the TEST key) runs here with a
pretend USB bus naming the board, reads the signed test release from the
release server on this computer, pairs with the board (confirmed over USB, as a
person would on the dashboard), and then - driven the way the tray drives it -

    is offered 4.0.99 with its size and what changed, over the air
    updates over the air: check, download, "Restart now", self-test, kept
    cancels a download: nothing changes
    relays the release when the board cannot reach it
    updates by cable over the native USB port: the board restarts into its ROM
    loader on the Companion's signed request, the Companion finds that port by
    the board's MAC, writes, leaves the loader and watches the self-test

Afterwards the board gets its normal firmware back and the person's data is
compared by hash (the shared fixture in test_ota_download.py).
--board; slow.
"""
import contextlib
import json
import os
import pathlib
import shutil
import queue
import subprocess
import tempfile
import threading
import time

import pytest

from board.test_ota_download import _settle, env  # noqa: F401 - the shared fixture
from support import paths
from support.ota_board import install, wait_status

pytestmark = [pytest.mark.board, pytest.mark.slow]

COMPANION = paths.TESTS.parent / "companion"
APP = COMPANION / "target" / "release" / "examples" / "system_app"


@pytest.fixture(scope="module")
def built():
    r = subprocess.run(["cargo", "build", "--release", "-p", "hidfi-companion", "--features", "test-ota-key",
                        "--example", "system_app"], cwd=COMPANION, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        pytest.skip(f"could not build the Companion: {r.stderr[-800:]}")
    return APP


class App:
    """The Companion's service, read through its JSON lines, driven through stdin."""

    def __init__(self, exe, store, url, mac, releases, extra_env=None):
        envv = dict(os.environ, HIDFI_RELEASES=releases, HIDFI_TEST_NAME="OTA E2E PC", **(extra_env or {}))
        self.p = subprocess.Popen([str(exe), store, url, mac], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, env=envv)
        self.q, self.last, self.mac = queue.Queue(), {}, mac.replace(":", "").upper()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            try:
                self.q.put(json.loads(line))
            except ValueError:
                pass

    def say(self, s):
        self.p.stdin.write(s + "\n")
        self.p.stdin.flush()

    def wait(self, pred, secs, what):
        end = time.time() + secs
        while time.time() < end:
            try:
                self.last = self.q.get(timeout=1)
            except queue.Empty:
                pass
            if self.last and pred(self.last):
                return self.last
        raise AssertionError(f"{what} - not within {secs}s; last: {json.dumps(self.last)[:900]}")

    def upd(self, v=None):
        v = v or self.last
        return next((b for b in v.get("updates", {}).get("boards", []) if b["mac"] == self.mac), {})

    def quit(self):
        try:
            self.say("quit")
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()


def com_serial(port):
    """The CH343's own USB serial number for a COM port path."""
    from serial.tools import list_ports
    return next((p.serial_number for p in list_ports.comports() if p.device == port), None)


@contextlib.contextmanager
def companion(env, built, seed_com=False, extra_env=None):
    """The real Companion, paired with the board as a person would pair it.
    seed_com: its store already knows this board's COM port - what an earlier
    cable update over COM leaves behind."""
    board = env["holder"].get()
    st = board.status()
    store = tempfile.mkdtemp(prefix="hidfi-e2e-")
    if seed_com:
        sn = com_serial(env["holder"].port)
        assert sn, "the COM port's serial number"
        upd = pathlib.Path(store) / "updates"
        upd.mkdir()
        (upd / "com_ports.json").write_text(json.dumps({sn: st["mac"].replace(":", "").upper()}), encoding="utf-8")
    url = f"http://{st['wifi_ip']}"
    a = App(built, store, url, st["mac"], env["srv"].url_for(st["wifi_ip"]), extra_env)
    try:
        a.wait(lambda v: "WaitingForApproval" in v.get("session", ""), 60, "the Companion asking to pair")
        app_id = json.loads((pathlib.Path(store) / "state.json").read_text(encoding="utf-8"))["id"]
        assert board.cmd({"cmd": "companion_pair", "id": app_id}, "companion_pair").get("state") == "confirm"
        v = a.wait(lambda v: "ShowCode" in v.get("session", ""), 30, "the Companion showing the code")
        code = v["session"].split('"')[1]
        assert board.cmd({"cmd": "companion_pair", "id": app_id, "code": code}, "companion_pair").get("state") == "paired"
        a.wait(lambda v: v.get("session") == "Online", 30, "the Companion online")
        a.id = app_id
        a.store = store
        yield a
    finally:
        a.quit()
        if getattr(a, "id", None):
            env["holder"].get().cmd({"cmd": "companion_unpair", "id": a.id}, "companion_unpair")
        shutil.rmtree(store, ignore_errors=True)    # it holds a pairing key


@pytest.fixture
def app(env, built):  # noqa: F811
    with companion(env, built) as a:
        yield a


def offered(a):
    """The offer once paired: lines from before the pairing finished (offered,
    rightly, by cable only) are still in the queue and must not count."""
    return a.wait(lambda v: "over_air: true" in a.upd(v).get("offer", ""), 90, "the update offered over the air")


def test_the_companion_offers_the_update_with_size_and_changes(env, app):  # noqa: F811
    v = offered(app)
    o = app.upd(v)["offer"]
    assert 'to: "4.0.99"' in o and f"size: {len(env['img'])}" in o and "over_air: true" in o, o
    assert '"4.0.99"' in o and '"4.0.5"' in o, f"every section newer than what runs: {o}"
    assert v["updates"]["latest"] == "4.0.99"


def test_over_the_air_with_restart_now(env, app):  # noqa: F811
    offered(app)
    app.say(f"update {app.mac} air")
    app.wait(lambda v: app.upd(v).get("job") == "Ready", 600, "the update downloaded and ready")
    app.say(f"restart {app.mac}")
    v = app.wait(lambda v: app.upd(v).get("job", "").startswith(("Done", "RolledBack", "Failed")), 300, "the update kept")
    assert app.upd(v)["job"] == 'Done { to: "4.0.99" }', app.upd(v)
    st = env["holder"].get().status()
    assert st["firmware"] == "hid_fi_v4.0.99-test" and st["ota_state"] == "valid"


def test_a_cancelled_download_changes_nothing(env, app):  # noqa: F811
    board = env["holder"].get()
    if board.status()["firmware"] != "hid_fi_v4.0.4-test":
        install(env["holder"], env["base"])
        wait_status(env["holder"].get(), lambda s: s.get("ota_state") == "valid", 150, "the base build proving itself")
    offered(app)
    env["srv"].faults["chunk_delay"] = 0.05
    try:
        app.say(f"update {app.mac} air")
        app.wait(lambda v: "Downloading" in app.upd(v).get("job", ""), 180, "the download starting")
        app.say(f"cancel {app.mac}")
        v = app.wait(lambda v: app.upd(v).get("job") in ("Stopped",) or "Failed" in app.upd(v).get("job", ""), 120,
                     "the download stopping")
        assert app.upd(v)["job"] == "Stopped"
    finally:
        env["srv"].faults["chunk_delay"] = 0.0
    st = env["holder"].get().status()
    assert st["firmware"] == "hid_fi_v4.0.4-test" and st["ota_state"] == "valid" and st["ota_step"] == "idle"


def test_a_board_that_cannot_reach_the_release_gets_it_from_the_companion(env, app):  # noqa: F811
    board = env["holder"].get()
    _settle(board)
    srv_url = env["srv"].url_for(board.status()["wifi_ip"])
    board.cmd({"cmd": "ota_source", "url": srv_url + "nothing-here/"}, "ota_source")
    try:
        offered(app)
        n_before = len(env["srv"].clients)
        board_ip = board.status()["wifi_ip"]
        app.say(f"update {app.mac} air")
        app.wait(lambda v: app.upd(v).get("job") == "Ready", 600, "the relayed update downloaded and ready")
        asked = env["srv"].clients[n_before:]
        assert not any(ip == board_ip and code in (200, 206) for ip, _, code in asked), \
            f"the board got files from the server, not from the Companion's relay: {asked}"
        assert any(ip != board_ip and name == "hid_fi-4.0.99.bin" for ip, name, _ in asked), \
            "the Companion fetched the image itself, to relay it"
        app.say(f"cancel {app.mac}")
        app.wait(lambda v: app.upd(v).get("job") == "Stopped", 120, "the ready update withdrawn")
    finally:
        board.cmd({"cmd": "ota_source", "url": srv_url}, "ota_source")
    st = env["holder"].get().status()
    assert st["ota_state"] == "valid" and st["ota_step"] == "idle"


def _cable(a, env):
    """Update by cable; returns the steps the Companion showed on the way."""
    board = env["holder"].get()
    _settle(board)
    offered(a)
    # The COM port is the Companion's now (the COM path, and the rescue). The
    # harness opens it again only after the new firmware is kept: an open resets
    # the board, and a reset during the self-test would roll the update back.
    env["holder"].release()
    a.say(f"update {a.mac} cable")
    seen = set()

    def watch(v):
        j = a.upd(v).get("job", "")
        if j.startswith("Cable"):
            seen.add(j.split('step: "')[1].split('"')[0])
        return j.startswith(("Done", "RolledBack", "Failed", "Stopped"))
    v = a.wait(watch, 900, "the cable update finishing")
    assert a.upd(v)["job"] == 'Done { to: "4.0.99" }', (a.upd(v), seen)
    st = wait_status(env["holder"].get(), lambda s: s.get("ota_state") == "valid", 90, "the board settled")
    assert st["firmware"] == "hid_fi_v4.0.99-test"
    return seen


def _back_to_base(env):
    if env["holder"].get().status()["firmware"] != "hid_fi_v4.0.4-test":
        install(env["holder"], env["base"])
        wait_status(env["holder"].get(), lambda s: s.get("ota_state") == "valid", 150, "the base build proving itself")


def test_by_cable_over_the_native_usb_port_or_its_rescue(env, built):  # noqa: F811
    """Native first. Behind some hubs and extenders the ROM's USB never appears
    (09F7C8's does not); then the board's COM port resets it out of the loader and
    the update goes on there - never a board left waiting in the loader."""
    _back_to_base(env)
    with companion(env, built, seed_com=True, extra_env={"HIDFI_CABLE_PREFER": "native"}) as a:
        seen = _cable(a, env)
    assert "restarting into update mode" in seen, seen
    assert "writing the firmware" in seen, seen
    if any("over the COM port" in s for s in seen):
        print("native update mode did not appear on USB here - rescued over COM")


def test_by_cable_a_known_com_port_comes_first(env, built):  # noqa: F811
    _back_to_base(env)
    with companion(env, built, seed_com=True) as a:
        seen = _cable(a, env)
        upd = pathlib.Path(a.store) / "updates"
        learned = json.loads((upd / "com_ports.json").read_text(encoding="utf-8"))
        journal = json.loads(next(p for p in upd.glob("*.json") if p.name != "com_ports.json").read_text(encoding="utf-8"))
    assert "restarting into update mode" not in seen, f"COM, not native: {seen}"
    # Every checkpoint is in the journal (the image step may be a check, not a
    # write, when the slot already holds it - the progress line can be skipped).
    assert journal["step"] == "Restarted", journal["step"]
    assert [h.split(":")[0] for h in journal["history"]][:2] == ["Preflighted", "ImageWritten"], journal["history"]
    assert list(learned.values()) == [env["holder"].get().status()["mac"].replace(":", "").upper()]
