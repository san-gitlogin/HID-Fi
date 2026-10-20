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
| `flash_esp.ps1` / `flash_esp.sh` | Build, flash and verify; auto-detects board and toolchain |
| `tests/run.py` | **The** test command - every tier, a pass/fail/skip table. Guide: `docs/TESTING.md` |
| `docs/` | Hardware, flashing, command reference, architecture |

## Build and flash

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force   # if scripts are blocked
.\flash_esp.ps1 -Compile
python tests\run.py            # no board needed - run before saying "done"
python tests\run.py --all      # with a board on COM + USB - ask the user first: it moves the mouse and types into a test page
```

The Linux-only `hostsim` tier: on Linux or in your coding-agent sandbox,
`tests/setup_linux.sh` then `python3 tests/run.py` (the coding agent gets this
from `.github/workflows/copilot-setup-steps.yml`); on Windows the same inside
WSL; on macOS `tests/docker/run.sh`. Do not install Docker, WSL or a VM without
asking. Setup from
nothing, step by step: AGENTS.md, "Setting up and running tests, as an agent".

A skipped tier is not a passed tier - report the skips. Never edit `tests/golden/`
to make a failure pass; `--update-golden` is for intended changes, with a reason.

Adding or changing a feature (`.claude/skills/hidfi-feature/SKILL.md` has the steps):
a new firmware command is documented in `docs/COMMANDS.md`, modelled in
`tests/support/board_sim.py` and sent by a test - static rules fail otherwise. A
new dashboard control or behaviour gets a scenario in
`tests/ui/bdd/features/<tab>.feature`; the ui tier ends with a **coverage
contract** that fails if any control is untouched, any function never runs, or
less of the dashboard script runs than `tests/golden/ui_js_coverage.json`. A bug
you find and do not fix becomes a scenario tagged `@known_issue:<key>` (strict
xfail) plus a line in `docs/TESTING.md#known-issues` - never a deleted test.
No byte >= 0x80 may reach `Keyboard.press()` (the core reads it as a raw HID
usage) - text goes through `typeString()`; see AGENTS.md "Text typing".

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

## Ownership and recovery - do not weaken

- The board's own WiFi password is the proof of ownership. Only the serial-only
  command `ap_password` returns it. Never put it in any WiFi reply.
- Over WiFi, the first access PIN requires `ap_password` in `set_auth`.
- Recovery of a forgotten access PIN or WiFi password happens only over the USB
  COM cable: `tools/recover.py`, `flash_esp.sh --recover`, `flash_esp.ps1 -Recover`.
  Do not add a WiFi or button-based reset.
- A PIN reset without the old PIN erases the vault unless a vault PIN is set; the
  recovery tools must warn and require a typed ERASE first.
- Wrong-PIN lockout is per device (`pinGuards`), with a home-network budget that
  never applies to access-point clients or serial.
- Say "access PIN" or "vault PIN", never just "PIN", in user-facing text.
- If you flash or recover a board for the user, tell them the board's WiFi name
  and password once (the flasher's last "Dashboard: join WiFi ..." line). Say it
  is a one-time display: write it down now; `./flash_esp.sh --recover` or
  `.\flash_esp.ps1 -Recover` shows it again over USB. Then do not store it
  anywhere or repeat it. Never suggest a shared weak password such as `hid12345`.
  This is the only secret you may show - never an access PIN, vault PIN or vault
  secret.
- Full rules: AGENTS.md, "Ownership, PINs and recovery". Tests that guard them:
  `tests/ui/bdd/features/ownership.feature`, `tests/unit/test_recover.py`,
  `tests/board/test_ownership_hw.py`.

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
