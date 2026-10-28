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
import json
import os
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

    def __init__(self, exe, store, url, mac, releases):
        envv = dict(os.environ, HIDFI_RELEASES=releases, HIDFI_TEST_NAME="OTA E2E PC")
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


@pytest.fixture
def app(env, built):  # noqa: F811
    board = env["holder"].get()
    st = board.status()
    store = tempfile.mkdtemp(prefix="hidfi-e2e-")
    url = f"http://{st['wifi_ip']}"
    a = App(built, store, url, st["mac"], env["srv"].url_for(st["wifi_ip"]))
    try:
        a.wait(lambda v: "WaitingForApproval" in v.get("session", ""), 60, "the Companion asking to pair")
        app_id = json.loads((__import__("pathlib").Path(store) / "state.json").read_text())["id"]
        assert board.cmd({"cmd": "companion_pair", "id": app_id}, "companion_pair").get("state") == "confirm"
        v = a.wait(lambda v: "ShowCode" in v.get("session", ""), 30, "the Companion showing the code")
        code = v["session"].split('"')[1]
        assert board.cmd({"cmd": "companion_pair", "id": app_id, "code": code}, "companion_pair").get("state") == "paired"
        a.wait(lambda v: v.get("session") == "Online", 30, "the Companion online")
        a.id = app_id
        yield a
    finally:
        a.quit()
        if getattr(a, "id", None):
            env["holder"].get().cmd({"cmd": "companion_unpair", "id": a.id}, "companion_unpair")
        __import__("shutil").rmtree(store, ignore_errors=True)    # it holds a pairing key


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
        n_before = len(env["srv"].log)
        app.say(f"update {app.mac} air")
        app.wait(lambda v: app.upd(v).get("job") == "Ready", 600, "the relayed update downloaded and ready")
        assert not any(name == "hid_fi-4.0.99.bin" and code in (200, 206) for name, _, code, _ in env["srv"].log[n_before:]), \
            "the board took the image from the Companion's relay, not from the server"
        app.say(f"cancel {app.mac}")
        app.wait(lambda v: app.upd(v).get("job") == "Stopped", 120, "the ready update withdrawn")
    finally:
        board.cmd({"cmd": "ota_source", "url": srv_url}, "ota_source")
    st = env["holder"].get().status()
    assert st["ota_state"] == "valid" and st["ota_step"] == "idle"


def test_by_cable_over_the_native_usb_port(env, app):  # noqa: F811
    board = env["holder"].get()
    _settle(board)
    offered(app)
    app.say(f"update {app.mac} cable")
    seen = set()
    def watch(v):
        j = app.upd(v).get("job", "")
        if j.startswith("Cable"):
            seen.add(j.split('step: "')[1].split('"')[0])
        return j.startswith(("Done", "RolledBack", "Failed", "Stopped"))
    v = app.wait(watch, 600, "the cable update finishing")
    assert app.upd(v)["job"] == 'Done { to: "4.0.99" }', (app.upd(v), seen)
    assert "restarting into update mode" in seen and "writing the firmware" in seen, seen
    st = wait_status(env["holder"].get(), lambda s: s.get("ota_state") == "valid", 60, "the board settled")
    assert st["firmware"] == "hid_fi_v4.0.99-test"
