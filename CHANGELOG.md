# Changelog

All notable changes to this project.

The version string reported by `{"cmd":"status"}` is `firmware`, e.g.
`hid_fi_v3.8`.

---

## v4.0.1 - 2026-10-22

HID-Fi Companion's screen sharing, above all on Windows: found by an overload
test and a Windows 11 test VM. The board's firmware is unchanged except for its
version number (no need to reflash for this).

### Fixed

- **Screen sharing stuttering at 3-4 fps on Windows**, worst with two monitors
  and several viewers, from a field log. Reproduced on a Mac by
  `companion/tests/overload.py` (six viewers, two monitors, swinging and
  dropping links, a stalling device, rejoins, jittery WiFi) and fixed:
  - *Keyframe storm:* a viewer that joined or changed size while busy made
    every frame a keyframe for everyone on its stream (125 of 125).
  - *Quality flapping:* a noisy WiFi estimate switched size every few seconds,
    each switch a keyframe. A size change now needs the link to stay changed
    (1 s down, 3 s up); real packet loss steps down at once.
  - *The estimate sinking to a tenth of the link:* after a still screen the
    WebRTC library cut its estimate to 85% of what an idle screen had sent; its
    pacer also held bursts at 1.1x the estimate. A patched copy of the library
    (`companion/vendor/str0m`, see `PATCH.md`) does what libwebrtc does.
  - *Probes that could not run:* the picture filled the whole estimate, so the
    library never probed its way back after a drop. It now gets 60%.
  - *A rule meant for a still screen* sent a keyframe after 2 s without a frame
    - on a thin link, 5 of every 6 frames.
  - *The Windows encoder* ran up to 2.5x over its bitrate: now a hard ceiling.
- **Light content was held to 15 fps and half size on thin links** by fixed
  thresholds. A viewer now gets the largest size whose cost for what is on
  screen fits its link; frame rate is lowered only when the encoder really drops
  frames.

- **Windows: the app did not start on a clean PC** ("VCRUNTIME140_1.dll was not
  found") - it needed Microsoft's Visual C++ runtime, which GitHub's build
  machines have and a fresh Windows does not. The C runtime is now inside the
  `.exe`; CI checks it stays that way.
- **Windows: full size ignored its bitrate** - 27-37 Mbit/s for busy content
  whatever the link (the encoder's rate control was set too late). Every
  full-size viewer on Windows flooded the WiFi, and the PC's own uplink with it
  (the dashboard's mouse waited behind the video). Now it follows its target;
  CI checks it (`rate_check --check`).
- **Windows: asking for a keyframe broke rate control for good** (and viewers
  ask whenever they join or lose packets): a keyframe now comes from a fresh
  encoder. **...and frequent rate changes broke it too** (its buffer was smaller
  than one 1080p frame). CI checks both.
- **Keyframes are paced by their cost:** at most half a stream's rate (one a
  second on a good link, up to 5 s apart on a thin one) - one a second at 1
  Mbit/s overran Windows' encoder 10x and starved macOS's.
- **Two monitors no longer share one encoding thread:** each watched monitor is
  captured and encoded on its own, so a computer encoding in software
  (Windows) uses more than one core.
- **A forced keyframe every 20 s instead of every 4 s:** a 1080p keyframe arrives
  as a short freeze; keyframes now come when a viewer needs one.

### Added

- **A Windows 11 test VM and Windows builds on the Mac**
  (`companion/tests/windows`): how the bugs above were found.
- **A third size, quarter, for very thin links:** such a viewer gets its own
  small stream instead of setting the pace for everyone at half size.
- The app's log says how long the slowest encoding turn took, and (at debug
  level) when each encoder was made.

## v4.0 - 2026-10-22

The computer's own screen on the dashboard, with the new HID-Fi Companion app
(macOS, Windows, Linux): every monitor, six viewers, the wall. Plus the fixes
found by testing it live.

### Added

- **See the computer's screen: the Screen tab and HID-Fi Companion.** A small
  app for the computer the board is plugged into (macOS, Windows, Linux) shows
  its screen live on the dashboard - above the trackpad on a phone, beside it on
  a tablet, floating over any tab, or alone on another screen (`#watch`). Video
  goes from the computer to the viewer directly and encrypted (WebRTC); the
  board pairs them with a six-digit code and vouches for the computer. Hardware
  H.264 on macOS and Windows, Cisco's OpenH264 on Linux; nothing is captured
  while nobody watches; a viewer on a weak link gets a smaller picture instead
  of slowing the others. Install: `docs/COMPANION.md`, or the steps on the
  Screen tab itself.
- **Every monitor at once (Screen tab, `#wall`).** One, two, three or four of the
  computer's monitors in a grid, each live; full screen one or all; or each in its
  own window (`?d=<monitor>#watch`) to drag to any screen. Each viewer also picks
  its own monitor, and one unplugged mid-view hands over to the main display.
- **Six viewers per computer** (was four): a wall of four and a phone or two.

### Fixed

- **The dashboard by its .local name stalled on Macs and iPhones** - 5 s for
  every lookup (they ask for an IPv6 address and the board never answered), so
  a refresh or a reconnect looked dead. The board now has an IPv6 link-local
  address, so the name answers at once (measured 5.0 s -> 0.003 s; page by name
  in 0.7 s). Only the link-local address: no address from the router (which
  could expose the dashboard to the internet where a router lets IPv6 in), and
  every request or socket from a non-local IPv6 address is refused.

- **The board served only 5 dashboard pages, and the 6th said "offline".** The
  WebSockets library has its own limit (5) and never saw the firmware's 8; three
  phones with a watch window each reached it. `hid_fi/build_opt.h` now gives
  every compiled file, libraries included, the same 8 (a compile-time check
  fails the build if they ever differ, and the flash scripts rebuild from clean
  when that file changes). One page more than the board can serve now says
  "This board is full" and connects by itself when one closes. Measured on a
  board: 8 pages at once, 9-13 ms answers, the 9th told and then let in.

- **A paired computer never showed again after the phone slept.** A reply lost
  with the board connection, or held back by the PIN prompt, was never
  answered, so the Screen tab waited for its ticket forever. Every request now
  gets an answer (nothing, if nothing came), and the Screen tab asks again as
  soon as the link is back.

- **"5 connected" with 3 phones.** The header counted pages; it counts devices
  now (`ws_devices` in status), and the list says "2 pages open".

- **Settings: Connected clients and Storage no longer sit on "Asking the
  board..." / "Reading...".** Both were read once, over HTTP, as the page
  loaded - before the access PIN was entered - so a fresh tab got "PIN
  required" and the lists never filled in until their refresh buttons were
  tapped. When they did load, they described the moment before the page's own
  socket existed, so a dashboard could read "No dashboards are open". They now
  load over the socket when it opens, after every sign-in, and each time
  Settings is opened. Reproduced and verified against a v3.11 board; two
  regression scenarios in `settings.feature`.

- **A Mac on macOS 15.6 was detected as Windows, on every boot.** It reads each
  string in pairs (`2,2,1,1,3,3,4,4,0`) but reads the language table once, which
  left 4 repeats of 9 - one short of the "half" rule - so Mac shortcuts and
  gestures were Windows ones. A host now also reads as a Mac when three or more
  strings came in back-to-back pairs and pairs are three quarters of the strings
  read. The measured Windows 11 pattern (2 repeats) stays Windows. Measured on the
  owner's Mac (5 of 5 boots wrong before); the rule lives in `hid_fi/host_detect.h`
  with 11 real and pinned sequences and 5 planted bugs in
  `tests/unit/test_host_detect.py`. `status` adds `usb_str_paired`, `usb_str_seen`.

### Added (not yet released or flashed)

- **Screen tab: see the computer's own screen.** With HID-Fi Companion running
  on the computer, its screen appears live in a new Screen tab, above the
  trackpad on a phone held upright and beside it on anything wider. Pinch,
  double tap, Ctrl+wheel or the buttons to zoom; the float button keeps the
  picture on top, draggable and resizable, while you use the other tabs. Pairing
  happens in the same tab: tap Pair, type the six digits the computer shows.
  The picture goes from the computer to the phone directly (WebRTC, encrypted)
  and stops whenever nobody can see it.
- **Watch only** (`#watch`, or "Open on its own" on the Screen tab): the picture
  alone for a TV, tablet or second monitor while another device controls; a
  bar that fades, full screen, keys F, +, -, 0. Up to four screens watch one
  computer; a fifth waits and joins by itself.
- **Install guide** linked from the Screen tab: [docs/COMPANION.md](docs/COMPANION.md).
- **Board side** (`hid_fi/companion.h`): pairing with a committed six-digit
  code, heartbeats and one-use viewer tickets (`POST /api/companion`,
  `companion_list`, `companion_pair`, `companion_unpair`, `screen_ticket`).
  No access PIN, no screen sharing; a PIN set without the old one removes every
  paired computer. See `companion/DESIGN.md` and SECURITY.md.

---

## v3.11 - 2026-10-20

Remembers your networks, like a phone - take it from one home to another.

### Home network

- **Up to five saved networks.** Adding a network no longer replaces the last
  one: the board keeps them, most recently used first, and joins **whichever is
  in range**, strongest signal first - at home, at your other home, wherever it
  is. Settings lists them, with which one is connected and which have worked;
  each can be forgotten on its own (`wifi_networks`, `wifi_forget`). Reset WiFi
  still forgets them all.
- **Looks before it joins.** When it is not on a network it scans first (about
  two seconds) and only joins a saved network it can see - instead of blindly
  trying the last one for 15 seconds at a time.
- **Away from all of them, it stays out of the way.** With no saved network in
  range it scans every 30 seconds for ten minutes, then every two minutes, so
  the board's own WiFi stays smooth for the phone using it. Settings says "none
  of your saved networks in range". A hidden network (which never shows in a
  scan) is still tried now and then.
- **A new network that does not work never strands the board.** A wrong
  password is not retried until it is entered again, and a new network that
  fails rests for five minutes - meanwhile the board goes back to one that has
  worked.
- Upgrading keeps your network: v3.10's single saved network becomes the first
  entry, still marked as having worked. The v3.10 settings keep mirroring the
  first entry, so going back to v3.10 still finds it.

### Tools and tests

- The GitHub test workflow runs only when started by hand (no mails on push);
  the local run is the gate (RELEASING.md).
- `tests/setup_linux.sh` installs Chromium's libraries as the user (it failed
  under sudo on a normal user - which is what WSL users are).
- Timing-sensitive tests - layout speed, and the Linux typing replay - run
  alone after the parallel phase.
- RELEASING.md and `tools/make_release.sh`: every release is a version, a tag,
  a changelog entry and a GitHub Release with the firmware zip; published tags
  never move.

---

## v3.10 - 2026-10-19

Owning the board: a password unique to each board, a first PIN only its owner
can set, and recovery that needs the board in hand.

> **Upgrading from a board on the old shared password (`hid12345`)?** The first
> time v3.10 starts it makes a password of its own, so phones have to rejoin with
> it. The flasher prints it at the end of the flash. A password you chose is kept.

### Security

- **Each board makes its own WiFi password.** Every board used to ship with
  `hid12345`, written in the README, so anyone in radio range who had seen the
  project could join. A board without a password of its own now makes one from
  the chip's hardware random number generator - twelve easy-to-type characters,
  about 59 bits - as the UK PSTI Act and ETSI EN 303 645 require. It is never
  derived from the MAC, which the board broadcasts. Both flashers print it after
  flashing. Settings recommends choosing your own, and warns while a board still
  uses `hid12345`.
- **Only the owner can set the first PIN.** With no PIN, anyone on the home
  network could set one and take the board over. Over WiFi the first PIN now needs
  the board's WiFi password (`set_auth` with `ap_password`); an open access point's
  first PIN can only be set from that access point. Over USB nothing is needed.
- **Recovery is over the USB COM cable only:** `.\flash_esp.ps1 -Recover`,
  `./flash_esp.sh --recover`. Show the WiFi password, set a new one, or reset the
  access PIN - keeping the vault when it has its own vault PIN, and otherwise
  erasing it only after a typed ERASE. Nothing can be recovered over WiFi, by a
  stranger or by the owner. New serial-only command `ap_password`.
- **Wrong PINs lock out the device that typed them**, not the whole board, so a
  stranger guessing PINs can no longer lock the owner out. 20 wrong PINs from the
  home network within 10 minutes lock the home network out for 10 minutes; the
  board's own WiFi and the USB cable are never locked out.
- **Connections that never sign in are closed after 20 seconds**, so silent
  connections cannot hold every slot and keep the owner out.

### Home network - built to stay up

- **It never gives up on a network that has worked.** A board that started before
  the router (a power cut brings both back at once) used to try once and stop for
  good; the core library treated our timed-out join as "stay off". One supervisor
  now owns reconnecting: 1 s after a drop, backing off to every 30 s, forever.
- **A new address is noticed and told.** The board records the previous address,
  moves its `.local` name to the new one, and tells every open dashboard, which
  says "address changed". Settings shows the address before, the drops since
  start, why the router let go, and which address to bookmark.
- **It is never unreachable.** With the home network gone for 20 s the board's
  own WiFi comes back even if auto-off had switched it off.
- **A dashboard that lost the board says how to find it**: its `.local` name, its
  own WiFi, or the USB cable - instead of spinning forever.
- **Optional: share the home internet** with phones on the board's own WiFi
  (Settings → Access point). Off by default, warns first, needs an access PIN to
  switch on over WiFi, and runs only while the board is on its home network.
- `python3 tools/hidfi.py find` lists the boards on your network and their
  addresses (mDNS, `_hidfi._tcp`).
- A password the router refuses three times on a known network slows retries to
  every 5 minutes and says so; a new network with a wrong password stops at once.
- Rehearsed on a real board without touching the router (`net_fault`, USB only):
  dropped, router off for 40 s, router off at boot, new address, AP auto-off -
  `tests/board/test_home_network.py`. During those runs the real router really did
  move the board from .107 to .100; network tests now ask the board where it is.

### Actions - for agents and scripts

- **Say what to do, not which keys.** `{"cmd":"do","a":"copy"}` presses Cmd+C on a
  Mac and Ctrl+C on Windows and Linux - the board knows which computer it is in.
  82 actions, each taken from the OS vendor's own shortcut page and identical to
  the dashboard's tile for it (a static test holds the two together).
- `{"cmd":"actions"}` lists the ones that work on this computer in about 730
  characters - a choice list for a typed-decision model, or a few hundred tokens
  for an LLM.
- `{"cmd":"open","app":"Calculator"}` opens an app through the computer's own
  search (Start, Spotlight, GNOME's overview); `"run"` runs a command or URL
  through Win+R or Alt+F2.
- `{"cmd":"steps","s":[...]}` runs up to 32 steps in one call and says where it
  stopped if one cannot be done.
- `tools/hidfi.py`: a command line and an MCP server, so an agent gets five small
  tools. See docs/AGENT_CONTROL.md.

### Shortcuts

- Corrected against the vendors' pages: Windows 11's Print Screen now selects an
  area, so *Whole screen* is Win+PrtScn; GNOME's Print opens the screenshot tool
  and Shift+Print takes the whole screen (they were the wrong way round); GNOME
  switches windows of one app with Super+\` and workspaces with Super+PgUp/PgDn;
  macOS *Paste and Match Style* is Option+Shift+Cmd+V; Ctrl+Alt+Del on GNOME is
  the power-off dialog, not a security screen.
- About 70 more, every one from the vendor's own page: Finder's folders and views,
  hiding and minimising apps, Dock and menu-bar focus, Windows' notification,
  widgets, snap layouts, cast and Game Bar, moving windows between screens and
  workspaces, GNOME's app grid and notification list, and Mac text editing keys.
- **Open an app** on the Keys tab: type a name or tap one of the computer's usual
  apps, and the board opens it.

### Dashboard

- Settings → Access PIN asks for the board's WiFi password for the first PIN, and
  says why in one line. **ⓘ** opens *PIN, passwords and recovery*: the three
  secrets and their jobs, what is needed where, what to do when you forget each
  one, and what someone else can and cannot do.
- **Forgot the PIN?** - on the Settings card and on the PIN prompt - gives the
  exact recovery command for Windows and for macOS/Linux, and what happens to
  stored passwords.
- The access point card advises on the password it has: recommends choosing your
  own while it is the board's generated one, warns while it is `hid12345`.
- The space between a shortcut group's heading and its tiles.
- A PIN refused because this device was locked out is no longer kept. It was
  re-sent on every reconnect, so a wrong one silently spent another attempt the
  moment the lock ran out, and the next lock was longer. Found by the live test
  below on a real board.
- **Turn off** and **Forgot the PIN?** are hidden while there is no access PIN to
  turn off or forget.
- **Who is connected.** Tap the dashboard count in the header to see every open
  dashboard: its address, whether it came over the board's WiFi or the home
  network, whether it is signed in, and which one is this device. Settings lists
  them too - before, it only listed devices on the board's own WiFi, so a tab left
  open on a laptop over the home network counted but appeared nowhere. The
  `clients` reply gains `dashboards`.
- "This board made this password" is now a fact about the password in use, not a
  flag cleared by any change: change it and put it back, and the board says so
  again. Boards from earlier v3.10 builds keep what they had.

### Tests

- Every row of the ownership tables (SECURITY.md) is a dashboard scenario
  (`tests/ui/bdd/features/ownership.feature`), run against a board model that
  implements the new rules exactly.
- The recovery tool and `flash_esp.ps1 -Recover` run end to end against a
  simulated board on a real serial device: every recovery case, the typed ERASE,
  several boards, a non-HID-Fi device, older firmware (`tests/unit/test_recover.py`).
- On hardware (`tests/board/test_ownership_hw.py`): the password over USB and never
  over WiFi, the first PIN needing it over the home network, the per-device
  lockout, and unsigned connections being closed - each using only a temporary
  PIN that is removed again.
- The whole ownership story on a real board, in Chromium, through the dashboard
  the board serves (`tests/board/test_ownership_live.py`): the first PIN with no,
  a wrong and the right WiFi password; a reload and sign-in; a vault PIN and a
  stored secret; a PIN change keeping it; this device locked out while the USB
  cable is not; recovery over USB with and without a vault PIN; the WiFi password
  changed over USB and from the page, each becoming the new proof. It runs only on
  a board with no PIN and an empty vault, and puts everything back.
- `tests/test_vault.py` stored a PIN-kind entry with letters in it, which the
  board has refused since v3.8. The suite refuses to run on a board with a PIN, so
  it had not run since; the live story above left a PIN-free board and it failed
  at once. Its marker is now digits.
- `test_sim_contract.py` models a board that is on a home network: `wifi_status`
  then carries the link details (`sta_rssi`, `sta_gateway` ...).

---

## v3.9 - 2026-10-17

Emoji and accents reach the computer, and phone text stops arriving garbled.

**New: emoji, accents and real curly quotes**, on a Mac and on Linux. A USB
keyboard sends key positions, never characters, so the board types each one
through the host's own Unicode entry: on macOS, Option plus the UTF-16 hex
with *Unicode Hex Input* selected (emoji go in as a surrogate pair under one
Option hold); on Linux, Ctrl+Shift+U, the hex, then Space. Both type junk on a
host that is not set up, so each is off until you turn it on in
Settings → Emoji and symbols (`set_unicode`), and it follows the detected OS.
Not sure whether a computer is set up? **Test** types `é😀` through that OS's
entry (`unicode_test`) without changing anything, and asks what you saw — the
board cannot read the screen, so only your yes turns it on.
A manual OS pin is saved on the board and travels with it, so detection
overrides it here: the macOS sequence (Option is Alt on a PC, where Alt+F opens
menus) is never sent to a computer enumeration identified as Windows, and the
Linux one never to a detected Mac. The Test button refuses in the same cases.
Windows has no built-in equivalent — its Alt+numpad hex is capped at 16 bits
and needs a registry change — so a PC keeps the fallback below.

**Fixed: text typed from a phone came out garbled.** Phone keyboards swap in
curly quotes and dashes on their own, and `typeString()` handed each UTF-8 byte
straight to `Keyboard.press()` — which reads any byte at or above `0x88` as a
raw HID keycode. So `’` (`E2 80 99`) typed Keypad-2, tapped Ctrl, then typed
`n`: *what’s* arrived as *what2ns*. Worse, `°` (`C2 B0`) pressed F1 and then
**Enter**.

Text is now decoded as UTF-8 before anything is pressed. Smart quotes, dashes,
the ellipsis and non-breaking spaces fold back to the ASCII the user meant;
anything a US layout cannot produce is skipped, never typed as a stray keycode.
This is also the fallback whenever Unicode entry is off.
The `type` reply carries `skipped`, and the dashboard says when it is non-zero.
This covers every path that types text — the text box, macros, PC unlock and
the vault — because they all go through `typeString()`.

**New: one command for every test.** `python tests/run.py` runs every tier the
machine can - static, unit, ui, build, hostsim with no board; `--board`, `--e2e`,
`--perf` for the hardware - and prints a pass/fail/skip table with the reason for
every skip. The firmware's typing code is now compiled and checked on the host
against the installed ESP32 core, plain typing and every dashboard control are
pinned against golden references verified identical to v3.8, Linux gets an
evdev suite that grabs the board's devices (every key, media, sleep/power,
pointer to the pixel) and a CI replay through Xvfb + IBus + GTK, and CI runs the
no-board tiers on every push plus a weekly build against the latest core and
libraries. Dependencies are pinned in `tests/deps.json`. See docs/TESTING.md.

**New: the dashboard, scenario by scenario, and on every screen.** About 280 BDD
scenarios drive every tab in Chromium with real touch input against a model of
the board that is itself checked against the firmware. A coverage contract fails
the run if any control goes untouched or any dashboard code goes unrun (151/151
controls, 95.6% of the script today). A responsive matrix read from the CSS draws
every state on 500+ screens - folded phones to 8K, upright and turned, browser
toolbars showing and hidden, notches included - and measures layout speed on a
slowed phone, a tablet, 1080p and 4K. Every firmware command must be documented,
modelled and tested, every sheet and overlay measured, and a Claude Code hook and
optional git hooks run the checks before work is handed back.

**Flashing with several boards plugged in.** Both flashers now recognise a HID-Fi
board by its USB identity (the CH343 on its COM connector) instead of by port
name: an Arduino or another ESP32 kit on the same machine is listed and left
alone, two or more HID-Fi boards are never guessed between (it asks, or stops
with exit 10), a board can be picked by its USB serial number (`-Serial` / `-s`,
stable across sockets and hubs) or all flashed with `-All` / `-a` (now on macOS
and Linux too, with a per-board summary), `-Which` / `-w` shows the decision
without opening a port, and a named port that is not a HID-Fi board is refused
unless forced. On macOS one board seen under two names is counted once - before,
that alone could stop the script with "several serial ports found".

**Fixed, found by those tests.** Each is now a scenario that keeps it fixed.

- **Security: the home-network lock now covers keys and the pointer.** With no
  access PIN and the board on a home network, the lock refused JSON commands
  but not the binary frames that carry keys, pointer, buttons and media - so any
  device on that network could still type on the PC. The board now notes, once
  per connection, whether a socket came over the home network, and drops its
  binary frames under the same rule. No cost on the hot path: one stored flag.
- **After a reconnect with a PIN set, the vault, saved PCs and macros came up
  empty** until Refresh. The board's connect greeting took a reply slot and every
  later reply went to the wrong callback. Fixed in the dashboard, so boards
  already flashed with v3.8 benefit too.
- An iPhone driving a Windows PC showed Mac shortcuts (Copy sent Cmd+C).
- The + for saved PCs opened "Edit saved PC" instead of a new one.
- Scanning over the home network without a PIN said "No networks found"; it
  now says a PIN is needed.
- Tapping a latched modifier released it but kept it lit.
- The sideways swipe between tabs never completed (the browser cancelled it).
- The knob editor's step label did not follow its slider.
- The serial banner said v3.8.
- **Layout: 736 of 737 problems the 500-screen matrix found.** The app switcher's
  buttons fell off short landscape screens; controls sat under the iPhone notch;
  Home and End hid under the deck's corner buttons; the help sheet ran off the top
  of 4K and ultrawide screens; Esc, F10-F12, Home and PgUp were cut to "F..." on
  foldables; Ctrl, Tab and ?123 lost a pixel or two on phones; "Performance" and
  "Windows" were cut on the smallest screens. Visible on purpose: a keyboard
  500-620px wide now uses the compact layout instead of a cramped ANSI one.
  Measured cost of every fix together: about 3% more work at page load on a
  slowed phone (~0.4 ms on the device); everything else unchanged.

Fixed in the old suites: `test_mouse_led.py --skip-clicks` still pressed and
released the left button (a click) and left the pointer ~870 px from where it
started; `test_v34_features.py` expected `v3.8` by name; `test_inventory.py` only
ran on `COM13`.

> Verified on hardware against macOS (this board, Unicode Hex Input) and
> Windows 10 or 11 (Chrome reported NT 10.0): plain typing, shortcuts, held keys, macros and 50+
> scripts and emoji sets, including the pinned-to-macOS-on-a-PC case, which sends
> no Alt. Linux was verified in a Debian 12 VM: the firmware's exact HID reports,
> translated with the kernel's own `hid_keyboard[]` table, through Xorg, IBus
> 1.5.27 and GTK, and through GTK without IBus - exact in every script, with
> Caps Lock on and off. Not tested: Qt/KDE apps and terminals without IBus, where
> Ctrl+Shift+U is not an input method and the hex would be typed as text.
> Off-board, every Unicode scalar value round-trips through a model of each host,
> plain ASCII is byte-identical to v3.8 including timing, and 16 planted bugs
> are all caught.

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

- **The dashboard showed an empty Keys tab while the board held the entries.**
  Replies come back over the socket in the order the commands were sent, and the
  page matched them to callbacks by position — but a command sent without a
  callback pushed no slot while still being answered, and `auth_required`
  returned without consuming one. On every connect the page asks for the config,
  macros, PCs and vault *before* the PIN has been accepted, so four refusals
  arrived and stranded four callbacks. From then on every reply was delivered to
  the previous command's handler for the life of the socket: the vault's answer
  landed in another card's callback, the list rendered empty, saving appeared to
  do nothing, and a vault PIN that had been set still offered to add one. A hard
  refresh never helped because the sequence is identical on every load.
  Now every command takes exactly one slot and every reply consumes exactly one,
  refusals included; pushed events consume none, and the queue is cleared when
  the socket closes. `tests/test_dashboard_live.py` drives the real dashboard
  against a fake board that speaks WebSocket and demands a PIN — the transport
  an HTTP-only stub could never exercise, which is how this got through.
- **A reconnect could leave a second socket running.** `connect()` assigned the
  new socket to the shared `ws` immediately and its handlers read that variable
  rather than their own socket, so a reconnect that overlapped a socket still
  closing left the older one's callbacks writing over the newer one's state, and
  nothing stopped a second `connect()` starting while one was already opening.
  Each socket's handlers are now bound to the socket that owns them and ignore
  everything once they are no longer the live one, and `connect()` returns early
  if a socket is already connecting or open. This was found while chasing a
  cursor that got laggy in a tab left open for hours; **it is not proven to be
  that cause** — five simulated reboots never reproduced the accumulation — but
  the binding was wrong regardless.
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

- **The topbar says how many dashboards are connected**, next to the latency
  pill. Because the board is an open access point within radio range, "am I the
  only one driving this?" was a question the dashboard could answer and did not.
  One connection is stated plainly; more than one turns the pill amber and the
  tooltip breaks it down into sockets and WiFi clients. It is the same pill as
  the latency readout — same radius, same surface, same tabular figures — and on
  a 320px screen it hides itself unless there is more than one, where the page
  title needs the room more than a count of one does.
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
