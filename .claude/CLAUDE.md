# CLAUDE.md

The full brief is **[`../AGENTS.md`](../AGENTS.md)**. Read it before changing
anything — most of it exists because something went wrong once.

## Project

ESP32-S3 that plugs into a PC as a **USB HID keyboard, mouse, media remote and
gamepad**, serving a phone dashboard from its own WiFi access point. The host
needs no drivers and no software, which is why it works on a lock screen and in a
BIOS.

## Key files

- `usb_hid_unlock/usb_hid_unlock.ino` — all firmware (~2,400 lines)
- `usb_hid_unlock/web_ui.h` — the whole dashboard as one PROGMEM raw string
  (~2,000 lines): HTML, CSS, JS and an SVG icon sprite
- `flash_esp.ps1` — build, flash, verify
- `tests/test_v34_features.py` — 24-check regression suite, safe on a live desktop

## Commands

```powershell
.\flash_esp.ps1 -Compile              # build and flash
python tests\test_v34_features.py     # expect 24/24
```

## Non-negotiables

1. **Read before editing.** Both main files are long and structured. Grep, read
   the surrounding code, then change it. Never infer behaviour from a name.
2. **Never edit a sketch file mid-build.** `arduino-cli` emits misleading errors.
3. **Never flash `merged.bin` at `0x0`.** It erases NVS and every saved setting.
   The script writes four partitions on purpose.
4. **Validate the dashboard before flashing.** Extract the PROGMEM string, run the
   script through `node --check`, confirm tag balance and icon references. A JS
   syntax error means a blank white page diagnosable only by reflashing.
5. **No emoji.** SVG sprite symbols only, 24×24, stroke 1.75. Judge new icons at
   16–24px, not at design size.
6. **Hot paths stay clean** — binary WebSocket frames, no JSON, no logging, no
   awaiting replies.
7. **Anything held must be releasable.** A stuck Alt or mouse button makes the
   user's PC unusable.
8. **`mapKeyName()` is the allowlist.** Combos missing from it fail silently.
9. **Never expose a saved PC password.** `pc_list` returns names only.
10. **Restore hardware state.** If a probe sets a PIN or saves a profile, undo it
    and verify the undo. Delete throwaway scripts when done.

## Expected working style

Verify rather than assume — read the ESP32 core source or the vendor guide when
behaviour is surprising. Several conclusions here came from discovering the core
never calls a function it should. State plainly what could not be tested and why,
rather than implying it was.
