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
flash_esp.ps1                  build + flash + verify (Windows), auto-detects everything
flash_esp.sh                   the same for macOS / Linux
hid_fi/
  hid_fi.ino           all firmware: transports, dispatch, HID, NVS
  web_ui.h                     the entire dashboard as one PROGMEM string
  unlock_client.py             CLI for the board over serial or WiFi
tests/
  test_v34_features.py         24 checks, reversible only, safe on a live desktop
  test_secrets.py              proves no command hands a stored secret back
  test_vault.py                the password vault and its PIN gate
  test_lock_state.py           lock-state machine
  test_mouse_led.py            REQ/ACK protocol; sends REAL clicks without --skip-clicks
docs/
  HARDWARE.md  FLASHING.md  MACOS.md  COMMANDS.md  ARCHITECTURE.md
```

**`web_ui.h` is the dashboard.** HTML, CSS, JavaScript and an SVG icon sprite, in
one C raw string literal delimited `R"rawliteral( ... )rawliteral"`. It is
compiled into the firmware, so any UI change needs a reflash.

---

## Before you change code

**Read the file first.** `hid_fi.ino` is ~3,600 lines and `web_ui.h` is
~3,900. Both have real structure and repeated patterns. Grep for the handler or
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

If PowerShell refuses with *"running scripts is disabled on this system"*, unblock
it **for the current shell only** — never machine-wide:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
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
  the build to finish. And never start a second build while one is running — two
  `arduino-cli` processes fight over the same build cache and neither finishes.
- **Never flash `merged.bin` at `0x0`.** It is a full 16 MB image and it wipes
  NVS — WiFi credentials, PIN, macros, knobs, saved PC passwords. The script
  writes four partitions and leaves NVS alone. Keep it that way.
- A build takes a couple of minutes. Poll for the result; do not assume failure.

### If `flash_esp.ps1` dies at "Uploading stub flasher"

The script sets `$ErrorActionPreference = 'Stop'`, and esptool 5.3.1 writes its
progress to **stderr**. PowerShell turns that into a terminating
`NativeCommandError` mid-flash, so the build succeeds and nothing is written.

Flashing over the CH343 can also fail with `Invalid head of packet (0x45)` —
`0x45` is ASCII `E`, the running firmware's own boot banner colliding with the
stub upload on the same UART.

The reliable route is the S3's **native USB-Serial-JTAG**, which has no
auto-reset circuit to race. It only enumerates while the chip is in the
bootloader, as a second `USB Serial Device (COMx)`:

```powershell
# 1. enter the bootloader and stay there -> COM14 appears
esptool --port COM13 --before default-reset --after no-reset --no-stub flash_id

# 2. write the four partitions over USB-Serial-JTAG
esptool --chip esp32s3 --port COM14 --before no-reset --after no-reset \
        write-flash --flash-mode dio --flash-freq 80m --flash-size 16MB \
        0x0 build/hid_fi.ino.bootloader.bin 0x8000 build/hid_fi.ino.partitions.bin \
        0xe000 boot_app0.bin 0x10000 build/hid_fi.ino.bin

# 3. hard reset through the CH343 so it runs the app
esptool --port COM13 --after hard_reset --no-stub flash_id
```

Step 3 matters: `--after hard-reset` on the JTAG port pulses an RTS line that
path does not have, so the board stays in the bootloader and no HID appears.

---

## Testing

Always run these after a firmware change:

```powershell
python tests\test_dashboard_static.py  # no board needed, run before flashing
python tests\test_dashboard_live.py    # no board needed, drives the UI over a WebSocket
python tests\test_v34_features.py      # expect 24/24
python tests\test_power_health.py      # expect 34/34
python tests\test_wifi_join.py         # expect 20/20, ~40s
python tests\test_memory_soak.py       # ~1 min, proves there is no leak
python tests\test_secrets.py           # no command leaks a secret
python tests\test_vault.py             # the vault gate holds — needs a board with
                                       # no PIN and an empty vault; it refuses otherwise
```

They are safe on a live desktop and restore anything they touch.

`test_wifi_join.py` is the regression test for the blocking-join bug. It pings
throughout a join and fails if latency spikes, so it is what stops that class of
bug coming back. Needs no credentials &mdash; it joins an SSID that does not exist.

`test_secrets.py` is the regression test for the disclosure rules in
`SECURITY.md`. It plants a marker password in a PC slot and a macro, then fails
if any listing, status or inventory reply contains it. It runs over **serial on
purpose**, because that is the transport with no PIN and no lockout.

`test_vault.py` proves the vault's gate: that nothing can be read, typed, edited
or deleted without the PIN, and that resetting the access PIN blind destroys
what it guarded. That last check erases the vault, so the test **refuses to run**
on a board that has a PIN set or entries stored. Do not defeat that guard — it
is the only thing standing between the suite and somebody's real passwords.

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

`tests/test_dashboard_live.py` goes further and **drives the real dashboard in a
headless browser against a fake board that speaks WebSocket and demands a PIN**.
That transport is where the reply queue lives, and a stub that only answers HTTP
cannot see it — which is exactly how a queue desync shipped. If you are changing
anything about `send()`, `waiters` or the auth handshake, this is the test that
will tell you.

---

## Talking to the board over serial — read this before writing a probe

**Opening the COM port resets the board.** The CH343's DTR and RTS lines drive
GPIO0 and EN through the auto-reset circuit, and pyserial asserts both on
`open()`. This is normal and every test in `tests/` accounts for it.

Always open the way the repo already does — set the lines **before** `open()`:

```python
ser = serial.Serial()
ser.port, ser.baudrate, ser.timeout = port, 115200, 2
ser.dtr = False          # GPIO0 high -> run the app, not the bootloader
ser.rts = False
ser.open()
```

`serial.Serial(port, 115200)` in one line does **not** do this and will reset the
board on every call.

### The failure this prevents

Opening and closing the port repeatedly in quick succession can land a reset with
GPIO0 still low, which latches the **ROM bootloader**. The board then looks
broken in a very confusing way: it is powered, the COM port is there, but no HID
works and the device list keeps changing.

**How to recognise it — look at the USB device list, not the serial output:**

| Symptom | Meaning |
|---|---|
| `USB JTAG/serial debug unit` present | The chip is in the ROM bootloader |
| A second `USB Serial Device (COMx)` appears | Bootloader CDC, not your firmware |
| `HID Keyboard Device` / `HID-compliant mouse` gone | Firmware is not running |
| `rst:0x15 (USB_UART_CHIP_RESET)` in the log | A DTR/RTS reset, i.e. something opened the port |
| `rst:0x1 (POWERON)` | A real power cycle |

```powershell
Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_303A|VID_1A86' } |
  Select-Object Status, Class, FriendlyName
```

A healthy board shows **six** entries: HID Keyboard, HID-compliant mouse,
consumer control, system controller, USB Input Device, and the CH343.

**Recovery**, in order of preference:

1. `esptool --port COM13 --after hard_reset --no-stub flash_id` — does the
   GPIO0-high / EN-pulse sequence correctly.
2. Unplug and replug the COM cable. A true power-on reset always samples GPIO0
   high.

And do not diagnose "the board is looping" from serial output alone — if you have
been opening the port, the resets are almost certainly yours. Observe the USB
device list instead, which requires touching nothing.

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
- **Nothing on the HID hot path may block either.** `usbWakeHost()` used to
  `delay(120)` on every report once a host suspended the bus. It is now rate
  limited and returns immediately; only the explicit `system/wake` command waits,
  because there the user asked for it and has nothing to feel lag in.
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
- **Never add a way to read a saved password back.** `pc_list` returns names and
  the slot's OS only; `unlock` takes a slot number. The **one** exception is the
  password vault's `vault_get`, and it is **PIN gated on every transport
  including serial** — serial is ungated so the board can be *recovered*, not
  read. That exception only holds because **changing or clearing the access PIN
  erases the vault** unless `old` proves you knew it, which is what stops
  `set_auth` over serial from being a way in. See `SECURITY.md`.
- **The vault has no session.** Reading a secret back and typing one each need
  the PIN in that request, on every transport. Do not add a "stay unlocked"
  convenience — typing a secret is indistinguishable from revealing it, because
  the board cannot see which window the keystrokes land in. Saving, renaming and
  deleting deliberately do **not** ask: they disclose nothing, and a PIN prompt
  in front of an act that cannot leak is a toll people learn to type blind.
- **Resetting a forgotten vault PIN with the access PIN erases the vault.**
  Without that, the access PIN would be a silent way to read everything and the
  separate vault PIN would be decorative.
- **A macro is not a secret store.** `macro_get` returns the body verbatim and is
  ungated over serial. Do not route secrets through it.
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
- **The keyboard is tiered, not one layout that stretches.** It grows by adding
  whole blocks as the measured container width allows, the way physical boards
  do: `compact` (12 columns, `123` symbol layer, under 500px) → `ansi` (500px) →
  `tkl` (1010px) → `full` (1300px, 104 keys with a numpad). Thresholds live in
  `KB_TIERS`.
  - **Every tier must expose every key the tier below it can reach.** Going *up*
    a tier may never take a key away. There was an `fn` tier at 790px once, so
    `Esc` and the function row existed on a phone, disappeared between 500px and
    790px, and returned on a laptop. The function row is part of `ansi` now, and
    the 65% side column carries `Del`/`Home`/`End` and `Ins`/`PgUp`/`PgDn`
    because the compact nav row does.
  - **Never hide a row that is the only way to reach its keys.** Compact may hide
    its digits row because the `123` layer still reaches the digits; the ANSI
    tiers may not, because they have no `123` layer. Hiding it there took the
    numbers away outright.
  - **Every block carries the same number of rows.** The blocks sit in a flex row
    and each divides the available height among its own rows, so a six-row nav
    cluster beside a five-row main block gets shorter rows and stops lining up.
    The row level with the function row is tagged `fn` in all three blocks — a
    spacer in the numpad and the nav cluster, the real thing in the main block —
    so hiding `.krow.fn` in the deck costs each block exactly one row.
  - **ANSI is a 60 column grid**, because ANSI is exactly 15u wide and that makes
    1u = 4 columns, so 1.25u = 5, 1.5u = 6, 1.75u = 7, 2u = 8, 2.25u = 9,
    2.75u = 11 and 6.25u = 25 are all whole numbers. Every ANSI row sums to 60.
    Key widths in the tables are in quarter-units.
  - **The blocks are flexed 15 : 3 : 4** (main : nav : numpad), which are their
    real proportions, so one key is the same width in all three. That makes the
    nav cluster narrow, so **its legends size from its own width** with a
    container query — a fixed font fitted a laptop and cut `Home` in half on a
    25px column. Mind the specificity: the per-tier rules are `(0,3,1)`.
  - **Compact keeps the slash beside the right Shift and Up above Down.** It was
    ten columns once and the arrows had to be a flat `← ↑ ↓ →` line, which nobody
    recognised as a keyboard.
  - `tests/test_dashboard_static.py` evaluates every layout table in node and
    fails if an ANSI row stops summing to 60, if the arrows lose the inverted T,
    if the slash leaves the right Shift, if the numpad cells overlap, or if any
    key name is missing from `mapKeyName()`.
  - `tests/test_responsive.py` sweeps 67 viewports and fails if a visible
    keyboard **cannot reach** the letters, digits, punctuation, arrows or
    modifiers, unioned across layers. That is the check that answers "can the
    user actually press everything on this screen", and it is the one to run
    after any layout change.
- **Never select a keyboard row by position.** `.krow:nth-child(2)` meant "the
  digits row" until the layout gained blocks, after which it silently deleted
  `Tab Q W E R T Y U I O P [ ] \` on every phone in landscape. Rows carry `fn`,
  `num` and `nav` classes; target those. `tests/test_responsive.py` now fails if a
  visible keyboard is missing any letter of the alphabet.
- **Test the trackpad deck in *both* modes.** In the card it is capped at `ansi`
  because it only gets the card's height; in fullscreen the cap is lifted and it
  reaches the full-size layout. Two different code paths, and the fullscreen one
  is easy to forget.
- **Nothing in the dashboard may need the Refresh button.** Every write to the
  board calls `syncAll()`, and `applyStatus()` rebuilds whatever a change to the
  access PIN affects, so a PIN set in Settings is visible in Keys immediately —
  and so is one set from another phone, because status is polled. A card that
  only updates when its own tab reloads is a bug.
- **The WebSocket reply queue is positional, so keep it 1:1.** Replies come back
  in the order the commands were sent and `waiters` matches them by position.
  Every command must push exactly one slot — `send()` pushes `null` when there is
  no callback — and every reply must consume exactly one, refusals included.
  Messages carrying `event` are pushed by the board and must consume none.
  Getting this wrong does not fail loudly: it silently delivers each reply to
  the previous command's handler for the rest of the socket's life. It shipped
  once as an empty Keys tab on a board that was holding the user's passwords.
  `tests/test_dashboard_live.py` is the regression test and needs no board.
- **A socket's handlers belong to that socket, not to `ws`.** `connect()` keeps
  the new socket in a local and every handler checks `ws === sock` before doing
  anything, so a socket that is still closing cannot write over the state of the
  one that replaced it. `connect()` also returns early while a socket is
  `CONNECTING` or `OPEN`, because reconnect timers, the visibility handler and
  the watchdog can all fire at once. Reading the shared `ws` from inside a
  handler is how you get two live sockets and a board that thinks two dashboards
  are connected.
- **A list row is icon, text, then controls, and the text is what gives way.**
  `.item .tx b` truncates and the buttons carry `flex:0 0 auto`. A saved email
  address used to push Type, Show and Edit into each other on a 375px phone.
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

- **Host OS detection is passive and must stay that way.** It used to tap Num
  Lock and time the LED reply; that mutated state on the user's PC, the "no
  reply" branch never undid the toggle it had caused, and an 800 ms window
  misread a slow host as a Mac — measured on one Windows laptop as 5 s to resolve
  on one boot and still undecided 9 s into the next. **Do not reintroduce a probe
  that types something.**
- **`SET_IDLE` does NOT separate macOS from Windows.** v3.7 assumed it did;
  measured on a real Mac, macOS sends it too. It is still counted as evidence a
  host is there, but it decides nothing.
- **The discriminator is the shape of the host's string-descriptor reads.** macOS
  asks for each string twice — two bytes to learn the length, then the whole
  thing — so the same index arrives back to back, string after string. Windows
  does it for the odd string too (measured on Windows 11: 11 requests, 2
  repeats), so the verdict is a **ratio**, not a count — a plain "two repeats
  means Mac" rule called this development PC a Mac. Same signal as QMK's
  `os_detection`, which reads it off the setup packet's `wLength`; the Arduino
  layer does not expose `wLength`, so the back-to-back repeat stands in. Reading
  it means the firmware defines `tud_descriptor_string_cb()` itself — the core's
  copy is `weak`, and its string table is file-static, so the descriptor is
  rebuilt from the `USB` object's own manufacturer/product/serial. **Keep those
  three coming from `USB`**: hardcode them and the host sees a different device.
  `status` carries `usb_str_seq`, the raw indices asked for, to settle arguments.
- **A host that says nothing is reported as `undetermined`, not guessed at.**
  Windows caches string descriptors per device, so a PC that already knows this
  board can be almost silent on a replug. Guessing from silence is exactly what
  made v3.7 call every Mac a PC.
- **A settled verdict is not revised by later traffic** — only a new enumeration
  replaces it. A Caps Lock LED report or a Device Manager refresh is not the host
  changing its mind.
- **Do not use the MS OS string descriptor (string index `0xEE`) to detect
  Windows.** It looks perfect — only Windows requests it — but Windows caches the
  result per VID/PID/bcdDevice under
  `HKLM\SYSTEM\CurrentControlSet\Control\usbflags` and never asks again. Verified
  on a development PC where this board was already recorded `osvc=00 00`. It would
  work once on a fresh machine and then make that machine look like a Mac forever.
- **Detection cannot tell Linux from Windows.** They enumerate identically. Linux
  reads as `windows` until pinned by hand. That is a known limit, not a bug.
- **A pinned OS survives moving to another computer.** Deliberate — the user's
  choice wins — but it is how a board set up on a Mac ends up holding Command on a
  PC. `status` carries `host_os_detected` alongside `host_os` so the dashboard can
  say the two disagree.
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
