"""macOS keyboard input sources, for the Unicode end-to-end tests only.

Unicode entry on a Mac needs the "Unicode Hex Input" source selected. The tests
switch to it only when run with --allow-input-source-switch, and put back the
exact previous state: the same source selected, and Unicode Hex Input removed
again if it was not enabled before.
"""
import contextlib
import subprocess
import time

from support import paths

UNICODE_HEX = "com.apple.keylayout.UnicodeHexInput"
TIS_SWIFT = r"""
import Carbon
func all() -> [TISInputSource] {
    (TISCreateInputSourceList([kTISPropertyInputSourceIsSelectCapable as String: true] as CFDictionary, true)?.takeRetainedValue() as? [TISInputSource]) ?? []
}
func prop(_ s: TISInputSource, _ k: CFString) -> String {
    guard let p = TISGetInputSourceProperty(s, k) else { return "" }
    return Unmanaged<CFString>.fromOpaque(p).takeUnretainedValue() as String
}
func enabled(_ s: TISInputSource) -> Bool {
    guard let p = TISGetInputSourceProperty(s, kTISPropertyInputSourceIsEnabled) else { return false }
    return CFBooleanGetValue(Unmanaged<CFBoolean>.fromOpaque(p).takeUnretainedValue())
}
let a = CommandLine.arguments
func find(_ id: String) -> TISInputSource? { all().first { prop($0, kTISPropertyInputSourceID) == id } }
switch a[1] {
case "list": for s in all() where enabled(s) { print(prop(s, kTISPropertyInputSourceID)) }
case "current": print(prop(TISCopyCurrentKeyboardInputSource().takeRetainedValue(), kTISPropertyInputSourceID))
case "enable": guard let s = find(a[2]) else { exit(1) }; exit(TISEnableInputSource(s) == noErr ? 0 : 1)
case "select": guard let s = find(a[2]) else { exit(1) }; exit(TISSelectInputSource(s) == noErr ? 0 : 1)
case "disable": guard let s = find(a[2]) else { exit(1) }; exit(TISDisableInputSource(s) == noErr ? 0 : 1)
default: exit(2)
}
"""


def _tool():
    paths.CACHE.mkdir(parents=True, exist_ok=True)
    src = paths.CACHE / "tis.swift"
    exe = paths.CACHE / "tis"
    stale = not exe.exists() or not src.exists() or src.read_text() != TIS_SWIFT
    if stale:
        src.write_text(TIS_SWIFT)
        r = subprocess.run(["swiftc", "-O", "-o", str(exe), str(src)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("could not build the input-source helper (needs Xcode command line tools): " + r.stderr[-500:])
    return exe


def tis(*args):
    r = subprocess.run([str(_tool()), *args], capture_output=True, text=True)
    if r.returncode != 0 and args[0] not in ("list", "current"):
        raise RuntimeError(f"input source {' '.join(args)} failed")
    return r.stdout.strip()


@contextlib.contextmanager
def unicode_hex_input():
    before_current = tis("current")
    before_enabled = tis("list").split()
    try:
        if UNICODE_HEX not in before_enabled:
            tis("enable", UNICODE_HEX)
        tis("select", UNICODE_HEX)
        time.sleep(0.5)
        if tis("current") != UNICODE_HEX:
            raise RuntimeError("could not select Unicode Hex Input")
        yield
    finally:
        tis("select", before_current)
        if UNICODE_HEX not in before_enabled:
            tis("disable", UNICODE_HEX)
        time.sleep(0.3)
        assert tis("current") == before_current, "input source not restored"
        assert tis("list").split() == before_enabled, "enabled input sources not restored"
