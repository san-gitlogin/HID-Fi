# Recovery mode: recovery over the one USB port

Status: **built** (firmware, tools, Companion, dashboard). Every claim marked ▢ is
unproven until the test named next to it passes on a real board; ✔ marks what is
proven, with where; ◐ is proven in part, and says which part is not. This file is
the spec the code and the tests are written from.

## Why

Recovery of a forgotten WiFi password or access PIN is USB only (AGENTS.md,
"Ownership, PINs and recovery"). Today it runs over UART0, which the dev kit's
CH343 carries to its COM port. The HID-Fi Dongle (Rev A, one USB-C plug) has no
CH343: its native USB runs HID only, so a dongle owner who forgets the WiFi
password or the access PIN has no way back. Recovery mode gives the native USB
port a serial link only after a deliberate press of BOOT, so holding the board
stays the proof of ownership.

## The rule, stated so nothing weakens

- Recovery is USB only. Still. A press of BOOT **opens a USB recovery session**;
  it never changes, resets, reveals or erases anything by itself. Every change
  still needs a computer, the tool, and (for a vault erase) a typed ERASE.
- Nothing over WiFi changes. `ap_password`, `net_fault`, `ota_source` and every
  other USB-only command stay refused over WiFi with `usb_only`.
- Normal boots never expose a serial link on the native port, so no program on
  the computer the board is plugged into can read the WiFi password unless
  someone pressed BOOT on that board in the last few minutes.
- Agreed by the owner, 2026-11-04 ("my rule says USB only right, adding boot
  button press to it wont cause any problem").

## Behaviour

| # | Step | Detail | Proof |
|---|---|---|---|
| B1 | Press | BOOT held while the firmware runs. At 2 s the existing access-point restore happens, unchanged. Held on to **8 s**, the light turns the recovery colour. | ▢ H2, H6 |
| B2 | Release | On release after 8 s the board stores a one-shot flag in RTC memory (`RTC_NOINIT`) and restarts. A release before 8 s does what it does today. | ▢ H2 |
| B3 | Recovery boot | `setup()` reads and **clears** the flag first, then creates a CDC interface before `USB.begin()` (the core only accepts interfaces before USB starts: `tinyusb_enable_interface2`). HID, WiFi and the dashboard run as usual. | ▢ H3 |
| B4 | Identity | Same VID:PID (303a:1001), same serial number (the MAC). Told apart from the ROM loader (product "USB JTAG/serial debug unit") by the product string and, always, by a handshake: `status` answers `"recovery": true`. Tools never guess from a port name. | ▢ H3, U1 |
| B5 | Session | Lines on the recovery CDC run as `SRC_SERIAL`, exactly as the COM cable does. Nothing is printed there unasked: no boot banner, no log, so the WiFi password is never pushed out (today's boot banner prints it on UART0). | ▢ H3, S2 |
| B6 | Window | The window closes **5 minutes** after the last command, or on `recovery_exit`; the board then restarts into a normal boot with no CDC. Unplugging also ends it: RTC memory does not survive power loss. | ▢ H4, H5 |
| B7 | Normal boots | Byte-identical USB descriptors to today's firmware, so host OS detection, BIOS keyboards and lock screens see no change. | ▢ H1 |
| B8 | Detection | A recovery boot's enumeration is not used to learn or store the host OS. | ▢ H3, S3 |
| B9 | Both boards | Built into every HID-Fi firmware: the dev kit gains it too (COM keeps working). | ▢ H3 |

Firmware broken (does not boot): the ROM loader is the way in (hold BOOT while
plugging in) to reflash; the settings survive, then recovery mode as above.
✔ proven on DA1A2C 2026-11-04 (read only): ROM USB-Serial-JTAG appears on the
one port; esptool reads chip, flash and eFuses; `--after watchdog-reset` brings
the firmware back in 1 s, `--after hard-reset` does not (no RTS on that path).
✔ firmware-running cable update over native USB only: docs/OTA.md, 2026-10-28.

## Who uses it

- `tools/recover.py` (and `./flash_esp.sh --recover`, `.\flash_esp.ps1 -Recover`):
  finds a recovery-mode board by USB identity + handshake; with no COM cable and
  no recovery board, it tells the person exactly what to press.
- `tools/flash_ports.py`: a new kind, `hidfi-recovery`, never flashed by it.
- HID-Fi Companion: shows "Recover this board" when a board on this computer is
  in recovery mode; same actions and the same ERASE confirmation as the tool;
  shows the WiFi password once and never stores it.
- Dashboard and docs: "the COM cable" becomes "the USB cable" with the press, where
  a board has no COM port.

## Tests (each a gate; none skipped silently)

Software, no board (`python tests/run.py`):
- U1 flash_ports: recovery CDC vs ROM loader vs normal board vs CH343 vs other
  ESP32s, one or several, named or not; never flashes a recovery port.
- U2 recover.py over a fake recovery link: show, set, reset, ERASE yes/no,
  refusal, timeout, window closed mid-way, two boards, no board (what to press).
- S1 board_sim models `recovery` in `status`, `recovery_exit`, the window and its
  expiry; test_sim_contract checks the model against a board.
- S2 static: nothing writes to the recovery CDC except command replies.
- S3 static: the CDC is created only under the one-shot flag, before `USB.begin()`.
- C1 Companion (Rust): detection, handshake, the flows, refusal paths, no secret
  in logs or the store.
- Dashboard BDD: every changed text and any new control has a scenario.

Hardware (board tier, test board 09F7C8 on COM + USB, never DA1A2C;
`tests/board/test_recovery_mode_hw.py` unless named):
- H0 ✔ the COM port's DTR/RTS hold GPIO0 low without a reset (`status` reads
  `boot_button: pressed`), so BOOT presses can be automated.
- H1 ✔ normal boot: USB descriptors byte-identical to v4.2.2's (macOS IORegistry).
- H2 ✔ hold 8.6 s and release: `ready` while held, restart, recovery boot. Holds of
  2.6 s and 5 s open nothing.
- H3 ◐ recovery boot: the link has the MAC as its serial, answers the handshake,
  `./flash_esp.sh --recover` shows the password over it (0.6 s), the HID report
  descriptor is unchanged, and WiFi, HID and a pointer move over the link hold on
  every cycle of H9. Not done on hardware: set / reset over the link (proven against
  the board model, `tests/unit/test_recover.py`).
- H4 ✔ the window closes after 300 s idle (measured 300.3 s), on `recovery_exit`,
  and on a reset; each time the link is gone and the next boot is normal.
- H5 ◐ an EN reset in recovery boots normally. A real unplug was not done (no
  hands here); the flag is read once and cleared first thing at every boot, so it
  cannot survive either. After a power cut that RTC memory is undefined: a chance
  match is 1 in 2^32, and would open only the 5-minute link, which changes nothing.
- H6 ◐ the tap and the 2 s access-point hold are unchanged in the code (the
  button change is pure insertion), and 2.6 s / 5 s holds open nothing. Not done on
  hardware: a tap types "HID OK" and Enter into whatever has focus, so it is not
  run unattended.
- H7 ◐ macOS ✔ (HID unchanged, the link, no prompt). Windows 11 ✔ (ARM64 VM, the
  board passed through from a Mac with QEMU as root, which takes every interface
  from macOS): in recovery Windows binds its own USB serial driver (COM3) beside five
  working HID devices, no prompt, none failed; `flash_esp.ps1 -Recover` under Windows
  PowerShell 5.1 finds the board, shows the password and closes or keeps it open
  (`companion/tests/windows/recovery_vm.ps1`). That run found a 5.1-only bug - one
  board in recovery read as none, because a single result there has no `.Count` -
  fixed, with a static rule for both PowerShell scripts. Linux ▢ with a real board:
  the Linux test VM (colima) cannot take a USB device.
- H8 ◐ the Companion's own code against the real link: macOS (USB listing 4.7 ms,
  handshake 279 ms) and Windows 11 (0.7 ms, 263 ms): show password - never in the
  view - and close (`examples/recovery_hw.rs`). The window's panel: every state in
  Chromium, WebKit, macOS WKWebView and Windows WebView2. Linux against a real link ▢.
- H9 soak: 50 press-to-recovery-to-exit cycles, no failure, timings recorded
  (`tests/board/recovery_soak.py`).

## Found while mapping (2026-11-04)

- ✔ H0 proven on 09F7C8 (v4.2.2): with the COM port open, DTR asserted and RTS
  not holds GPIO0 low with no reset; `status` read `boot_button: pressed` at 0.3,
  1.0 and 1.7 s, no extra boot, no `button_press` (a hold past 2 s never types).
  So every BOOT scenario runs unattended on the dev kit.
- ✔ H1 baseline recorded passively from the macOS IORegistry (no request reaches
  the board, so host-OS detection is undisturbed): `~/HID-Fi-work/evidence/
  baseline_usb_09F7C8_v4.2.2.json` - one HID interface (3/1/1, 2 endpoints), a
  202-byte report descriptor with this board's pointer and gamepad settings.
- The update self-test (`ota.h` `otaTick`) only needs HID up, but a restart while
  `ota.pending` rolls the update back: recovery refuses then, and while an update
  is being written, exactly as `download_mode` does. The light says "not now".
- The boot banner prints the WiFi password on UART0 (`hid_fi.ino` setup). The
  recovery CDC must never carry `COM_SERIAL` output: replies only.
- The Companion's flasher never takes 303a:1001 for a loader (`hidfi-flash/src/
  serial.rs`), so a recovery port cannot be flashed by mistake; a test keeps it so.
- `flash_ports.py` calls any 303a device `esp-native` and lets `-p` flash it:
  a recovery port must become its own kind, refused for flashing.
- The firmware cannot tell the dev kit from the dongle, so the dashboard and docs
  describe both ways in: the COM cable, or (no COM cable) the BOOT press.
- `tud_descriptor_string_cb` answers "TinyUSB Device" for interface strings; the
  recovery CDC's interface name must resolve there too.
- Precedents in this firmware: the pointer and gamepad interfaces are already
  chosen before `USB.begin()`; `rtcFaultMagic` is an `RTC_NOINIT` one-shot flag
  read and cleared at boot. Recovery mode reuses both patterns.

## Every piece it touches, and what each is checked against

| Piece | Change | Related things that must not move |
|---|---|---|
| `hid_fi.ino` BOOT poll | 8 s hold -> flag + restart | tap test, 2 s AP restore, 20 ms poll, loop timing |
| `hid_fi.ino` setup | CDC only under the flag | descriptors (H1), detection, HID ready time, OTA self-test |
| `hid_fi.ino` loop | read the CDC when it exists | hot paths (rule 7): one null check when off; `--perf` before/after |
| `processCommand` | `recovery_exit`, `status.recovery` | every `SRC_SERIAL` rule, `usb_only` over WiFi |
| LED | recovery colour, "not now" | existing blink/feedback, LED off at boot |
| `ota.h` | refuse while pending/busy | download_mode, rollback, self-test |
| `web_ui.h` | recovery wording, any status badge | coverage contract, 500+ screens, ownership.feature |
| `board_sim.py`, `test_sim_contract.py` | model it exactly | every dashboard scenario |
| `tools/recover.py`, `flash_ports.py`, `flash_esp.sh/.ps1` | find and use a recovery port | COM path unchanged; never flash a recovery port |
| Companion (`hidfi-flash`, `hidfi-agent`, `hidfi-companion`) | detect + recover UI | discovery by MAC, cable updates, pairing, screen sharing |
| SECURITY.md, README, AGENTS.md, CLAUDE.md, COMMANDS.md, FLASHING.md, COMPANION.md, CHANGELOG | the rule and the steps | ownership tables have a scenario per row |
