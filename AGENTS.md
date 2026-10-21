# AI agent instructions — ESP32-S3 USB-HID Remote

This file is the shared brief for any AI assistant working in this repository:
Codex, Copilot, Claude, Gemini, Cursor, Windsurf, Jules and the rest. The short
per-tool files all point here: `.github/copilot-instructions.md`,
`.claude/CLAUDE.md`, `GEMINI.md`.

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
tools/flash_ports.py           which port is a HID-Fi board, among several (used by flash_esp.sh)
tools/recover.py               USB-only recovery: WiFi password, access PIN (flash_esp.sh --recover)
tools/hidfi.py                 agent client: CLI and MCP server for do/open/steps (docs/AGENT_CONTROL.md)
hid_fi/
  hid_fi.ino           all firmware: transports, dispatch, HID, NVS
  web_ui.h                     the entire dashboard as one PROGMEM string
  unlock_client.py             CLI for the board over serial or WiFi
tests/
  run.py                       THE test command - picks tiers, runs pytest, prints the table
  conftest.py  pytest.ini      fixtures, tier markers, the safety rules
  deps.json                    versions HID-Fi is verified against (firmware deps are exact pins)
  support/                     board session, dashboard + fake board, e2e test page, corpus,
                               firmware facts, Linux key derivation, perf budgets
  static/ unit/ ui/ compile/ hostsim/ board/ e2e/ perf/    one folder per tier
  ui/bdd/                      the dashboard, one feature file per tab, + the coverage contract
  ui/responsive/               every state on 500+ screens, turning and resizing, layout speed
  hooks/                       git pre-commit / pre-push, and the Claude Code Stop hook
  golden/                      what "unchanged" means - never edit to make a failure pass
  test_*.py                    the original standalone suites; the tiers run them
                               (test_lock_state.py really locks the PC - manual only)
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
board, `5` no esptool, `10` ambiguous (more than one board — ask the user which),
`11` the named port is not a HID-Fi board.

**Several boards on one machine.** Both flashers pick only boards whose COM
connector is the CH343 (`1A86:55D3`), never guess between several, and never
flash another kind of device unless forced. Before flashing on a machine you do
not know, run `-Which` / `--which`: it shows the decision and opens no port. The
selection logic is `tools/flash_ports.py` (the PowerShell script mirrors it) and
`tests/unit/test_flash_ports.py` runs both scripts end to end on fake ports with a
stand-in esptool - change one, change both, and keep that test passing.

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

### Setting up and running tests, as an agent

Do these in order. Stop and ask the user at any step marked ASK.

1. `python tests/run.py doctor` - shows what is installed against what is pinned.
2. If test packages are missing: `pip install -r tests/requirements.txt` and
   `python -m playwright install chromium`. Prefer a virtualenv if the user has one.
3. `python tests/run.py` - every tier this machine can run, no board needed. This
   is the gate before saying "done". Read the table it prints; report skips with
   their reasons. In a sandbox or VM with under 8 GB of memory, set
   `HIDFI_WORKERS=2`: with one Chromium per CPU, browser crashes and timing
   failures appear that are not bugs - and a "Target crashed" failure is never
   evidence about the code until it reproduces with fewer workers.
4. The `hostsim` tier and the Linux layout baselines need Linux. Find out where
   you are (`uname -s`; on Windows, `wsl -l -v`) and take the matching route:
   - Linux, or a cloud sandbox (Codex, Copilot, Jules - these are Linux):
     `tests/setup_linux.sh`, then `python3 tests/run.py`. You may run it there
     without asking when it is your own sandbox; on a user's machine it uses
     sudo - ASK.
   - Windows: the tiers run in WSL. If `wsl -l -v` lists an Ubuntu, run
     `tests/setup_linux.sh` and `python3 tests/run.py` inside it, in a clone
     made inside WSL (not under /mnt/c). No WSL: ASK - installing it needs an
     administrator and a restart.
   - macOS with Docker running: `tests/docker/run.sh`. Docker not running and
     `HIDFI_VM_HOME` set: `tests/docker/vm.sh up`, `tests/docker/run.sh`,
     `tests/docker/vm.sh down`. Neither: ASK.
   - Never install Docker Desktop, WSL, colima or a VM unasked. Every route is in
     docs/TESTING.md, "The Linux tiers, whatever machine you have".
   - Respect where the user wants things stored. If they said "nothing on the
     internal disk", every tool, cache and image goes under `HIDFI_VM_HOME`, and
     run the tools only through `vm.sh`, which redirects HOME for them. Check
     afterwards that `~/.colima`, `~/.lima`, `~/Library/Caches/lima` were not
     created.
5. `--board`, `--perf`, `--e2e`: ASK first - see the rules below.
6. New Linux golden files from step 4 land in the reports folder. Read them,
   then copy them into `tests/golden/`, and say so. Never write a golden file to
   make a failure pass.

The PowerShell flasher tests need `pwsh`: native on Windows; elsewhere they skip.
The GitHub workflow is **manual only** (Actions > tests > Run workflow): pushes
never start it, so the owner gets no mails. Local runs are the gate. Never
re-enable push, pull-request or scheduled triggers, and do not push to "let CI
check it".

**The one command** - see [docs/TESTING.md](docs/TESTING.md) for everything:

```bash
python tests/run.py            # no board: static, unit, ui, build, hostsim - run before saying "done"
python tests/run.py --board    # + serial suites against the board on COM
python tests/run.py --e2e      # + real typing/mouse into a focus-guarded page on THIS computer
python tests/run.py --all      # everything; after any firmware change, with a board attached
```

Rules for agents:

- **Ask before any tier that touches the user's computer.** `--board` moves the
  mouse (net zero) and changes the volume (down then up); `--e2e` types and
  clicks into a test page and needs the user to click Start and keep hands off.
  Read a script end to end before running it on someone's desktop - a "safe"
  flag once still pressed the mouse button.
- **A skip is not a pass.** Report skipped tiers and their reasons, exactly as the
  summary prints them. "Tests pass" with the hardware tiers skipped is a claim
  about the no-board tiers only.
- **Golden files** (`tests/golden/`) define "unchanged". Update one only for an
  intended change (`--update-golden`), read the diff, and say why. Never to make
  a failure go away.
- **Never call Playwright from another thread.** `board_sim.py` schedules its
  delayed side effects with `later()` and runs them in `tick()`, from a route
  handler. A `threading.Timer` that closed a socket "worked" on Playwright 1.63
  only as a side effect of an error the model swallowed; 1.64 dropped the side
  effect and six reconnect scenarios failed. An exposed function is no answer
  either: blocking calls from one deadlock. `tests/static` refuses a timer thread
  in the model.
- **Read facts from the source** (`tests/support/firmware.py`, `linux_keys.py`)
  instead of copying lists into tests; a copy drifts.
- **Add a property, add a planted bug.** `tests/unit/firmware/mutations.json`
  lists deliberate bugs that the unit tier must catch; a new guarantee needs one.
- **Firmware dependency versions are exact pins.** Moving one follows
  [docs/TESTING.md#upgrading-a-dependency](docs/TESTING.md#upgrading-a-dependency).

What the original suites guard, so nobody "simplifies" them away:

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
on a board that has a PIN set or entries stored (the board tier reports that as
a skip). Do not defeat that guard — it is the only thing standing between the
suite and somebody's real passwords.

**The dashboard is tested in Chromium against a model of the board**
(`tests/ui/bdd`, ~280 scenarios across every tab; about 100 s with
`pytest-xdist`). The model, `tests/support/board_sim.py`, is held to the real
firmware by `tests/board/test_sim_contract.py` whenever a board is attached - so
when the firmware's replies change, change the model in the same commit or that
test fails. The model also covers what used to be manual: a join, the
`lan_locked` gate, `/api/scan` over the LAN, the PIN lockout.

**Still manual, on real hardware:** those same paths against a real access
point, the zoom-reset button (only iOS Safari lets a page be pinch-zoomed), and
iOS haptics. The WiFi performance tests run when given `--board-url`.

**`test_mouse_led.py` without `--skip-clicks` sends real clicks** into whatever is
focused. The board tier always passes `--skip-clicks`, which since v3.9 also skips
the press/release pair (a press and a release *is* a click).

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
`open()`. This is normal and every test in `tests/` accounts for it. On macOS the
built-in CDC-ACM driver (port `cu.usbmodem*`) pulses the lines **even when
`dtr`/`rts` are set False before `open()`** - so every open is a reboot there, the
USB side drops for a few seconds, and keystrokes sent straight after an open are
lost. The test suite therefore opens the port once per run
(`tests/support/board.py`) and waits for `usb_mounted` before typing.

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
4. **Model it and test it, in the same change** (the skill
   `.claude/skills/hidfi-feature` walks through it):
   - a new command: a branch in `tests/support/board_sim.py` that replies exactly
     as the firmware does, a line in `tests/board/test_sim_contract.py` proving it,
     and docs in `docs/COMMANDS.md`;
   - a new control, sheet or behaviour on the dashboard: a scenario in
     `tests/ui/bdd/features/<tab>.feature` written as a person would describe it,
     with steps in `tests/ui/bdd/test_<tab>.py`. Tap it the way a finger does
     (`ui.touch_*` for gestures), and assert what reaches the board.
5. **Validate before flashing** — syntax check, tag balance, icon references.
6. **Flash, run the suite, then verify the new thing specifically**, ideally with
   a throwaway probe script over serial that leaves the board as it found it.
7. **Update the docs** in the same change.

### Definition of done

A change is done when `python tests/run.py` prints **PASSED** and each of these
holds. The first four are enforced - the run fails without them:

- [ ] every firmware command is documented, modelled by the simulated board and
      sent by a test (`tests/static`, three rules);
- [ ] every control on the dashboard is touched by a scenario, and every named
      dashboard function runs in one (the **coverage contract**, printed at the end
      of the ui tier; see `docs/TESTING.md#the-dashboard-coverage-contract`);
- [ ] the share of the dashboard script the scenarios run has not dropped below
      `tests/golden/ui_js_coverage.json`; the lines that never run are listed in
      `tests/.reports/ui_js_uncovered.txt` - read it, it is the to-do list;
- [ ] the responsive matrix (500+ screens, every tab, sheet and overlay, turning
      and resizing, layout speed) shows no new problem - it is part of the ui
      tier; a new sheet or overlay is added to `tests/support/responsive_run.py`;
- [ ] no golden file changed except for a stated, intended reason;
- [ ] a bug found but not fixed in this change is a scenario tagged
      `@known_issue:<key>` with the reason in `KNOWN_ISSUES`
      (`tests/ui/bdd/conftest.py`) and a line in `docs/TESTING.md#known-issues`.
      The tag is a strict xfail: when the bug is fixed the scenario passes, the run
      fails, and the tag must come off. Never delete a scenario to get a green run;
- [ ] hardware tiers were run, with the user's OK, for any firmware change - or the
      summary says plainly that they were not.

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
  do: `compact` (12 columns, `123` symbol layer, under 620px) → `ansi` (620px - from 500px
  it cut Esc and F10-F12 and shrank the side legends to 7px) →
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

## Ownership, PINs and recovery - security rules (v3.10)

These rules protect the owner. Do not weaken any of them to make a feature or a
test easier; if one blocks you, stop and ask the user.

Definitions:
- "WiFi password" means the board's own access-point password (`apPassword` in
  `hid_fi.ino`). It is the proof of ownership.
- "Access PIN" means `authToken`. It lets a phone use the board over WiFi.
- "Vault PIN" means `vaultPinCode`. It guards the vault separately. Never call
  either PIN just "the PIN" in user-facing text; name which one.

Rules:
- Never send the WiFi password over WiFi. Only `ap_password` returns it, and only
  when `cmdSource == SRC_SERIAL`. Never add it to `status`, `storage_info`,
  `/api/status` or any WebSocket reply.
- Over WiFi, the first access PIN (no PIN set, `token` not empty) requires
  `ap_password` to equal the WiFi password (`handleSetAuth`). If the access point
  is open, require the requester to be an access-point client (`peerOnAp()`).
  Serial requires nothing.
- Never add a way to reset or recover a PIN or the WiFi password over WiFi. Not
  with the WiFi password, not with a code, not with a button. Recovery is USB only:
  `tools/recover.py` and `flash_esp.ps1 -Recover`.
- Never add a physical-button reset or erase. A child pressing BOOT must not cost
  anyone their vault.
- A PIN reset without the old PIN erases the vault unless a vault PIN is set
  (`handleSetAuth`, `vaultGuardedByAccessPin`). Keep that. Recovery tools must say
  which will happen before changing anything, and require a typed ERASE (or
  `--erase-vault` / `-EraseVault`) when the vault will be erased.
- Every PIN check goes through `pinCheck()` with `pinPeer` set to the requester.
  Lockout is per IP (`pinGuards`) plus the home-network budget (`LAN_FAIL_BUDGET`).
  Never make access-point clients or serial subject to the home-network budget.
- Keep `dropUnsignedSockets()`: an unauthenticated socket is closed after
  `WS_AUTH_DEADLINE_MS` while a PIN is set.
- A board without a stored WiFi password makes one with `makeApPassword()` from
  `esp_random()`. Never derive it from the MAC, a serial number or anything the
  board broadcasts. Never reintroduce a shared default.
- Change `tests/support/board_sim.py` in the same commit as any of these rules, and
  keep `tests/ui/bdd/features/ownership.feature`, `tests/unit/test_recover.py` and
  `tests/board/test_ownership_hw.py` passing. Every row of the ownership tables in
  SECURITY.md has a scenario; a new rule needs a new row and a new scenario.
- Hardware tests that need a PIN use a temporary PIN on a board that has none, and
  must remove it and assert `auth_set` is false afterwards. Never print the WiFi
  password in test output - and remember who else prints: pytest shows the values
  in a failed `assert a == b` (reduce it to a bool first), and Playwright's error
  log shows what `fill()` was given (type secrets with an `eval_on_selector`
  helper, as `tests/board/test_ownership_live.py` does).

When an AI agent flashes or recovers a board FOR a person (the one exception to
"never show a password"):
- After a flash, or after `--recover --show-password` / `--set-password`, tell the
  person the board's WiFi name and password in your reply, in plain text, once.
  The flasher prints it on its last lines ("Dashboard: join WiFi ..."). They need it
  to join the board's WiFi; without it the dashboard is unreachable.
- Say in the same message that this is a one-time display: they should write it
  down or save it in a password manager now, and if it is lost,
  `./flash_esp.sh --recover` (Windows: `.\flash_esp.ps1 -Recover`) shows it again
  over the USB cable.
- Then forget it. Do not write it to memory, notes, files, commit messages, logs,
  artifacts, issue trackers or test output, and do not repeat it later in the
  conversation. If asked again, run the recovery tool rather than recalling it.
- Never invent or "simplify" it. Do not suggest a weak shared password such as
  `hid12345`; if the person wants their own, set it with `--set-password` (8 to 63
  characters) and show it once in the same way.
- This exception covers only the board's WiFi password, shown to the person who
  holds the board. It never covers the access PIN, the vault PIN, vault secrets or
  saved PC passwords - an agent never displays those.

## Actions for agents - the rules

The board turns a word into the right keys for the computer it is plugged into:
`{"cmd":"do","a":"copy"}`, `open`, `steps`, `actions`. Human guide:
docs/AGENT_CONTROL.md. Rules for changing it:

- The action table is `ACTIONS` in `hid_fi.ino`. Each row is: name, label, macOS,
  Windows, Linux (GNOME). An empty column means that OS has no equivalent; the
  board refuses it there (`no_equivalent`). Never fill a column with a guess.
- Every action row must equal a row of the dashboard's `SC` table in `web_ui.h`:
  same label, same three combos. `tests/static` fails otherwise. Change both
  together.
- Only add a shortcut that the OS vendor documents: Apple's "Mac keyboard
  shortcuts", Microsoft's "Keyboard shortcuts in Windows", GNOME's "Useful
  keyboard shortcuts". If you cannot point to the line, do not add it. Note a
  Windows 11-only shortcut as such in its label if it does nothing on Windows 10.
- Action names are lower_snake words and never change once released - agents
  and saved scripts depend on them. Add, do not rename.
- `board_sim.py` reads the table from `hid_fi.ino`, so the model needs no edit
  for a new row. A new step verb or command needs the model, a unit test in
  `tests/unit/test_actions.py`, and a line in docs/COMMANDS.md.
- Keep `actions` small: it is what an agent reads. It lists only names that work
  on the detected OS. `tests/unit/test_actions.py` caps it at 1000 characters and
  `tests/unit/test_hidfi_tool.py` caps the MCP tool list at 3.5 KB.
- `steps` stays bounded: 32 steps, 5 s per wait, 10 s of waiting per call. These
  block the board's loop, like `macro_run`.
- These commands go through the same PIN and home-network gates as every other
  command. Never add one to the LAN-safe list.

## The home network - the rules

The board must survive a router that restarts, drops it, is off at boot, or
gives it a new address. `checkWifiConnection()` is the supervisor:

- It alone reconnects. `WiFi.setAutoReconnect(false)` stays: the core retries
  some reasons and not others, and a timed-out join ended with `disconnect()`,
  which the core read as "stay off" - a board that booted before the router
  never joined. Never re-enable it, and never add a second reconnect path.
- A network that has worked (`sta_ok` in NVS) is retried forever: 1 s after a
  drop, backing off to 30 s. A new network whose password the router refused is
  not retried. Do not add a give-up for a proven network.
- With the home network gone for `AP_FALLBACK_MS`, the access point comes back
  even when auto-off switched it off. That check sits before the join guard; a
  15 s join must not delay it (it did once: 36 s).
- `staCameOnline()` is the one place that marks the board online. It records an
  address change (`sta_prev_ip`), persists `last_ip`, restarts mDNS on the new
  address, and pushes `sta_online`.
- `net_fault` is serial-only test staging. Keep it USB-only; never reachable
  over WiFi. `tests/board/test_home_network.py` must pass on a real board after
  any change here, and `tests/ui/bdd/features/home_network.feature` covers the
  dashboard.
- Tests that talk to the board over the network take its address from
  `support.board.lan_url()`, not from the command line: the address can change
  mid-run (it did, .107 to .100).

## Text typing - the rule that came from a bug

`Keyboard.press()` in the ESP32 core reads every byte **>= 0x88 as a raw HID
usage** and 0x80-0x87 as a modifier. Text arrives as UTF-8, so before v3.9 a
phone's curly quote (`E2 80 99`) typed Keypad-2, a Ctrl tap and `n`, and a degree
sign pressed F1 then **Enter**. Therefore:

- **No byte >= 0x80 may reach `Keyboard.press()`.** `typeString()` decodes
  well-formed UTF-8 (Unicode Table 3-7) and either types ASCII, folds phone
  punctuation to ASCII, enters the character through the host's Unicode input
  (`set_unicode`), or skips and counts it. Any new path that presses a character
  must go through it. The one-character fallback in `pressKeyCombo()` has a
  `< 0x80` guard and must keep it. **Open item:** `hidKeyFor()` (used by
  `key_down`/`key_up` and the WebSocket key frames) still passes a one-byte name
  straight to `press()`. Valid UTF-8 never produces a one-byte name >= 0x80, but
  a raw byte over serial or HTTP would; it needs the same guard.
- **The Unicode sequences only run where they are safe.** The macOS one holds
  Option, which is Alt on a PC (Alt+F opens menus), so it is vetoed whenever
  detection saw Windows, even if the OS is pinned by hand; the Linux one is vetoed
  on a detected Mac. Off by default, per OS.
- The unit tier compiles `typeString()` against the installed core and checks
  every Unicode scalar value; `tests/golden/typing_ascii.json` pins plain typing
  report-for-report.

---

## Screen sharing (HID-Fi Companion) - the rules

The app is in `companion/` (Rust workspace); read `companion/REQUIREMENTS.md`,
`DESIGN.md` and `PROGRESS.md` first. The board's side is `hid_fi/companion.h`
(pairing, tickets) and the dashboard's Screen tab in `web_ui.h`.

- **Video never touches the board.** WebRTC goes computer -> viewer directly; the
  board is a directory and trust anchor (pairing, signed one-use tickets, the
  app's certificate fingerprint). The app never holds a board WebSocket (it would
  break release-all, radio sleep and AP auto-off): `POST /api/companion` only.
- **The board picked is the one on this computer's USB**, matched by USB serial
  (= efuse MAC = status `mac`). 303A:1001 is generic: never match by VID/PID.
- **Pages:** the board serves `ws_max` = 8 dashboard pages. The WebSockets
  library's own limit comes from `hid_fi/build_opt.h`; a `static_assert` ties it
  to `WS_MAX_CLIENTS`, and the flash scripts rebuild from clean when that file
  changes (a stale library object would corrupt memory). Never remove it.
- **Viewers:** six per computer (`MAX_VIEWERS`), each choosing a monitor; capture
  and encoding are per (monitor, quality) lane and shared. Keyframes are per
  viewer and rate limited - one slow viewer must never make keyframes for all.
- **macOS signing:** an ad-hoc signed app gets a new identity every build; macOS
  then refuses Screen Recording while System Settings still shows the switch on.
  `companion/package/macos.sh` signs with a local "HID-Fi Local" certificate when
  one exists. After an ad-hoc rebuild: `tccutil reset ScreenCapture in.hidfi.companion`.
  Never rebuild the bundle under a running app without expecting it to restart
  itself (macOS drops a replaced app's local network access; `replaced.rs`).
- **Every dashboard request gets an answer** (`send()` calls back with `null` when
  the link drops or the PIN is asked): a callback that waits forever left the
  Screen tab stuck on "Connecting..." after a phone slept.
- **Tests:** `companion/README.md` lists them. Real-board ones (`hardware.py`,
  `board_pages.py`, `multi_monitor_mac.py`) need `HIDFI_PIN` in the environment
  - never write a PIN into a file. Linux: `companion/tests/linux/run.sh` (Docker;
  `tests/docker/vm.sh` on a Mac). Windows: the manual `companion.yml` workflow.

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
