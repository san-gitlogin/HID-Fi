# GitHub Copilot instructions

The full brief for AI assistants in this repository is **[`../AGENTS.md`](../AGENTS.md)**.
Read it before making changes. What follows is the short version.

## What this is

An ESP32-S3 that plugs into a PC as a **USB HID keyboard, mouse, media remote and
gamepad**, and serves a phone dashboard from its own WiFi access point. No drivers
or software on the host.

## Where things live

| Path | What |
|---|---|
| `hid_fi/hid_fi.ino` | All firmware — transports, command dispatch, HID, NVS |
| `hid_fi/web_ui.h` | The **entire dashboard** as one PROGMEM raw string |
| `flash_esp.ps1` | Build, flash and verify; auto-detects board and toolchain |
| `tests/` | `test_v34_features.py` is the regression suite — run it after firmware changes |
| `docs/` | Hardware, flashing, command reference, architecture |

## Build and flash

```powershell
.\flash_esp.ps1 -Compile
python tests\test_v34_features.py     # expect 24/24
```

## The rules that matter most

- **Never edit a sketch file while `arduino-cli` is building.** It reports
  nonsense errors such as a missing `WiFi.h`.
- **Never flash `merged.bin` at `0x0`** — it wipes NVS and every saved setting.
- **Check the dashboard JavaScript with `node --check` before flashing.** A syntax
  error produces a blank white page that can only be diagnosed by reflashing.
- **No emoji in the dashboard.** Icons are SVG symbols in one sprite, 24×24,
  stroke 1.75.
- **Nothing on an input hot path allocates, logs, or awaits a reply.** Latency is
  the product.
- **`mapKeyName()` is the allowlist** for every key combo. Anything missing from
  it silently does nothing.
- **Do not add a way to read a saved PC password back out.** See `../SECURITY.md`.
- **Do not change the shape of the existing serial commands** — other tools depend
  on them.

## Already investigated, do not re-diagnose

- The **RGB LED** needs the board's "RGB" solder pads bridged. Not a firmware bug.
- A **sleeping host** ignores all HID input; the firmware calls TinyUSB's
  `tud_remote_wakeup()` directly because the Arduino layer never does.
- The **two USB-C ports differ**: COM is the serial bridge for flashing, USB is the
  native port that becomes the keyboard.
