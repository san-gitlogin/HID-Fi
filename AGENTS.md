# AI agent instructions — ESP32-S3 USB-HID Remote

This file is the shared brief for any AI assistant working in this repository.
`.github/copilot-instructions.md` and `.claude/CLAUDE.md` both point here.

Read it before changing anything. Most of it exists because something went wrong
once.

---

## What this project is

An ESP32-S3 board that plugs into a PC over USB, presents itself as a **USB HID
keyboard, mouse, media remote and gamepad**, and serves a phone dashboard over its
own WiFi access point. The host needs no drivers and no software.

Two things follow from that and shape every decision:

1. **HID is one way.** The board can never read the PC's state back. Anything the
   dashboard displays about the host is what we *believe* we set.
2. **Latency is the product.** A laggy cursor makes the whole thing feel broken.
   Nothing on an input hot path may allocate, log, or wait for a reply.

---

## Layout

```
flash_esp.ps1                  build + flash + verify, auto-detects everything
usb_hid_unlock/
  usb_hid_unlock.ino           all firmware: transports, dispatch, HID, NVS
  web_ui.h                     the entire dashboard as one PROGMEM string
  unlock_client.py             CLI for the board over serial or WiFi
tests/
  test_v34_features.py         24 checks, reversible only, safe on a live desktop
  test_lock_state.py           lock-state machine
  test_mouse_led.py            REQ/ACK protocol; sends REAL clicks without --skip-clicks
docs/
  HARDWARE.md  FLASHING.md  COMMANDS.md  ARCHITECTURE.md
```

**`web_ui.h` is the dashboard.** HTML, CSS, JavaScript and an SVG icon sprite, in
one C raw string literal delimited `R"rawliteral( ... )rawliteral"`. It is
compiled into the firmware, so any UI change needs a reflash.

---

## Before you change code

**Read the file first.** `usb_hid_unlock.ino` is ~2,400 lines and `web_ui.h` is
~2,000. Both have real structure and repeated patterns. Grep for the handler or
the CSS block, read the surrounding code, then edit. Do not infer a function's
behaviour from its name.

**Do not trust documentation over code.** If a doc and the source disagree, the
source wins and the doc needs fixing.

---

## Flashing

```powershell
.\flash_esp.ps1 -Compile        # build then flash
.\flash_esp.ps1                 # flash the existing build
.\flash_esp.ps1 -Compile -Port COM7 -All -Erase -NoVerify
```

Exit codes: `0` ok, `1` flash failed, `2` compile failed, `3` no image, `4` no
board, `5` no esptool, `10` ambiguous (more than one board — ask the user which).

The FQBN, if you ever need it directly:

```
esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=default,PSRAM=opi,FlashSize=16M,UploadSpeed=921600,PartitionScheme=huge_app
```

### Rules that are not negotiable

- **Never edit a sketch file while `arduino-cli` is reading it.** It produces
  nonsense errors like `fatal error: WiFi.h: No such file or directory`. Wait for
  the build to finish.
- **Never flash `merged.bin` at `0x0`.** It is a full 16 MB image and it wipes
  NVS — WiFi credentials, PIN, macros, knobs, saved PC passwords. The script
  writes four partitions and leaves NVS alone. Keep it that way.
- A build takes a couple of minutes. Poll for the result; do not assume failure.

---

## Testing

Always run these after a firmware change:

```powershell
python tests\test_dashboard_static.py  # no board needed, run before flashing
python tests\test_v34_features.py      # expect 24/24
python tests\test_power_health.py      # expect 34/34
python tests\test_wifi_join.py         # expect 20/20, ~40s
python tests\test_memory_soak.py       # ~1 min, proves there is no leak
```

They are safe on a live desktop and restore anything they touch.

`test_wifi_join.py` is the regression test for the blocking-join bug. It pings
throughout a join and fails if latency spikes, so it is what stops that class of
bug coming back. Needs no credentials &mdash; it joins an SSID that does not exist.

**Still manual, because they need a client on the board's AP:** a successful
join, the `lan_locked` gate, `/api/scan`, PIN lockout engaging, and the dashboard
end to end.

**`test_mouse_led.py` without `--skip-clicks` sends real clicks** into whatever is
focused. Do not run it casually.

### Check the dashboard without flashing

`tests/test_dashboard_static.py` does this automatically: it extracts the PROGMEM
string, runs `node --check` over the script, and verifies every `$('id')` and
every literal icon reference resolves. **A JavaScript syntax error gives a blank
white dashboard**, and the only way to diagnose that after flashing is to reflash.
This check has caught real bugs before they reached the board.

---

## Adding a feature

The pattern that works here:

1. **Find the truth first.** Read the relevant firmware handler *and* the
   dashboard code. If it touches USB or the board, check the vendor guide
   (`ESP32-S3-N16R8_User_Guide.txt`) or the ESP32 core source under
   `%LOCALAPPDATA%\Arduino15\packages\esp32`. Several "bugs" here turned out to be
   documented hardware behaviour.
2. **Firmware side.** Add a handler, declare it in the prototypes block near the
   top, and add a `strcmp` branch in `processCommand`. One JSON object per
   command, via `sendJson()`.
3. **Dashboard side.** Add markup in the right `<section class="view">`, styles in
   the matching CSS block, and wiring in `bindAll()`. Icons go in the SVG sprite.
4. **Validate before flashing** — syntax check, tag balance, icon references.
5. **Flash, run the suite, then verify the new thing specifically**, ideally with
   a throwaway probe script over serial that leaves the board as it found it.
6. **Update the docs** in the same change.

### Constraints that will bite you

- **Nothing in `loop()` may block.** This has been broken twice, both times by a
  WiFi call that looked harmless: `connectWifi()` waited up to 15 s and
  `WiFi.scanNetworks()` waited 2–4 s. `loop()` services the socket, the web server
  and the pointer stream, so blocking there freezes the dashboard and can drop the
  connection. Both are now state machines (`updateStaJoin()`, async scan polled
  over `/api/scan`). If a new feature needs to wait, make it a state machine and
  report progress through status fields the dashboard can poll.
- **Only one mouse object may ever exist.** `USBHIDMouseBase` registers its
  descriptor in its constructor behind a class-wide `static bool`. Relative and
  absolute cannot coexist; the mouse is heap allocated at boot and switching
  reboots.
- **`mapKeyName()` is the allowlist.** Any shortcut or combo must resolve there or
  it silently does nothing — `press` does not report unknown keys. Check before
  adding entries to the shortcuts table.
- **The serial command contract is an API.** Other tools drive boards over serial.
  Adding commands is safe; changing the shape of `unlock`, `lock`, `status`,
  `ping` or `set_lock_state` is not.
- **Never add a way to read a saved PC password back.** `pc_list` returns names
  only; `unlock` takes a slot number. See `SECURITY.md`.
- **Route any new authenticated transport through `pinCheck()`**, or the PIN
  lockout can be bypassed.
- **Do not weaken the exposure gate.** On a home network with no PIN set, commands
  from outside the softAP subnet are refused with `lan_locked`. Only read-only
  commands and `set_auth` are exempt. Radio range is a real limit; a subnet is not.
- **Bound anything arriving from outside before copying it** — `WS_TEXT_MAX`,
  `SERIAL_LINE_MAX`.
- **Do not add CPU frequency scaling.** It changes the clock the UART derives from,
  and serial is the recovery path. The radio is where the power actually goes.

---

## Dashboard conventions

- **No emoji, anywhere.** Every glyph is an SVG symbol in one sprite: 24×24,
  stroke 1.75, round caps and joins. New icons match that family.
- **Judge an icon at 16–24px**, not at design size. Detail that reads at 48px
  turns to mush in the tab bar.
- **Hot paths stay clean.** Pointer, knob and joystick traffic goes out as binary
  WebSocket frames — no JSON, no logging, no awaiting a reply. There is a
  `bufferedAmount` guard so a slow socket drops frames instead of queueing lag.
- **Anything held must be releasable.** Held modifiers and mouse buttons are
  released when the last WebSocket client disconnects, and on page hide. A stuck
  Alt makes the user's PC unusable.
- `contain: layout` makes an element a containing block for `position: fixed`
  descendants. It broke fullscreen once. Do not reintroduce it on `.card` or
  `.view`.

---

## Working style expected here

- **Verify, do not assume.** Read the source, the vendor guide, or probe the
  hardware. Several conclusions in this project came from reading the ESP32 core
  and finding it never calls a function it should.
- **Say what you did not verify.** If something could not be tested — because it
  needs a phone, a second machine, or a sleeping host — say so plainly rather
  than implying it was checked.
- **Clean up.** Throwaway probes and preview files get deleted. The repo should
  end each session with no scratch files.
- **Never leave the hardware in a changed state.** If a test sets a PIN or saves a
  profile, restore it afterwards and verify the restore.
- **Do not touch `.env` files or `vite.config.*`** if this project is ever
  vendored into a larger workspace that has them.

---

## Known hardware behaviour, already investigated

Do not re-diagnose these:

- **The RGB LED does nothing.** GPIO48 is not wired to the WS2812 until the board's
  "RGB" solder pads are bridged. Vendor documented. Not fixable in firmware, which
  is why the dashboard has no LED controls.
- **A sleeping host ignores all HID input.** The USB bus is suspended. The Arduino
  ESP32 layer tracks suspension but never calls `tud_remote_wakeup()`, so the
  firmware declares the TinyUSB symbols `extern "C"` and calls it directly. It
  still only works if the host armed the device as a wake source, which is why
  `system/wake` reports `was_suspended` and `remote_wakeup`.
- **The two USB-C ports are different.** COM is a CH343 serial bridge for
  flashing; USB is the native port that becomes the keyboard. Most "it does not
  work" reports are the wrong port or a charge-only cable.
