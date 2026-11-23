"""The end-to-end test page, opened and started by the harness itself - no person.

The board types into whatever has the keyboard focus on the computer its USB is
plugged into, so the test page must have it, for certain, before any keystroke - that
is what the old "click Start" was for. Here the harness proves it, three ways:

  the PAGE    reports its box focused (document.hasFocus() and the box active;
              support/sink.py). The browser is started as an ordinary app - its
              own throwaway profile, no remote control - because a browser driven
              through Playwright EMULATES focus: its page said "focused" while the
              keystrokes went to another app (seen 2026-11-23: a canary never arrived);
  the OS      reports this very browser process as the frontmost application
              (macOS NSWorkspace, read through JavaScript for Automation - no
              Accessibility permission involved), before every command;
  a CANARY    typed once must arrive exactly (tests/conftest.py), or the run errors.

It is started by a real click: the cursor is put on the page's window (only after
macOS shows that window, of this process, topmost under that point) and the BOARD
clicks - a genuine user gesture, so the page focuses its box and locks the pointer.

macOS only (the owner's test machine); elsewhere tests/conftest.py keeps the manual
page. HIDFI_E2E_MANUAL=1 forces the manual page anywhere.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time


def _jxa(script, timeout=10):
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", script], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def frontmost_pid():
    """The process id of the application macOS says is in front (has the keyboard)."""
    out = _jxa("ObjC.import('AppKit'); $.NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier")
    try:
        return int(out)
    except ValueError:
        return None


def activate(pid):
    """Bring the application with this process id to the front, all its windows."""
    _jxa("ObjC.import('AppKit'); var a = $.NSRunningApplication.runningApplicationWithProcessIdentifier(%d);"
         " a.activateWithOptions($.NSApplicationActivateAllWindows | $.NSApplicationActivateIgnoringOtherApps);" % pid)


def windows():
    """On-screen normal windows, front to back: [{pid, x, y, w, h}] (top-left origin)."""
    out = _jxa("ObjC.import('CoreGraphics'); var l = ObjC.deepUnwrap(ObjC.castRefToObject("
               "$.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements, 0)));"
               "JSON.stringify(l.filter(w=>w.kCGWindowLayer==0).map(w=>({pid:w.kCGWindowOwnerPID,"
               "x:w.kCGWindowBounds.X,y:w.kCGWindowBounds.Y,w:w.kCGWindowBounds.Width,h:w.kCGWindowBounds.Height})))")
    try:
        return json.loads(out)
    except ValueError:
        return []


def topmost_at(x, y):
    """The process owning the frontmost normal window under this point, or None."""
    for w in windows():
        if w["x"] <= x < w["x"] + w["w"] and w["y"] <= y < w["y"] + w["h"]:
            return w["pid"]
    return None


def warp(x, y):
    _jxa("ObjC.import('CoreGraphics'); $.CGWarpMouseCursorPosition({x:%d, y:%d});" % (x, y))


def chromium_path():
    """The Chromium Playwright installed - used here as a plain app, never driven."""
    r = subprocess.run([sys.executable, "-c", "from playwright.sync_api import sync_playwright\n"
                        "with sync_playwright() as p: print(p.chromium.executable_path)"],
                       capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


class AutoPage:
    """A plain browser window showing the test page in unattended mode, in front."""

    def __init__(self, url, sink, timeout=30):
        exe = chromium_path()
        if not exe or not os.path.exists(exe):
            raise RuntimeError("no Chromium installed (python -m playwright install chromium)")
        self.profile = tempfile.mkdtemp(prefix="hidfi-e2e-")
        self.proc = subprocess.Popen(
            [exe, f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check",
             "--disable-features=Translate,AutofillServerCommunication", "--disable-session-crashed-bubble",
             "--window-position=60,60", "--window-size=1100,820", "--new-window", url + "?auto=1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.pid = self.proc.pid
        end = time.time() + timeout
        while time.time() < end and not (sink.info().get("seq") or 0):
            time.sleep(0.2)                       # the page is loaded once it reports
        if not (sink.info().get("seq") or 0):
            self.close()
            raise RuntimeError("the page never loaded")
        self.point = None
        while time.time() < end:
            activate(self.pid)
            time.sleep(0.5)
            mine = [w for w in windows() if w["pid"] == self.pid and w["w"] > 400 and w["h"] > 300]
            if mine and frontmost_pid() == self.pid:
                w = mine[0]
                self.point = (int(w["x"] + w["w"] / 2), int(w["y"] + w["h"] * 0.6))
                if topmost_at(*self.point) == self.pid:
                    break
            self.point = None
        if not self.point:
            self.close()
            raise RuntimeError("the page's window never came to the front")

    def aim(self):
        """Put the cursor on the page's window; True only if, right then, macOS shows
        this browser in front and its window topmost under the cursor."""
        warp(*self.point)
        time.sleep(0.2)
        return self.in_front() and topmost_at(*self.point) == self.pid

    def in_front(self):
        return frontmost_pid() == self.pid

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(10)
        except Exception:
            self.proc.kill()
        shutil.rmtree(getattr(self, "profile", ""), ignore_errors=True)
