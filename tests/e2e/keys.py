"""Which keys an end-to-end test may press on a live desktop, and what each must arrive as.

The browser test page blocks default actions, but some keys act before any page
sees them (Cmd+Q, Alt+F4, the Windows key, F11 fullscreen, PrtSc screenshots,
Esc leaving pointer lock). Those are only exercised on Linux through evdev, where
the board's devices are grabbed and the desktop never receives them.
"""

# firmware key name -> KeyboardEvent.code the browser must report
BASE = {
    **{c.upper(): f"Key{c.upper()}" for c in "abcdefghijklmnopqrstuvwxyz"},
    **{str(d): f"Digit{d}" for d in range(10)},
    "SPACE": "Space", "ENTER": "Enter", "BACKSPACE": "Backspace", "TAB": "Tab", "DELETE": "Delete",
    "HOME": "Home", "END": "End", "PAGEUP": "PageUp", "PAGEDOWN": "PageDown",
    "UP": "ArrowUp", "DOWN": "ArrowDown", "LEFT": "ArrowLeft", "RIGHT": "ArrowRight",
    "MINUS": "Minus", "EQUALS": "Equal",
    "[": "BracketLeft", "]": "BracketRight", ";": "Semicolon", "'": "Quote", ",": "Comma",
    ".": "Period", "/": "Slash", "\\": "Backslash", "`": "Backquote",
    "KP0": "Numpad0", "KP1": "Numpad1", "KP5": "Numpad5", "KP9": "Numpad9", "KPDOT": "NumpadDecimal",
    "KPPLUS": "NumpadAdd", "KPMINUS": "NumpadSubtract", "KPSTAR": "NumpadMultiply",
    "KPSLASH": "NumpadDivide", "KPENTER": "NumpadEnter",
    "SHIFT": "ShiftLeft", "CTRL": "ControlLeft", "RSHIFT": "ShiftRight", "RCTRL": "ControlRight",
}

PER_OS = {
    # Option/Alt alone is harmless on a Mac and Linux; on Windows it focuses the browser's menu.
    "mac": {"ALT": "AltLeft", "RALT": "AltRight", "GUI": "MetaLeft"},
    "linux": {"ALT": "AltLeft"},
    "windows": {},
}

# The modifier that drives text-editing shortcuts on each OS.
SHORTCUT_MOD = {"mac": "GUI", "windows": "CTRL", "linux": "CTRL"}
WORD_DELETE = {"mac": "ALT+BACKSPACE", "windows": "CTRL+BACKSPACE", "linux": "CTRL+BACKSPACE"}


def safe_keys(host_os):
    keys = dict(BASE)
    keys.update(PER_OS.get(host_os, {}))
    if host_os == "mac":
        keys.pop("KPENTER", None)     # macOS reports keypad Enter as "NumpadEnter" on some layouts, "Enter" on others
    return keys
