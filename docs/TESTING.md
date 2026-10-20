# Testing HID-Fi

One command runs everything your machine can run, and tells you what it skipped
and why:

```bash
python tests/run.py            # no board needed: static, unit, ui, build, hostsim
python tests/run.py --board    # + the board on its COM port
python tests/run.py --e2e      # + real typing and mouse into a test page on this computer
python tests/run.py --perf     # + performance (add --board / --e2e for the hardware ones)
python tests/run.py --all      # everything this machine can do
python tests/run.py doctor     # check the environment, run nothing
```

The run ends with a table - passed, failed, skipped per tier - and the reason for
every skip. It exits non-zero on any failure, so CI and contributors read the
result the same way. Reports land in `tests/.reports/` (`junit.xml`,
`summary.md` to paste into a PR, `perf.json`).

---

## Contents

- [Set up once](#set-up-once)
- [Tiers](#tiers)
- [What the tests do to your computer](#what-the-tests-do-to-your-computer)
- [Testing on each OS](#testing-on-each-os)
- [What is covered where](#what-is-covered-where)
- [The dashboard, scenario by scenario](#the-dashboard-scenario-by-scenario)
- [The dashboard coverage contract](#the-dashboard-coverage-contract)
- [Every screen: the responsive matrix](#every-screen-the-responsive-matrix)
- [Known issues](#known-issues)
- [Golden files and baselines](#golden-files-and-baselines)
- [Performance](#performance)
- [Upgrading a dependency](#upgrading-a-dependency)
- [Before a pull request](#before-a-pull-request)
- [Adding a test](#adding-a-test)
- [Hooks: tests that run themselves](#hooks-tests-that-run-themselves)
- [When it goes wrong](#when-it-goes-wrong)

---

## Set up once

Everything in [FLASHING.md](FLASHING.md#install-the-tools-first-time-only)
(arduino-cli, the ESP32 core, two libraries), plus:

```bash
pip install -r tests/requirements.txt
python -m playwright install chromium
```

and a C++ compiler for the unit tier: Xcode command line tools on macOS
(`xcode-select --install`), `build-essential` on Linux, MinGW or Visual Studio
Build Tools on Windows. Node.js 18+ for the dashboard syntax check.

`python tests/run.py doctor` shows what is installed against what is pinned in
[`tests/deps.json`](../tests/deps.json).

---

## Tiers

| Tier | Needs | What it proves | Touches your computer? |
|---|---|---|---|
| **static** | Python, Node | The dashboard parses and every id/icon resolves; every firmware command is documented in [COMMANDS.md](COMMANDS.md) and nothing documented is missing; `FW_VERSION` matches the CHANGELOG; the flashers and docs build the same FQBN; nothing writes `merged.bin`; no emoji in the dashboard | No |
| **unit** | C++ compiler, ESP32 core | The firmware's real typing code, compiled on your machine against the **installed core's own** `USBHIDKeyboard.cpp` and US key table: every Unicode character round-trips through models of macOS and Linux, ill-formed UTF-8 never becomes a key, nothing is left held; plain-ASCII typing is **byte-identical** (reports and timing) to the committed reference; core canaries; 17 planted bugs must all be caught | No |
| **ui** | Playwright | Gestures; the reply queue never drifts; 67 viewports with no **new** layout problems; every one of ~700 controls on every tab, at phone, tablet and desktop width, sends exactly what it always sent; the emoji settings and Test button | No |
| **build** | arduino-cli + pinned core/libs | The firmware compiles with the flasher's exact settings, inside its size budget, with no new compiler warnings in HID-Fi's code | No |
| **hostsim** | Linux + Xvfb, IBus, GTK | The firmware's exact keystrokes replayed into a real Linux input stack (IBus as on GNOME, and GTK without IBus): every language, emoji, Caps Lock on and off. Runs in CI on every push | No (a virtual X display) |
| **board** | Board on COM | The serial command surface and the original suites: pointer split, media, power modes, inventory, secrets never leak, vault gate, OS detection, the WiFi join state machine, mouse/wiggle/LED | Net-zero mouse moves, a volume down then up. Never types or clicks |
| **e2e** | Board on COM **and** USB | Real typing, shortcuts, held keys, macros, every safe key by code, every language with Unicode entry off and on, the PC guard, mouse movement to the pixel, wheel, pan, buttons, drag, wiggle | **Yes** - only into the test page, only while it has focus |
| **linux** (part of e2e) | Linux + board on COM and USB + `evdev` | The kernel's own events from the board with its devices **grabbed**: every key name including F-keys, Esc, Super, PrtSc; every printable character; dangerous combos; all media keys; sleep, power and wake; gestures; presenter; lock and unlock; exact pointer motion, scroll, pan, buttons; latency from kernel timestamps | No - grabbed devices never reach the desktop |
| **perf** | Varies | See [Performance](#performance) | As its tier |

`--tier unit,ui` runs a subset; `-k name` runs matching tests; `-x` stops at the
first failure. Anything after `--` goes to pytest.

---

## What the tests do to your computer

The rules every tier follows:

- **Opt in.** Nothing touches the hardware without `--board`, and nothing sends
  input to your computer without `--e2e`.
- **Focus guard.** End-to-end input goes only into the test page, and every
  command is preceded by a check that the page still has focus. If a
  notification steals focus, the run stops before sending anything.
- **Keys a browser cannot block are never sent to a live desktop.** Cmd+Q,
  Alt+F4, the Windows key, F11, PrtSc, Esc and the system keys are only
  exercised on Linux through evdev, where the board's devices are grabbed.
- **The board is left as found.** The run snapshots the board's settings
  (Unicode switches, OS pin, power mode, macro slots) on first contact and
  restores them at the end; anything it had to restore is listed in
  `tests/.reports/board_restore.json`, and the run fails if the board is not
  back where it started.
- **One serial session.** Opening the COM port reboots the board (macOS's
  serial driver pulses the reset lines even with DTR/RTS held low), so the
  whole run shares one session. The original scripts in `tests/` open the port
  themselves and are run one after another.
- **Suites that would destroy something refuse.** `test_vault.py` erases the
  vault by design, so on a board with a PIN or a stored entry it refuses and
  changes nothing - reported as a skip.
- **Never automatic:** `test_lock_state.py` really locks the computer and types
  its password. Run it by hand.

One residual risk worth knowing: `test_v34_features.py` checks that `lock` is
idempotent by first telling the board the PC is already locked. If that firmware
logic ever broke, this test would lock your screen - an annoyance, not damage.

---

## Testing on each OS

The board needs two cables for the hardware tiers: **COM** to the machine running
the tests, **USB** to the computer being tested - usually the same machine.

### macOS

```bash
python tests/run.py --e2e
```

A browser tab opens on the test page. Click **Start**, then leave it in front and
do not touch the keyboard or mouse until the run finishes. Start also turns on
pointer lock, which the mouse tests need.

Unicode entry tests need *Unicode Hex Input* selected. Let the run do it:

```bash
python tests/run.py --e2e --allow-input-source-switch
```

It adds and selects the input source for those tests, then restores exactly
what you had - the same source selected, and Unicode Hex Input removed again if
it was not enabled before.

### Windows

```powershell
python tests\run.py --e2e
```

Same page, same Start button. Windows has no Unicode entry by design, so those
tests skip; the **PC guard** test runs instead - the board pinned to macOS with
emoji on, plugged into this PC, must still send only plain characters and refuse
the macOS Test button.

**Testing a Windows PC from a Mac or Linux machine** (nothing installed on
Windows): COM cable to the machine running the tests, USB cable to the PC, then

```bash
python tests/run.py --e2e --sink remote
```

and open the printed `http://<this-machine>:<port>/` on the PC. Both must be on
the same network. Latency tests skip in remote mode (two clocks).

### Linux

The richest tier, because grabbing the board's input devices makes every key safe
to test:

```bash
pip install evdev
sudo -E python tests/run.py --e2e       # or add yourself to the 'input' group and log in again
```

`-E` keeps your Python environment under sudo. If any of the board's devices
cannot be grabbed, the evdev suite skips entirely rather than send anything to
the desktop. The browser-page tests also run (on GNOME/IBus desktops the Unicode
ones too; a Qt/KDE browser without IBus will fail them, which is the documented
limit of Ctrl+Shift+U).

The **hostsim** tier needs no board and runs anywhere with:

```bash
sudo apt install xvfb ibus ibus-gtk3 python3-gi gir1.2-gtk-3.0 python3-xlib x11-xkb-utils dbus-x11
python tests/run.py --tier hostsim
```

On a Linux box without the ESP32 core, record the keystrokes elsewhere and replay:

```bash
python tests/hostsim/record.py > cases.json                       # on a machine with the core
HIDFI_HOSTSIM_CASES=cases.json python tests/run.py --tier hostsim  # on the Linux box
```

### No Linux machine?

**For the tiers that need no board** - `hostsim` (the Linux text-input replay) and
the Linux layout baselines - Docker on any Mac or PC is enough:

```bash
tests/docker/run.sh            # static, unit, ui, hostsim in Ubuntu 24.04, as CI runs them
tests/docker/run.sh hostsim    # only the Linux text-input replay
```

Your checkout is mounted read-only; results land in `tests/.reports/linux/`,
with any golden file the run created for Linux there for review. The image is
about 1.5 GB; Docker Desktop can keep it on an external drive (Settings >
Resources > Advanced > Disk image location). CI runs the same tiers free on
every push of this public repository.

**For the board on a Linux host** (`--e2e`, the evdev latency tests), a live USB
stick of any GNOME distribution (Ubuntu, Fedora) is enough: boot it, install the
packages above, plug in the board, run `sudo -E python tests/run.py --e2e`.

---

## What is covered where

| Feature | unit | ui | board | e2e macOS | e2e Windows | e2e Linux (evdev) | hostsim |
|---|---|---|---|---|---|---|---|
| Plain typing, every printable character | traces | | | text | text | key codes | text |
| Phone punctuation folding, skipped count | all 1.1M code points | toast | count | text | text | | text |
| Unicode entry (emoji, accents, scripts) | model, all code points | switches, Test flow | commands | Unicode Hex Input | n/a by design | raw sequence | IBus + GTK |
| PC guard (pin from a Mac) | model | refusal message | | | text | | |
| Every key name | | sends | | safe keys, by code | safe keys, by code | **all 85**, by code | |
| Shortcuts, held keys, release all, macros | | sends | | yes | yes | yes | |
| Dangerous combos (Ctrl+Alt+Del, Super+L, Alt+F4) | | sends | | never sent | never sent | yes (grabbed) | |
| Media keys | | sends | volume down/up | | | all 22 | |
| Sleep, power, wake | | sends | | never sent | never sent | yes (grabbed) | |
| Gestures, presenter | | sends | | | | yes | |
| Lock / unlock | | sends | lock state machine | never sent | never sent | yes (grabbed) | |
| Pointer movement, large-delta split | | trackpad gestures | accepted | to the pixel | to the pixel | to the pixel | |
| Wheel, pan, buttons, drag, wiggle | | | accepted | yes | yes | yes | |
| Absolute pointer, gamepad | | sends | accepted/rejected by mode | | | | |
| Secrets never leave the board | | | yes | | | | |
| Ownership: first PIN needs the WiFi password, per-device lockout, unsigned connections closed, password never over WiFi | | ownership.feature (21 scenarios) | test_ownership_hw.py | | | | |
| Recovery over USB (both flashers): show/set the WiFi password, reset the access PIN, vault kept or erased after ERASE, several boards | test_recover.py, against a simulated board on a real serial device | | | | | | |
| WiFi join, power modes, inventory | | | yes | | | | |
| Dashboard: every tab, control, sheet, gesture and error path | | ~280 scenarios | model checked against the board | | | | |
| Latency and throughput | | | serial | page timestamps | page timestamps | kernel timestamps | |

Not automated, and why: absolute-pointer and gamepad **delivery** (switching
either reboots the board into a different USB device - covered by
acceptance on the board tier and by hand); real lock/unlock against a real
password (`test_lock_state.py`, by hand); `vault_type` putting a real secret on
the wire (only with `test_vault.py --type`, by hand).

---

## The dashboard, scenario by scenario

`tests/ui/bdd/` drives the real dashboard in Chromium, one feature file per tab:

| Feature file | What it covers |
|---|---|
| `app_shell.feature` | Connecting, the link pill and latency, offline and reconnect (dropped and unreachable), the access PIN prompt and lockout, tabs from the bar, the sidebar and links, back button, help, refresh, swipe between tabs |
| `trackpad.feature` | Pointer, tap and double tap, hold-to-drag, tap-then-drag with its nudge, two-finger scroll and glide, pinch, three-finger swipes, tap and drag, the app switcher (held tile, slide, arrows, three-finger), the buttons card and mouse view, wheel, grab everywhere it is offered, gesture tiles per OS, feel settings, haptics per browser, the keyboard deck, fullscreen |
| `keyboard.feature` | Send and Go, every key class, held modifiers with two fingers, latching, the 123 layer, OS-specific labels, custom combos, macros (create, run, edit, delete, full), the layout tier for every screen size and the numpad |
| `keys_vault_shortcuts.feature` | The vault (store, refuse, type, show, rename, delete, its own PIN, reset, erase, board refusals), shortcuts per OS and per phone, browsing, groups, search, counts |
| `media.feature` | Knobs (turn, hold to mute, ranged, create, edit, delete, media and key knobs), playback tiles, mute state, quick actions (defaults per OS, toggles, long-press edit, Test, create, delete), the layout saved on the board |
| `gamepad.feature` | Off by default, enabling (reboot overlay), the power button, both sticks, the D-pad, every button bit, a board slow to come back |
| `session.feature` | Unlock, Ctrl+Alt+Del per OS, lock, lock-then-unlock, saved PCs (save, unlock, edit, forget, never echoed), sleep and wake with the USB-wake explanation, presenter, timer, stay awake, HTTP fallback |
| `settings.feature` | Computer detection and pinning, Mac setup keys, emoji switches and the Test flow, scan and join (DHCP, fixed IP, DNS, failure, busy, forget mid-join), the LAN warning and LAN lock, the access point form and overlay, the access PIN (set, change, off, the four boxes: paste, Backspace, arrows, letters), pointer mode, power modes, device, clients, storage, Test HID, the log |

How it works:

- **The board is a model**, `tests/support/board_sim.py`: every command, the PIN
  lockout, the vault gate, reboots, the WiFi join, the LAN lock, an unreachable
  board. It is held to the firmware by `tests/board/test_sim_contract.py`
  whenever a board is attached - the same commands go to both and the replies
  must match field for field.
- **Input is real.** Gestures go through Chromium's DevTools touch input, so
  pointer capture, `pointerrawupdate` and multi-touch behave as on a phone.
- **What the phone is** is part of the scenario: `Given the dashboard is open on
  a <device>` (`DEVICES` in `tests/support/ui.py` - phone, iPhone, Android phone,
  small phone, tablet, desktop, wide desktop) and `Given this phone's browser
  <trait>` (`BROWSERS` in `tests/ui/bdd/conftest.py`).
- **The board can say no** on demand: `Given the board will refuse the next
  "<cmd>" with "<message>"`.
- It runs in parallel when `pytest-xdist` is installed (about 100 s; `run.py
  --serial` to run in one process).

Run one feature while you work on it:

```bash
python -m pytest tests/ui/bdd/test_settings.py -q -n 4 -p no:cacheprovider
```

---

## The dashboard coverage contract

"Nothing missed" is measured, not claimed. Every scenario records which controls
it touched and which of the dashboard's JavaScript ran (Chromium's precise
coverage, kept across reloads). When the whole ui tier has run, the results are
merged and the run **fails** if:

- a control on the dashboard (an id, or the family of a generated control such
  as `[data-k]` keys) is touched by no scenario;
- a named function never runs, and is not listed with a reason in
  `tests/golden/ui_functions_unrun.json`;
- the share of the dashboard script that runs drops below
  `tests/golden/ui_js_coverage.json` (95.6% today), or rises well above it
  (raise the floor with `--update-golden`).

The lines that never run are written to `tests/.reports/ui_js_uncovered.txt`, as
`web_ui.h:<line>` - that file is the to-do list. What remains on it today and why:

| Lines | Why no scenario reaches them |
|---|---|
| `txAbs()` | Unused: nothing calls it. Harmless - in absolute mode the firmware turns the pad's relative moves into its own virtual cursor (`hidMouseMove`) |
| The zoom-reset button | Only iOS Safari lets a page with `user-scalable=no` be pinch-zoomed; check by hand on an iPhone |
| Real fullscreen exit | Headless Chromium has no fullscreen; the dashboard's own fullscreen mode is covered |
| The tab swipe's `go()` | Known issue `swipe_tabs` |
| `new WebSocket()` throwing | Only for a malformed URL, which the page never builds |

A partial run (`-k`, one file, `--lf`) does not judge the contract - it says so
and points at the full command.

---

## Every screen: the responsive matrix

`tests/ui/responsive/` draws the dashboard on **517 screens** and measures every
state on each one - 16,027 screens in all, about four minutes of the ui tier. The
count follows the CSS: every new breakpoint adds its boundaries.

**Which screens.** The matrix is read from `web_ui.h` itself
(`tests/support/responsive.py`), so a new breakpoint is covered on the next run
without anyone remembering to add it:

- **regions** - one screen inside every combination of width band and height
  band the media queries create (every pairing of rules that can be active
  together), as a mouse and, where a hand could reach it, as a touch screen;
- **boundaries** - both sides of every breakpoint (309/310, 359/360, 2399/2400 ...),
  including the large-display zoom steps that need a width and a height at once,
  and the sidebar's 1100px decided in JavaScript;
- **devices** - iPhones from the SE to the 17 Pro Max, Galaxy and Pixel phones,
  the Fold folded and open, the Surface Duo, iPads and an Android tablet - each
  upright and on its side, with the browser's toolbars showing and hidden, at its
  real pixel ratio, as a touch screen (`hover:none`), with the notch and home
  indicator insets it really reports - then laptops, desktop windows, QHD,
  ultrawide, 4K, 5K and 8K.

**Which states.** Every tab; the shortcuts searched; the keyboard deck; the mouse
buttons; the controller enabled; the fullscreen pad and fullscreen deck; every
sheet (access PIN, LAN lock, knob, action, macro, the vault's five, saved PC,
help, join, wake failed); every overlay (reboot, new access point, app
switcher); a long message. The board is full: every vault, PC and macro slot
used, every name as long as it may be.

**What is checked, on each.** Nothing wider than the screen. No control's label
cut off. Every control can actually be tapped - scrolled to, and not under the
tab bar, a sheet or another control. Keys and controls big enough for a finger
on a touch screen. No text under 9px. One way to navigate and one logo. Every
letter on a visible keyboard, the arrow keys in line, the keyboard drawn at the
tier it has room for. Nothing pinned under the notch or the home indicator. A
sheet inside the screen with its close button reachable. The fullscreen pad
exactly the size of the screen, with a way out. Each check is something that has
broken before (see the CHANGELOG) or a promise the layout makes.

**Turning and resizing** (`test_rotation.py`): every touch device is turned on its
side, gets its toolbars back, and is turned upright again - from the keyboard
tab, the deck and the fullscreen deck. The first frame after each change must
already have the right keyboard tier, nothing may appear that a fresh load at
that size does not have, and coming back must leave the layout exactly as it
was. Desktop windows are resized across the sidebar and keyboard breakpoints and
back.

**Speed** (`test_layout_perf.py`): on a phone with its CPU slowed four times, a
tablet slowed twice, a 1080p desktop and a 4K display - loading, the slowest tab
switch, all tabs, the deck, fullscreen, a sheet and a rotation, as main-thread
work (script, style, layout), plus layout shift on load. Each has a budget a
person would notice, and a per-OS baseline in
`tests/golden/ui_responsive_perf.<os>.json` that fails anything more than twice as
slow. The table lands in `tests/.reports/responsive_perf.md`. Today (macOS,
Apple silicon, with the crowded board's full saved layout loaded):

| main-thread ms | phone, 4x slower | tablet, 2x slower | 1080p | 4K |
|---|---|---|---|---|
| load | 60 | 34 | 15 | 13 |
| slowest tab switch | 17 | 12 | 7 | 39 |
| all seven tabs | 68 | 39 | 22 | 55 |
| keyboard deck | 8 | 6 | 3 | 13 |
| rotate | 12 | 6 | 5 | 3 |
| layout shift on load | 0 | 0 | 0 | 0 |

**Problems that exist today** are recorded per OS in
`tests/golden/ui_responsive_known.<os>.json` - one on macOS, down from 737 when
the matrix first ran (see Known issues). It is a ratchet: a
new problem fails the run with the screen and state that show it; a fixed one
fails until it is taken off the list (`--update-golden`), so it cannot come back.
`tests/.reports/responsive.md` groups them by kind. The ones that matter most
are listed under [Known issues](#known-issues).

Working on one screen size:

```bash
python -m pytest tests/ui/responsive -q -n auto -k "iPhone_14 or Galaxy_Fold"
```

A new sheet or overlay must be added to `SHEET_STATES` / `OVERLAY_STATES` in
`tests/support/responsive_run.py`; a static rule fails until it is.

Pixel measurements depend on the platform's fonts, so each OS keeps its own
baseline. CI on Linux has none until one is recorded there
(`python tests/run.py --tier ui --update-golden` on Linux, then commit the two
`.linux.json` files); until then it reports the matrix but cannot ratchet it.

---

## Known issues

A bug the tests find and that is not fixed in the same change becomes a scenario
tagged `@known_issue:<key>` (reason in `KNOWN_ISSUES`, `tests/ui/bdd/conftest.py`)
that runs as a **strict** xfail: the day the bug is fixed the scenario passes, the
run fails, and the tag has to come off. `run.py` lists them under KNOWN ISSUES.

**There are none open.** Fixed in v3.9, each now an ordinary scenario that keeps
it fixed:

| Was | Severity | What it was, and the fix |
|---|---|---|
| `lan_binary` | **Security** | With no access PIN and the board on a home network, the LAN lock only guarded JSON commands; binary frames (keys, pointer, buttons, media) only checked `wsAuthed`, which is true for everyone without a PIN, so any device on the LAN could type. The board now records per socket whether it came over the LAN (`wsFromLan`, set once at connect) and drops its binary frames under the same rule. Firmware - `tests/board/test_lan_gate.py` proves it on hardware. |
| `reconnect_lists` | High | The board greets a new socket with a bare `auth_required` when a PIN is set; the dashboard let it take a reply slot, so every later reply went to the previous command's callback and the vault, saved PCs and macros came up empty after a reconnect. A greeting carries no `message` (every refusal does), so it now takes no slot. Works with boards already flashed. |
| `shortcuts_os` | Medium | An iPhone driving a Windows PC kept Mac shortcuts: `applyHostOs()` returned early when the PC matched its initial guess and never re-synced the shortcut OS. |
| `pc_new_event` | Medium | The + for saved PCs passed the click event as the PC to edit: it opened "Edit saved PC", skipped prefilling and the password check. |
| `lan_scan` | Low | A scan refused over the LAN read "No networks found"; it now says a PIN is needed. |
| `latch_drop` | Low | Tapping a latched modifier released it on the PC but re-latched it on screen. |
| `swipe_tabs` | Low | The tab swipe listened to pointer events, which the browser cancels once it claims a sideways pan; it uses touch events now. New scenarios check that a vertical scroll, a flick starting on a control, a slow drag, two fingers and a mouse drag never change tab. |
| `knob_step_label` | Low | The knob editor's step label did not follow its slider. |

The responsive matrix first found **737** layout problems on macOS. One is left, a
deliberate trade-off: on a 653x224 screen (a Galaxy Fold cover screen on its
side) the fullscreen deck's keys are 16px. Fixed:

| Where | What it was | Fix |
|---|---|---|
| Phones on their side with the browser's toolbars, any screen under 480px tall | The app switcher's buttons fell off the screen | Pad and buttons side by side, no scrolling while Alt is held |
| Landscape iPhones | Fullscreen bar, switcher and overlays under the notch | Padding from the safe-area insets |
| Landscape phones, deck open, under 360px tall | Home and End under the corner buttons | The deck makes room beside them (above them on a narrow screen) |
| Keyboards 500-620px wide (foldables, the fullscreen deck) | Esc, F10-F12, Home, PgUp cut; side legends at 7px | ANSI starts at 620px (was 500); compact below reaches every key |
| Phones 310-520px | Ctrl, Alt, Tab, ?123 cut by 1-3px | Those caps' type 10% smaller; keys unchanged |
| Large scaled displays (4K and up, ultrawide) | The help sheet ran off the top, close button unreachable | `max-height:88%` of the sheet, which follows root zoom - `88vh` did not |
| Under 360px | "Performance", "Windows" cut | Less padding, 12px (11px under 310px) |
| Very short screens | The reconnect overlay's Reload, the sticks and the knob tile off screen | Overlay can scroll; sticks and knobs capped by height |
| Everywhere a ranged knob shows | MIN/MAX at 8.5px | 9px |

What changed for people, on purpose: a keyboard 500-620px wide now uses the
compact layout (digits row plus the `123` layer) instead of a cramped ANSI, and
on a phone on its side with the browser's toolbars showing, the deck's keys make
room beside the corner buttons. Measured cost of all the fixes: page load is
about 3% more work on a phone slowed four times (about 1.5 ms, ~0.4 ms on the
phone itself); every other action is unchanged within noise.

Found but not tagged, because no person can hit them:

- The PIN boxes' focus moves on the next tick (`select()` in a timeout), so
  digits typed within the first few milliseconds after focusing can land in the
  wrong box. Scenarios wait 120 ms after focusing, as a finger would.
- `lastTapAt` starts at 0, so in the first 450 ms of a page's life any tap counts
  as the second tap of a double tap.
- `txAbs()` is never called. Not a defect: in absolute mode the firmware turns
  relative moves into absolute positions itself. It can go, or stay for a future
  touchscreen-style mode.

---

## Ownership and recovery

The rules in [SECURITY.md](../SECURITY.md#ownership-who-can-claim-the-board-and-how-you-get-back-in)
are tested at three levels, and each row of its two tables has a test:

- **Dashboard** - `tests/ui/bdd/features/ownership.feature`: the first PIN on the
  board's own WiFi and over the home network, with and without the right WiFi
  password; an open access point; changing a known PIN; the WiFi password never
  reaching the page; the explanation and *Forgot the PIN?* sheets; the password
  advice; one device locked out while the owner signs in; the home-network budget
  sparing the board's own WiFi; an unsigned connection closed and the dashboard
  recovering.
- **Recovery tools** - `tests/unit/test_recover.py` runs `flash_esp.sh --recover`
  and `flash_esp.ps1 -Recover` against `support/fake_serial.py`: a simulated board
  on a real pseudo-terminal, so the tools open it like any COM port. Every
  recovery case, the typed ERASE (and anything else refusing), the WiFi password
  typed twice, several boards, a device that is not a HID-Fi board, firmware older
  than v3.10. The PowerShell half needs `pwsh` (Windows CI has it).
- **Hardware** - `tests/board/test_ownership_hw.py`, with `--board` and, for the
  network parts, `--board-url ws://<board's home-network IP>:81`. It only ever uses
  a temporary PIN on a board that has none, removes it, and never prints the WiFi
  password.
- **The whole story, live** - `tests/board/test_ownership_live.py` drives the
  dashboard the board itself serves, in Chromium, through ten ordered steps: the
  first PIN (no, wrong and right WiFi password), sign-in after a reload, a vault PIN
  and a stored secret, a PIN change that keeps it, this device locked out while the
  cable is not, USB recovery with and without a vault PIN, and the WiFi password
  changed over USB and from the page. A failed step skips the rest. It needs a
  bench board (no access PIN, empty vault) and the board's home-network address:

  | Run it | Needs |
  |---|---|
  | `python tests/run.py --tier board --board --board-url ws://<board IP>:81` | the board on the COM cable and on your home network, no access PIN, empty vault |
  | the same with `ws://192.168.4.1:81` | this machine joined to the board's own WiFi; the two WiFi-password steps skip, since they would drop it |

  Afterwards the board has no PIN, an empty vault and its original WiFi password,
  and the run fails if it cannot prove that. Secrets are typed into the page with a
  helper that Playwright does not log, because its error log prints whatever
  `fill()` was given.

---

## The home network

`tests/board/test_home_network.py` rehearses a bad night for the router on a real
board, through the USB cable only (`net_fault`), so the router is never touched:

| Case | Staged | Must happen |
|---|---|---|
| kicked | the router lets the board go | back within 15 s, same address, reachable over the network |
| router off | network gone 40 s | own WiFi stays up, `reconnecting`, back within 40 s of the router |
| off at boot | board restarts with the router off | keeps trying, joins when it is back |
| new address | the board last held another address | `sta_prev_ip`, `sta_online` event with `changed` and the mDNS name |
| auto-off | AP off, then the network goes | AP back within 35 s |
| name | - | `esp-hid-XXXXXX.local` resolves to the board |
| USB only | `net_fault` over WiFi | refused |

Run it with `--board`; it takes about four minutes and puts everything back. The
dashboard side is `tests/ui/bdd/features/home_network.feature`.

## Golden files and baselines

`tests/golden/` holds what "unchanged" means. A test compares against its golden
file; any difference fails with the exact entries that changed.

| File | Holds | Created from |
|---|---|---|
| `typing_ascii.json` | Every HID report and delay for plain-ASCII typing | Verified byte-identical to v3.8 |
| `typing_corpus_fingerprints.json` | A fingerprint of the exact reports for every corpus entry in every OS mode | v3.9 |
| `ui_controls.json` | What every dashboard control sends, at three widths | Verified identical to v3.8 for every shared control |
| `ui_functions_unrun.json` | Dashboard functions no scenario can reach, each with the reason (a ratchet) | v3.9 |
| `ui_js_coverage.json` | The floor for the share of the dashboard script the scenarios run (a ratchet) | v3.9 |
| `ui_untouched_allowed.json` | Controls no scenario has to touch, with the reason - empty, and should stay so | v3.9 |
| `ui_responsive_known.<os>.json` | Layout problems the responsive matrix finds today (a ratchet) | v3.9, per OS |
| `ui_responsive_perf.<os>.json` | Layout speed per device profile (fails at 2x) | v3.9, per OS |
| `ui_layout_known_issues.<os>.json` | Layout problems that already exist (a ratchet) | First run per OS |
| `build_size.json`, `build_warnings.json` | Program size and HID-Fi's compiler warnings (ratchets) | v3.9 |
| `perf_baseline.json` | Per-OS performance baselines | First `--perf` run per OS |

When a change is **meant** to alter one of these, rerun with `--update-golden`,
read the diff, and say why in the pull request. Never update a golden file to
make a failure go away without knowing why it failed.

Ratchets only tighten: fixing a known layout problem fails the test until you
remove it from the known-issues file, so it can never quietly come back.

---

## Performance

Budgets are product requirements; baselines catch a slowdown that is still
inside budget (more than 1.5x worse than this OS's baseline fails).

| Metric | Budget | Tier | Measured on the reference Mac |
|---|---|---|---|
| Serial round trip, p95 | < 40 ms | board | 17 ms |
| Serial commands per second | >= 20 | board | 59 |
| Boot to banner | < 6 s | board | 3.0 s |
| OS detection | < 5 s | board | 3.1 s |
| Heap lost over a soak | < 2 KB, floor >= 40 KB | board | 0 B |
| Key to host, p95 | < 60 ms | e2e | 5 ms |
| Typing throughput | >= 20 char/s | e2e | 50 char/s |
| Pointer to host, p95 | < 60 ms | e2e | 3 ms |
| Key / pointer latency from kernel timestamps, p95 | < 40 ms | linux | |
| WiFi ping, p95 | < 80 ms | needs `--board-url` | |
| WiFi ping while streaming 60 Hz pointer frames, p95 | < 120 ms | needs `--board-url` + PIN | |

The WiFi tests need this machine on the board's network: `--board-url
ws://192.168.4.1:81` (its own access point) or its LAN address. If the board has
an access PIN, give it through the environment - `HIDFI_PIN=1234` - never on the
command line; it is not stored or printed. The soak length is `--soak-minutes`.

---

## Upgrading a dependency

The firmware's dependencies are **exact pins** in `tests/deps.json`, because they
are compiled into the product. The places HID-Fi leans on their internals, and
the test that guards each:

| Dependency | What HID-Fi relies on | Guarded by |
|---|---|---|
| ESP32 core `USBHIDKeyboard` | `press()` reads bytes >= 0x88 as raw usages, 0x80-0x87 as modifiers; `KEY_*` values; the en_US table | unit: canary, golden ASCII traces, the whole typing bench (it compiles the installed core's own source) |
| ESP32 core TinyUSB | The firmware defines `tud_descriptor_string_cb()` itself for OS detection; the core must still link it | board: `test_host_os`; perf: detection time |
| ESP32 core `USBHIDMouse` | Only one mouse object may exist (see CONTRIBUTING) | board: pointer mode checks in `test_v34_features` |
| ESP32 core build options | `USBMode=default` (TinyUSB) and `CDCOnBoot=default` | static: FQBN agreement |
| ArduinoJson 7 | `StaticJsonDocument` / `DynamicJsonDocument` / `createNested*` - **deprecated**, still compiled | build: warnings ratchet. A future major version will remove them: migrate to `JsonDocument`, `doc[key].to<JsonArray>()` and `add<JsonObject>()` first |
| WebSockets (links2004) | `onEvent` binary frames, `sendBIN`, `broadcastTXT`, client IPs | build; perf: WiFi tests |

**To move a pin:**

1. Install the candidate: `arduino-cli core install esp32:esp32@X.Y.Z` (or `lib install Name@X.Y.Z`).
2. `python tests/run.py doctor` - it reports the difference.
3. `python tests/run.py` - the unit tier compiles the **new** core's keyboard code;
   canaries, golden traces and the bench say whether it still behaves the same.
   The build tier says whether it compiles, and whether size or warnings moved.
4. Flash a board and run `python tests/run.py --all` on at least one OS -
   detection and USB enumeration only show on hardware.
5. Only then change `tests/deps.json`, update any golden file whose change you
   understand (`--update-golden`), and note the version in the CHANGELOG.

CI does steps 1-3 against the **latest** versions every Monday
(`latest-dependencies` job), so a breaking upstream release shows up as a red
run before anyone has to trip over it.

---

## Before a pull request

```bash
python tests/run.py                  # always - no board needed
python tests/run.py --all            # if you touched firmware, and you have a board
```

Paste `tests/.reports/summary.md` into the pull request, and say which OS the
hardware tiers ran on. CI runs the no-board tiers with strict pins on every push.

---

## Adding a test

- Put it in the tier folder that matches what it needs (`tests/static`,
  `unit`, `ui`, `compile` - the build tier - `hostsim`, `board`, `e2e`, `perf`)
  and mark it with that tier (`pytestmark = pytest.mark.<tier>`).
- Use the shared pieces in `tests/support/`: `board` (one serial session,
  snapshot/restore), `dashboard` (the page and a fake board), `sink` (the e2e
  page), `corpus` (text and independent expectations), `firmware` (facts read
  from the sketch), `linux_keys` (derived Linux codes), `perf` (budgets).
- Read facts from the source instead of copying them; a copied list drifts.
- Anything that sends input to the host belongs in `e2e`, through `drive`
  (it checks focus first) - never in `board`.
- A test that skips must say why. A test that cannot fail is not a test: if you
  add a property, add a planted bug to `tests/unit/firmware/mutations.json` that
  breaks it.
- Anything a person does on the dashboard is a scenario in
  `tests/ui/bdd/features/`, not a script - `.claude/skills/hidfi-feature`
  describes the steps, the shared vocabulary and the contract.

---

## Hooks: tests that run themselves

```bash
python tests/run.py install-hooks
```

points git at `tests/hooks/`: **pre-commit** runs static and unit (seconds),
**pre-push** runs every no-board tier, coverage contract and responsive matrix
included (about six minutes). `git config --unset core.hooksPath` turns them off.

For AI agents, `.claude/settings.json` registers `tests/hooks/claude_stop.py` as a
Claude Code **Stop** hook: when an agent finishes a turn that changed `hid_fi/`,
`tests/` or `docs/`, the static tier runs, and if the dashboard or its scenarios
changed since the last good run, the ui tier runs too (about five minutes). A failure is handed back to
the agent, which keeps working until it passes. It never runs a tier that touches
the board or the computer.

---

## When it goes wrong

| Symptom | Cause and fix |
|---|---|
| Every hardware test skipped: "no board found" | The COM cable is not plugged in, or is charge-only. `run.py doctor` shows the ports it sees |
| e2e skipped: "page was not started" | Open the printed URL and click Start within 4 minutes |
| e2e stops: "lost focus" | A notification or another window took focus. Close it and rerun - nothing was sent after focus was lost |
| Mouse tests skipped: "pointer lock not active" | Click Start again; some browsers drop pointer lock when the window changes |
| `board not left as found` | A test changed a setting and could not restore it. `tests/.reports/board_restore.json` says which |
| Linux: "no permission to read /dev/input" | `sudo -E`, or `sudo usermod -aG input $USER` and log in again |
| unit: "could not fetch the kernel's hid-input.c" | Offline. The Linux mapping tests skip; everything else still runs |
| A golden test fails after an intended change | Rerun with `--update-golden`, review the diff, commit it with the reason |
| The first burst of keys after an idle period is lost | The host suspended the board's USB. Expected; tests wait for `usb_mounted` before typing |
| macOS: two spaces arrive as `. ` | macOS's "Add period with double-space" (System Settings → Keyboard → Text Input), which Chrome honours. The board typed both spaces; the Mac e2e test accepts exactly that rewrite and nothing else |
| macOS/Windows: a large mouse move does not cancel exactly | The pointer stops at the screen edge. Exact pixels are checked on the board tier and on Linux (evdev has no edges); the page checks direction for large moves |
