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
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force   # if scripts are blocked
.\flash_esp.ps1 -Compile
python tests\test_dashboard_static.py  # no board needed
python tests\test_v34_features.py     # expect 24/24
```

## Never hammer the serial port

Opening the COM port **resets the board** — DTR and RTS drive GPIO0 and EN. Open
it the way `tests/` does, setting the lines before `open()`:

```python
ser = serial.Serial(); ser.port = port; ser.dtr = False; ser.rts = False; ser.open()
```

Repeated fast opens can latch the **ROM bootloader**, which looks like the board
looping or dying. Tell-tale: `USB JTAG/serial debug unit` appears in the USB
device list and the HID interfaces vanish. Recover with
`esptool --port COMx --after hard_reset --no-stub flash_id`, or unplug/replug.
Diagnose from the USB device list, not from serial output you are causing.

## The rules that matter most

- **Never edit a sketch file while `arduino-cli` is building.** It reports
  nonsense errors such as a missing `WiFi.h`.
- **Never flash `merged.bin` at `0x0`** — it wipes NVS and every saved setting.
- **Check the dashboard JavaScript with `node --check` before flashing.** A syntax
  error produces a blank white page that can only be diagnosed by reflashing.
- **No emoji in the dashboard.** Icons are SVG symbols in one sprite, 24×24,
  stroke 1.75.
- **The keyboard is tiered.** It gains whole blocks with width: compact (<500px)
  → ANSI (500px) → +nav cluster (1010px) → +numpad (1300px, 104 keys). **Every
  tier must expose every key the tier below it reaches** — a wider screen may
  never offer fewer keys, and a row may only be hidden if its keys are reachable
  some other way. ANSI is a 60 column grid so
  1u = 4 columns and every row sums to 60. The static test fails if a row stops
  adding up, if the arrows lose the inverted T, or if a key name is missing from
  `mapKeyName()`.
- **Nothing on an input hot path allocates, logs, or awaits a reply.** Latency is
  the product.
- **`mapKeyName()` is the allowlist** for every key combo. Anything missing from
  it silently does nothing.
- **Do not add a way to read a saved PC password back.** The only reveals are
  the vault's `vault_get` and `vault_type`, PIN gated on *every* transport
  including serial, with no session. Saving and deleting are not gated because
  they disclose nothing. `macro_get` is ungated over serial, so secrets never
  belong in a macro. See `../SECURITY.md`.
- **The dashboard must never need its Refresh button.** Every board write calls
  `syncAll()`, and the status poll rebuilds what the access PIN affects.
- **Do not change the shape of the existing serial commands** — other tools depend
  on them.

## Already investigated, do not re-diagnose

- **Host OS detection is passive** — do not reintroduce the old Num Lock
  keystroke probe, and do not use the `0xEE` MS OS descriptor (Windows caches the
  answer in `usbflags` and stops asking). **`SET_IDLE` does not separate macOS
  from Windows** — v3.7 assumed it did and was wrong on a real Mac. The
  discriminator is the host's **string-descriptor reads**: macOS asks for the
  same index twice (2 bytes, then the whole string), Windows and Linux ask once.
  The firmware owns `tud_descriptor_string_cb()` to see it; the raw counts are in
  `status` and Settings → Computer.
- **Never select a keyboard row by position.** `:nth-child(2)` deleted the whole
  QWERTY row once the layout gained blocks. Rows carry `fn`, `num` and `nav`
  classes.
- The **RGB LED** needs the board's "RGB" solder pads bridged. Not a firmware bug.
- A **sleeping host** ignores all HID input; the firmware calls TinyUSB's
  `tud_remote_wakeup()` directly because the Arduino layer never does.
- The **two USB-C ports differ**: COM is the serial bridge for flashing, USB is the
  native port that becomes the keyboard.
