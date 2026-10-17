#!/usr/bin/env python3
"""Run under the SYSTEM python (python3-gi, python3-xlib from the distro).

    replay.py <cases.json> <hid_keyboard.json> <out.json>

Starts nothing itself: expects DISPLAY to point at a running X server with an
input method already configured by the caller. Opens a GTK text box, and for
each case replays the firmware's HID reports exactly the way the kernel's
hid-input driver turns them into key events (modifier bits as usages
0xE0-0xE7, then array releases, then array presses), injected via XTEST with
X keycode = evdev code + 8. Records what the text box ends up holding.
"""
import json
import sys
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402
from Xlib import X, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

cases = json.load(open(sys.argv[1]))
TABLE = json.load(open(sys.argv[2]))
OUT = sys.argv[3]

win = Gtk.Window(title="hidfi hostsim")
win.set_default_size(800, 300)
tv = Gtk.TextView()
buf = tv.get_buffer()
win.add(tv)
state = {"text": "", "focused": False}


def on_change(*_):
    s, e = buf.get_bounds()
    state["text"] = buf.get_text(s, e, True)


buf.connect("changed", on_change)


def tick():
    gw = win.get_window()
    if gw:
        gw.focus(Gdk.CURRENT_TIME)
    state["focused"] = tv.has_focus() and win.is_active()
    return True


def clear():
    buf.set_text("")
    return False


win.connect("destroy", Gtk.main_quit)
win.show_all()
tv.grab_focus()
GLib.timeout_add(100, tick)


class Kbd:
    def __init__(self):
        self.d = display.Display()
        self.mods, self.keys = 0, set()

    def _ev(self, usage, down):
        code = TABLE[usage]
        if not code:
            raise SystemExit(f"usage 0x{usage:02x} has no Linux key")
        xtest.fake_input(self.d, X.KeyPress if down else X.KeyRelease, code + 8)

    def report(self, mods, keys):
        ks = set(k for k in keys if k)
        for b in range(8):
            if (mods ^ self.mods) & (1 << b):
                self._ev(0xE0 + b, bool(mods & (1 << b)))
        for k in sorted(self.keys - ks):
            self._ev(k, False)
        for k in [k for k in keys if k and k not in self.keys]:
            self._ev(k, True)
        self.mods, self.keys = mods, ks
        self.d.sync()


def settle():
    last, same = None, 0
    while same < 6:
        time.sleep(0.05)
        cur = state["text"]
        same = same + 1 if cur == last else 0
        last = cur
    return last


def worker():
    k = Kbd()
    results = {}
    time.sleep(1.5)
    for c in cases:
        for _ in range(30):
            if state["focused"]:
                break
            time.sleep(0.1)
        if not state["focused"]:
            results[c["id"]] = {"error": "text box lost focus"}
            continue
        GLib.idle_add(clear)
        time.sleep(0.25)
        if c.get("caps"):
            k.report(0, [0x39]); k.report(0, []); time.sleep(0.15)
        for ev in c["ev"].split():
            if ev.startswith("R"):
                mods, rest = ev[1:].split(":")
                k.report(int(mods, 16), [int(x, 16) for x in rest.split(".")])
                time.sleep(0.01)
        got = settle()
        if c.get("caps"):
            k.report(0, [0x39]); k.report(0, []); time.sleep(0.15)
        results[c["id"]] = {"text": got}
    json.dump(results, open(OUT, "w"))
    GLib.idle_add(Gtk.main_quit)


threading.Thread(target=worker, daemon=True).start()
Gtk.main()
