# Changelog

All notable changes to this project.

The version string reported by `{"cmd":"status"}` is `firmware`, e.g.
`hid_fi_v3.8`.

---

## v3.8 - 2026-09-21

A password vault, a keyboard that grows with the screen, and Mac-or-PC
switching that is finally right.

> v3.7 was never released — its work is folded in here, so this is everything
> since v3.6.

> Verified on an ESP32-S3 N16R8 against Windows 11: detection settles to
> `windows` within the 3 s window, nothing is typed into the host, and Num Lock
> is never touched. The macOS half of the discriminator — that macOS re-reads a
> string descriptor index back to back and Windows does not — is taken from
> keyboardio's FingerprintUSBHost and QMK's `os_detection`, and has **not** been
> measured on a Mac by this project. Settings → Computer prints the raw counts,
> so one look on a Mac confirms or refutes it.
>
> The vault's gate is covered by `tests/test_vault.py` (37 checks on a clean
> board) and a live probe on a board holding real entries. **`vault_type`
> putting real keystrokes on the wire has not been machine-tested** — it would
> have typed a secret into whatever window had focus — so that one path is
> verified by inspection and by hand.

### Changed

- **The keyboard is tiered, not stretched.** It gains whole blocks as width
  allows, the way physical keyboards do: **compact** (12 columns, `123` symbol
  layer, under 500px) → **ANSI** with the function row, arrows and the
  `Del`/`Home`/`End` column (500px) → **+ the full navigation cluster** (TKL,
  1010px) → **+numpad**, a full 104 keys (1300px). A wide screen now gets more
  keys instead of the same few keys stretched across it, and rotating a phone to
  landscape or opening the trackpad deck in fullscreen moves up the tiers.
  - **Going up a tier never takes a key away.** An earlier `fn` tier put the
    function row at 790px, so `Esc` was on a phone, gone between 500px and 790px,
    and back on a laptop. The function row is now part of every tier above
    compact, and the 65% side column carries the keys the compact nav row
    reaches. A row is only hidden if its keys are reachable another way — the
    digits row used to go on short screens, which took the numbers away outright
    on layouts that have no `123` layer.
  - Every block carries the **same number of rows**, so the nav cluster and the
    numpad line up with the main block instead of dividing the same height into
    six rows against five.
  - The nav cluster's legends **size from its own width** with a container query.
    A fixed font fitted a laptop and cut `Home`, `PgUp` and `PgDn` in half on a
    25px column.
  - The ANSI block is a **60 column grid**, because ANSI is exactly 15u wide and
    that makes 1u four columns — so 1.25u, 1.5u, 1.75u, 2u, 2.25u, 2.75u and
    6.25u are all whole numbers and every row sums to exactly 60.
  - The three blocks are flexed **15 : 3 : 4**, their real proportions, so one
    key is the same width in all three.
- **A real inverted-T arrow cluster.** Up sits directly above Down with Left and
  Right either side. The arrows used to sit in a flat `← ↑ ↓ →` line.
- **The key beside the right Shift is the slash, not M.** Anyone reaching for `/`
  by position — which is how the row is read on any keyboard — used to hit M.
- **The Windows modifier cap is the `⊞` glyph** rather than the word "Win", which
  no longer fits. The Mac and Linux caps were already single glyphs, so all three
  are now consistent. The Menu key is an icon for the same reason.
- **Host OS detection is now passive.** The board no longer taps Num Lock and
  times the host's LED reply. Nothing is typed into the host, so there is nothing
  to undo.
- **A Mac is no longer reported as Windows.** The first pass at passive detection
  assumed macOS does not send the HID `SET_IDLE` request; measured on a real Mac,
  it does, so the signal separated nothing and every Mac read as a PC. The
  discriminator is now the shape of the host's **string-descriptor reads**: macOS
  asks for each string twice — two bytes to learn its length, then the whole
  thing — so the same index arrives back to back, string after string. Windows
  does it for the odd string too (measured on Windows 11 against this board: 11
  requests, 2 repeats), so the verdict is the *proportion* of repeats rather than
  a count. Seeing any of this means the firmware owns
  `tud_descriptor_string_cb()`, which the ESP32 core declares weak; it rebuilds
  the descriptor from the `USB` object's own manufacturer, product and serial
  strings, so the board still enumerates as exactly the same device.
- **A host that says nothing is reported as `undetermined` rather than guessed
  at.** Windows caches a device's descriptor strings, so a PC that already knows
  this board can be nearly silent on a replug. The dashboard asks you to pick.
- **A settled verdict is no longer revised by later traffic.** Only a new
  enumeration replaces it — a Caps Lock LED report or a Device Manager refresh is
  not the host changing its mind.
- **Detection re-arms whenever the USB bus drops**, so carrying the board from a
  Mac to a PC is picked up a few seconds after it enumerates, without a reboot.
  The previous answer stays on screen until a new one is reached, so a momentary
  glitch does not blank the UI.

### Fixed

- **The dashboard went stale between tabs.** Setting the access PIN in Settings
  left the Keys tab still saying "set an access PIN first" until the page was
  reloaded. Every write to the board now refreshes what depends on it, and the
  status poll notices a PIN set from another phone and rebuilds the affected
  cards. Nothing in the dashboard should need the Refresh button; that is now
  the rule rather than the exception.
- **A long entry name crushed the buttons next to it.** A saved email address in
  the password list pushed Type, Show and Edit into each other on a 375px phone.
  The label truncates now, the controls never shrink, and the row tightens on
  narrow screens — checked from 320px up.
- **The compact keyboard's shortcut keys stayed Windows combos on a Mac.** The
  nav row's `Ctrl+C`, `Ctrl+V`, `Ctrl+Z` and `Alt+Tab` were baked into the layout
  table while every other key around them followed the detected OS, so on a Mac
  four keys did nothing useful. They are now `⌘C`, `⌘V`, `⌘Z` and `⌘Tab` there.
- **A whole row of letters vanished in landscape.** The rule that drops the
  digits row on short screens selected `:nth-child(2)`, which followed the row
  *order* rather than the row — so once the layout gained a block structure it
  deleted `Tab Q W E R T Y U I O P [ ] \` instead. It hit every phone in
  landscape. The rule now targets the row by class, and the responsive suite
  fails if a visible keyboard is missing any letter.
- **The trackpad deck was capped at ANSI even in fullscreen**, where it has the
  whole screen. Fullscreen now reaches the full-size layout.
- **Opening the deck painted one frame at the previous tier**, because
  `ResizeObserver` reports a frame late. At a narrow width that meant ANSI keys
  squeezed to 15px. The deck toggle and fullscreen now rebuild synchronously.
- **Detection could stall forever.** It waited on `tud_mounted()` and an 800 ms
  echo window with no retry and no re-arm. Measured on one Windows laptop: the
  same board resolved in 5 s on one boot and was still `unknown`/`pending` 9 s
  into the next. A host that says nothing recognisable is now reported as
  `undetermined` and the dashboard asks you to pick.
- **A misdetected Windows host was left with Num Lock flipped.** The Windows
  branch undid its own toggle; the macOS branch did not.
- **`usbWakeHost()` slept 120 ms on the pointer hot path** whenever a host had
  suspended the bus — the one thing `loop()` is never allowed to do.
- **A pinned OS silently outlived the computer it was set on.** `status` now
  carries `host_os_detected` beside `host_os`, and Settings → Computer says so.
- **`flash_esp.ps1` died mid-flash after a successful build.** It sets
  `$ErrorActionPreference = 'Stop'`, and esptool 5.3.1 writes progress to stderr,
  which PowerShell turned into a terminating `NativeCommandError`. Every native
  tool call now runs through one helper that judges the tool by its exit code.
- `tests/test_inventory.py` opened the serial port with the bare
  `serial.Serial(port)` constructor, which asserts DTR and RTS and so reset the
  board on every run.

### Added

- **A password vault.** Twelve slots holding a purpose, a kind (PIN or password)
  and a secret of any length, which the board types for you — so a password is
  never typed on a keyboard that could be watched or logged, and a PIN is not
  assumed to be four digits. Keys → Passwords, above the shortcuts.
  - **Reading one back or typing one asks for the PIN, every single time**, with
    the same four-box prompt the board itself uses. There is no session that
    keeps the vault open, because keystrokes *are* the secret and the board
    cannot see which window they land in.
  - **Storing, renaming and deleting do not ask.** None of them discloses
    anything, and a PIN prompt in front of an act that cannot leak is a toll
    rather than a control.
  - **The gate holds on USB serial too**, which is the one exception to serial
    being ungated, and wrong guesses are rate limited there as well.
  - **The vault can have its own PIN**, separate from the one that unlocks the
    dashboard — the way a browser asks for your account password before showing
    a saved one. Forgotten it? It can be reset with the access PIN, and that
    erases the vault; otherwise the access PIN would quietly be a way to read
    everything and the second PIN would be decorative.
  - **Resetting the access PIN erases the vault** unless the old one is given.
    The dashboard passes it for you, so an ordinary PIN change keeps everything.
  - Listing gives purposes and kinds only — never a secret, not even its length.
    Editing never reveals: leave the field empty and the stored secret is kept.
    An entry saved as a PIN is refused unless it is all digits, and the field
    shows a number pad and strips anything else as you type.
- **A saved PC remembers which OS it is.** Unlocking a Mac and unlocking a PC
  are not the same keystrokes, and the board may be plugged into a different
  computer by the time a saved password is used — Esc into a Mac login window
  collapses the password field. `pc_save` now takes `os`, defaulting to whatever
  the board is plugged into when you save, `pc_list` reports it, and `unlock`
  uses the slot's OS rather than the attached one. Saved PCs can also be
  **edited** from the dashboard: passing an occupied slot updates the name and
  OS, and the password may be left empty to keep the stored one, since the page
  has no way to show it back. Forgetting one now names the PC in the
  confirmation and says the password will be deleted.
- **A numpad that sends real keypad usages.** `KP0`–`KP9`, `KPDOT`, `KPPLUS`,
  `KPMINUS`, `KPSTAR`, `KPSLASH` and `KPENTER` are distinct HID keys from the
  number row, and applications that tell them apart see the difference.
- **`status` reports the evidence**, not just the verdict: `host_os_detected`,
  `detect_phase`, `usb_str_reqs`, `usb_str_rereads`, `usb_str_seq`,
  `usb_set_idle`, `usb_ctrl_reqs` and `usb_led_reports`. Settings → Computer
  shows the same counts and the raw sequence of string indices the host asked
  for, so a detection argument can be settled by reading numbers on the phone.
- **Keyboard geometry is a regression test.** `tests/test_dashboard_static.py`
  evaluates every layout table in node and fails if an ANSI row stops summing to
  60, if Up stops sitting above Down, if the slash leaves the right Shift, if the
  numpad cells overlap, or if any key name is missing from `mapKeyName()`.
- **`tests/test_host_os.py`** — detection settles, reports its evidence, survives
  a pin/auto round trip, and the host's Num Lock is read before and after to
  prove nothing was typed.

---

## v3.6 - 2026-09-19

Making the board painless on a Mac, and honest about which computer it is on.

> Verified on an ESP32-S3 N16R8 against a Mac: Num Lock detection resolves to
> `mac` about 2.3 s after boot, the manual override persists across a reboot and
> `auto` re-probes, the gesture/app-switcher/keyboard mappings switch correctly
> both ways, `lock` fires `Ctrl+Cmd+Q`, and unlock types the password at the
> macOS login window. Still needs a confirming run against a physical Windows PC.
> Detection is a brief transient: a lock issued in the first few seconds after
> connecting, before the probe settles, falls back to the PC shortcut — see
> [docs/MACOS.md](docs/MACOS.md).

### Added

- **Automatic Mac-vs-PC detection.** The board toggles Num Lock once and watches
  for the host's LED reply — Windows and Linux echo it, macOS has none and stays
  silent — and switches its behaviour to match. The probe is a non-blocking state
  machine, runs once after the host settles, never while the host is asleep, and
  taps Num Lock straight back so it leaves no toggle behind. Reported as `host_os`
  / `host_os_source` in `status`, and pushed as a `host_os` event the moment it
  settles.
- **`set_host_os`** (`mac` | `windows` | `linux` | `auto`) pins the OS on the
  board or re-arms the probe, with a **Computer** selector in Settings. A manual
  choice always wins — needed because the probe cannot tell Linux from Windows.
- **Mac-aware shortcuts.** Lock sends `Ctrl+Cmd+Q` instead of `Win+L`; the app
  switcher holds **Command** instead of Alt; and the gestures (Mission Control,
  Spaces, copy/paste/undo, tabs, back/forward, zoom) send their macOS combos. All
  driven by the detected or pinned OS, on both the firmware and dashboard sides,
  so the two never disagree.
- **A Mac setup panel** in Settings, and [docs/MACOS.md](docs/MACOS.md). The
  Keyboard Setup Assistant asks you to identify the *board's* keyboard, not your
  laptop's; two buttons send exactly the keys it asks for (always Z and /, since
  the board is US ANSI), so nobody has to hunt for key positions. The panel also
  explains the "Allow accessory" prompt and that a first-ever plug-in must be
  approved while the Mac is unlocked.
- **`flash_esp.sh`** — the macOS/Linux twin of `flash_esp.ps1`. Same four-partition
  write that leaves NVS alone, same flags and exit codes, auto-detects the bundled
  esptool and boot_app0, and reads the board back to verify. It finds the CH343
  port under every name macOS gives it — `cu.wchusbserial*` with WCH's driver and
  `cu.usbmodem*` with the built-in one — so it works with no driver installed.

- **OS-aware defaults across the dashboard.** The quick-action seeds, their
  preset palette, and the trackpad gesture-tile labels all follow the detected
  computer: a Mac gets Cmd-based mute, a `Cmd+Shift+4` screenshot and a Mission
  Control tile where Windows has the projector menu, Task View and Desktop tiles.
  A saved or edited set is never overwritten.

### Changed

- **A proper two-Shift keyboard.** Both Shifts now flank the letters — left
  before Z, right after M — on both the Type tab and the trackpad deck, on both
  layers. Caps moves to the navigation row and the arrows become one inline
  cluster. Modifier caps use the platform glyphs on a Mac (⌘ ⌥ ⌃), which also
  stops "Cmd" clipping to "C…" at phone width. The 10-column grid, two-finger
  chording and every responsive breakpoint are unchanged.
- The Shortcuts filter and the app-switcher modifier now follow the detected
  computer OS by default instead of guessing from the phone's browser; tapping a
  shortcut OS tab still pins your own choice.

### Fixed

- **Unlock did nothing on a Mac.** The wake step pressed Esc, which collapses the
  macOS login field, so the password typed into nothing. On a Mac it now wakes
  with a mouse jiggle plus a Shift tap and waits longer for the login window; the
  Unlock button also offers a forced retry instead of dead-ending on a stale
  "already unlocked". Ctrl+Alt+Del, which is Windows-only, is hidden on a Mac.
- **Docs pointed at a folder that does not exist.** Every reference to
  `usb_hid_unlock/` is now `hid_fi/`, the actual sketch folder, and the flashing
  guide's test command points at `tests/` rather than the old path.

---

## v3.5

The release that made the trackpad behave like a trackpad. Every entry under
Fixed came from using the board rather than from reading the code, and each one
is covered by a test that fails without the fix.

### Added

- **A real scroll wheel in the mouse view.** The middle button was a button you
  tapped. It is now a ridged wheel you drag: 16 px of travel is one detent, each
  detent sends one scroll notch with a haptic tick, and it follows the Natural /
  Classic setting. A tap that never moved still middle clicks, so nothing was
  taken away.
- **A keyboard on the trackpad card.** The corner toggle flips the same card
  between the pad and a compact keyboard with its own type-and-send field, so a
  click and the typing that follows it no longer cost a trip across the app. Both
  faces are locked to one height, so flipping never moves the page under your
  thumb. The dedicated Type tab is unchanged and still carries the full keyboard.
  The two keyboards share one host state: a modifier latched on either lights up
  on both, and a key held on one can be struck from the other.
- **Swipe sideways to change tabs.** Deliberately hard to trigger: it is refused
  on the trackpad, on any key or control, on a second finger, after a pause that
  looks like a hold, on anything slower or shorter than a flick, on a diagonal, on
  a mouse, in fullscreen, and while a button or key is being held. The listeners
  are passive, so the gesture can never swallow a scroll.
- Flipping the card releases whatever the other face was holding - a grabbed
  mouse button on the way in, a held key on the way out - so neither half can
  strand something down on the PC.
- **A live three-finger app switch.** Sliding three fingers sideways now opens the
  switcher while your fingers are still down and steps window by window as you
  move, committing when you lift - the way a Windows touchpad does it. It used to
  decide only once you let go, which meant choosing blind. Turn off *Three finger
  app switch* in Feel and the old virtual-desktop swipe comes back, which is what
  macOS does.
- **Scroll inertia.** A flicked two-finger scroll coasts and slows, on the scroll
  axis only.
- **Both MAC addresses** on the network panel. `mac` is the station side, the one
  your router's DHCP table and MAC filters see; `ap_mac` is what a phone sees when
  it joins the board. They are different addresses on the same radio.
- **`{"cmd":"storage_info"}` and a Settings panel showing it** — how much of the
  board's permanent memory is in use, and what is actually kept there. Passwords
  and PINs are reported as `..._set: true/false`; the values themselves never
  leave the board.
- **`{"cmd":"clients"}` and a Device panel listing them** — every station on the
  access point, with MAC, IP, signal quality, and whether it currently has the
  dashboard open. Device names are deliberately absent: a WiFi client is never
  obliged to give one, and phones randomise their MAC as well. The panel says so
  rather than leaving it a mystery.
- **A "Scan again" button** on the network list, with a count. A scan is a
  snapshot, and the network you are waiting for may not have been up yet.
- **Scaling for very large displays.** The root scales in steps from 2560px
  upward, reaching 3.5x at 8K.
- **A colophon** in Settings, and `-Force` on the flasher for scripted erases.

### Fixed

- **A two-finger scroll could throw the cursor across the screen.** Two fingers
  never leave the glass at the same instant, and the moment one lifted the other
  was handed the pointer - so the tail of every scroll became a fast pointer move,
  which on a desktop can land on a window control. Pointing is now gated on the
  contact count the way a real trackpad does it: once two fingers have been down
  the gesture owns that contact until every finger lifts, and you touch again to
  point. A regression test drives the exact motion and measures 0px of cursor
  travel; with the fix removed the same test measures 257px.
- **Lifting two fingers flung the pointer.** Scroll had no velocity of its own and
  borrowed the pointer's, which is only ever written by one-finger moves, so a
  scroll ended by replaying whatever the last drag had left behind. Scroll now
  carries its own velocity and can no longer move the cursor at all.
- **A three-finger swipe straight up could be read as a sideways one.** The
  direction was measured from the last finger to lift back to the *first*
  finger's starting point - two different contacts, sitting about 80px apart - so
  the gap between your fingers counted as travel. Each finger is now measured
  against where it personally started.
- **A three-finger contact could turn into a scroll** as the fingers came off one
  at a time, for the same reason a two-finger one could turn into a pointer move.
  Both are gated on the peak contact count now.
- **The mode label kept saying "grab" after grab was switched off**, until you
  happened to touch the pad. It was only refreshed from pointer events, so the
  button changed the state without changing the label.
- **The dashboard could be pinch-zoomed and was hard to get back from.** Double
  tap no longer zooms, pinch is refused as it happens rather than only at the
  start, and if the page ends up zoomed regardless a bar offers to reset it.
- **The board ignored everything for 15 seconds after every power on, if a saved
  network no longer answered.** Boot started the access point and both servers,
  then sat in a busy-wait until the home-network join resolved. A join that fails
  takes the full timeout, and for all of it the dashboard would not load and the
  serial console would not answer either — exactly when someone had just plugged
  the board in and was trying to use it. The join now runs through the same state
  machine as every other join, and `loop()` is never held up.
- **The network scan was the one endpoint with no access control.** `/api/scan` is
  a plain GET and never passed through the gate that protects every command, so
  any device on your home network could list the networks around you without a
  PIN. It now answers to the same rules, with the PIN sent in a header rather than
  a query string so it stays out of history and logs.
- **iOS rewrote the access point name and password by itself.** A text field
  followed by a password field reads to Safari as a login form, so tapping
  AutoFill while joining a network silently refilled the access point's own
  credentials — and `autocomplete="off"` does not stop it. Those two fields now
  start read-only and release on first touch, which password managers skip.
- The join password field has a **Show** toggle, so a wrong autofilled password
  is visible before you send it rather than after it fails.
- **`ap_configure` silently removed the access point password when none was
  given.** The dashboard field said "leave blank to keep the current password"
  while the hint above it said blank meant open — and blank really did drop
  security. Removing the password is now an explicit `"open":true`, backed by its
  own checkbox; an omitted password keeps the existing one.
- **Access point changes never survived a reboot.** Boot read `ap_ssid` and
  `ap_pass` back out of NVS, but nothing ever wrote them, so a rename lasted only
  until the next power cycle. `ap_configure` now saves both.
- **An open access point came back locked.** An empty stored password cannot be
  told apart from "never configured", so a deliberately open network reverted to
  the built-in default on boot and phones could no longer join it. The choice is
  now recorded as its own `ap_open` flag.
- **Haptics silently did nothing on iPhone.** iOS Safari has never implemented the
  web vibration API. The Feel panel now says so rather than showing a switch that
  cannot work, and uses the one haptic iOS does give a web page.
- **The app was unreadable on a 4K display at 100% scaling.** A 3840 pixel wide
  screen reports 3840 CSS pixels, so every size in the sheet was drawn at its
  literal value and the interface read like a stamp.
- **The fullscreen pad overhung a scaled display.** It was sized with `100vw` and
  `100vh`, and viewport units do not follow root zoom. `inset:0` does.
- **The logo appeared twice once the window was wide enough for the sidebar.** The
  top bar carries a mark for the phone layout, where no sidebar exists to carry
  one, but it was never switched off when the sidebar came back.
- **Long key labels were clipped on phones.** "Caps" and "?123" needed one pixel
  more than their column allowed at 430px and under, so both rendered as "C...".
  Caps is now the standard glyph, matching the arrow already used for Shift.
- **Cards were wider than the screen below about 310px.** A grid item will not go
  under its own content width, and one stubborn slider row set the width for every
  card in the row.
- **A full erase now asks first.** Nothing it removes can be backed up, because
  the board never returns a password or a PIN to any command, so `-Erase` makes
  you type ERASE before it runs. `-Force` skips the question for scripts.
- **The flasher stopped claiming the default access point password** after one had
  been set. It says so only while the network still carries its factory name.

### Verified

- Flashing onto a chip erased of all 16 MB: the board boot-loops on
  `invalid header: 0xffffffff`, and a plain `.\flash_esp.ps1` brings it back.
- 67 viewports from 280x653 to 8192x4320, every aspect ratio from 0.15 to 6.67.
- 26 gesture cases, 24 feature checks, 34 power and health checks, 23 inventory
  checks, all on hardware.

---

## v3.4

The release that made the cursor usable and turned the dashboard into a product.

### Performance

- **Pointer traffic moved to binary WebSocket frames.** The ESP32 core's
  `WebServer` has no HTTP keep-alive, so the previous POST-per-event cost a full
  TCP handshake every time. Serial round trip went from 22.4 ms to ~15 ms and the
  cursor stopped falling behind the finger.
- Removed `delay(10)` from `loop()`.
- Dropped serial echo from the input hot path — ~110 bytes at 115200 baud is about
  10 ms of blocking per event.
- Large pointer deltas are split across multiple HID reports instead of being
  clipped at ±127.
- Horizontal pan wired up; the fourth argument to `move()` had been there all
  along and was never passed.

### Added

- **Shortcuts browser** — hundreds of macOS, Windows and Linux shortcuts, grouped,
  collapsible and searchable, filtered to the OS you pick. Every entry resolves to
  a key the firmware can actually send.
- **App switcher** — holds Alt down so the host keeps its own switcher open. Slide
  in two axes to move along a row or between rows, release to raise the window.
- **Saved PC passwords** — stored on the board, never readable back, and refused
  unless an access PIN is set.
- **4-digit access PIN** with escalating lockout: one free retry, then 5s, 15s,
  60s, then 5 minutes per attempt.
- **Access point name and password** changeable from the dashboard, with a
  reconnect panel that carries the new credentials.
- **Keys moved onto the binary WebSocket channel.** Typing used to cost a 1 KB
  JSON parse, a long `strcmp` dispatch walk and a reply frame nobody reads, per
  keystroke. It is now a three byte frame with no reply. The key *name* is sent
  rather than a resolved HID code, so `mapKeyName()` stays the single source of
  truth and the dashboard never needs its own copy of the keymap.
- **AP SSIDs accept emoji and accents.** Validated as UTF-8 with a 32 **byte**
  limit, since one emoji uses four. The dashboard shows a live byte count.
- Enter with an empty type box now sends a bare Return instead of refusing, and
  the phone's own Go key submits.

### Fixed

- **The fullscreen trackpad could not be exited.** The only way out sat at the
  bottom of the screen, underneath Safari's own toolbar, which
  `env(safe-area-inset-bottom)` does not report. There is now a labelled exit at
  the top of the pad, the bottom bar is lifted clear by a floor value, Escape
  works, and the app's tab bar is hidden while fullscreen.
- **The keyboard was clipped on 320px screens.** `minmax(300px,1fr)` refuses to
  go below 300px, so the card ended up wider than the viewport and was cut off by
  `body{overflow:hidden}` with no way to scroll to it. Now `minmax(min(300px,100%),1fr)`.
- **The up and down arrows were misaligned.** Flexbox key widths depend on how
  many gaps a row has, so rows with different key counts never lined up. Every
  row is now the same ten column grid; measured offset is 0px at every width.
- Function keys shrank to 22px on a phone. That row now wraps and never goes
  below 40px, and the navigation row wraps rather than truncating `Ctrl+C` to `Ct..`.
- Key labels could be selected by fast repeated taps, popping the copy bar over
  the keyboard.
- The gamepad prompt's icon, text and button were not on a shared centre line.
- **Static IP addressing** for the home network, with live feedback as the join
  runs. Nothing on the ESP32 can detect that a requested address is already taken
  — a static client never asks permission — so the board checks the address it
  actually ended up with, and retries once on DHCP if the attempt times out. It
  reports which of the three happened rather than guessing.
- **Power modes** — `performance`, `balanced` (default) and `saver`. Balanced
  holds the radio awake while a dashboard is connected and lets it sleep when none
  is, so the saving costs nothing anyone can feel.
- **`ap_auto_off`** — stops the access point once the board is reachable over the
  home network and nobody has used the AP for ten minutes. Holding BOOT for two
  seconds always restores it.
- **Exposure gate** — with the board on a home network and no PIN set, wireless
  control from outside the access point subnet is refused. Read-only commands and
  `set_auth` stay open so the PIN can be set from where the user is standing.
- `heap_floor` and `heap_low` in status, read from the allocator's own low-water
  mark.
- Gamepad, media knobs, macros, quick actions, presenter controls, fullscreen
  trackpad, per-view help, and a mouse-shaped button layout.

### Fixed

- **`connectWifi()` blocked `loop()` for up to 15 seconds.** That loop services
  the web server, the socket heartbeat and the pointer stream, so joining a
  network froze the dashboard at the exact moment the user was watching it for
  feedback, and could drop the socket outright. It is now a state machine ticked
  from `loop()`.
- **`WiFi.scanNetworks()` blocked for 2–4 seconds** for the same reason. The scan
  is now started asynchronously and polled.
- `ap_configure` restarted the access point before its own reply could reach the
  phone that asked for it. The restart is deferred by 300 ms.
- WebSocket text frames were copied twice per command (`malloc` + `String`) and
  were unbounded, so a large frame could exhaust the heap. Frames are now capped
  and copied once. Serial lines are capped too.
- The heap watermark was sampled at 1 Hz, which missed exactly the transient dips
  it existed to catch. It now reads `ESP.getMinFreeHeap()`, which the allocator
  maintains exactly.
- In balanced power mode the radio only woke on the first pointer packet, so the
  first movement after a client connected could feel slow. Connecting is now
  enough to wake it.
- Double click worked only via the explicit button; tap-and-a-half grabbed
  immediately so the OS never saw a second click.
- Fullscreen trackpad vanished — `contain: layout` makes an element a containing
  block for `position: fixed` descendants, so the pad anchored to the card and
  collapsed to zero height.
- The HTTP API accepted a PIN inline without counting the attempt, which let the
  lockout be bypassed entirely. Both paths now share one check.
- Held keys and mouse buttons are released when the last WebSocket client
  disconnects, so a dropped phone cannot leave Alt stuck down.

### Changed

- Test scripts moved to `tests/`.
- LED controls removed from the dashboard — the WS2812 is not wired to GPIO48
  without bridging the board's solder pads, so the buttons could never work. The
  commands remain on the serial and HTTP interfaces.
- Licensed under PolyForm Noncommercial 1.0.0.

### Known limitations

- The board types **US-layout ASCII**. Symbols and passwords come out wrong on
  other keyboard layouts.
- The board cannot read the host back, so mute and volume are shown as intent.
- Traffic is plain HTTP and unencrypted WebSocket. The network is the security
  boundary, which is why joining a shared network demands a PIN.
- Radio sleep and pointer smoothness are genuinely opposed. `balanced` avoids the
  conflict by timing rather than resolving it; `saver` really does stutter.
- CPU frequency scaling is not offered. It would change the clock the UART derives
  from, and the UART is the recovery path.
