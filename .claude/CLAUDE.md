# CLAUDE.md

The full brief is **[`../AGENTS.md`](../AGENTS.md)**. Read it before changing
anything — most of it exists because something went wrong once.

## Project

ESP32-S3 that plugs into a PC as a **USB HID keyboard, mouse, media remote and
gamepad**, serving a phone dashboard from its own WiFi access point. The host
needs no drivers and no software, which is why it works on a lock screen and in a
BIOS.

## Key files

- `hid_fi/hid_fi.ino` — all firmware (~3,600 lines)
- `hid_fi/web_ui.h` — the whole dashboard as one PROGMEM raw string
  (~3,900 lines): HTML, CSS, JS and an SVG icon sprite
- `flash_esp.ps1` / `flash_esp.sh` — build, flash, verify
- `companion/` — HID-Fi Companion, the Rust app that shows the computer's screen
  on the Screen tab; rules in AGENTS.md "Screen sharing"
- `hid_fi/build_opt.h` — compiler flags for every file, libraries included (the
  WebSockets page limit); a change rebuilds from clean
- `tests/run.py` — **the** test command; tiers, safety rules and golden files are in
  `docs/TESTING.md`

## Commands

```bash
./flash_esp.sh -c                     # build and flash (Windows: .\flash_esp.ps1 -Compile)
python tests/run.py                   # no board needed - run before saying "done"
python tests/run.py --all             # with a board on COM + USB - ASK the user first
python tests/run.py doctor            # installed vs pinned versions
python tests/run.py --tier ui         # the dashboard: ~280 scenarios + the coverage contract
tests/setup_linux.sh                  # Linux, WSL or a sandbox: everything the suite needs (then python3 tests/run.py)
tests/docker/run.sh                   # macOS / any Docker: the Linux-only hostsim tier in Ubuntu 24.04
tests/docker/vm.sh up                 # macOS, no Docker: the self-contained VM (HIDFI_VM_HOME) - ask before setting it up
```

Setting up from nothing, and where the Linux VM may store things: AGENTS.md,
"Setting up and running tests, as an agent".

## Serial access will reset the board — and can brick-look it

DTR and RTS drive GPIO0 and EN, so `open()` reboots the ESP32. Always set
`ser.dtr = False` and `ser.rts = False` **before** `ser.open()`, as every file in
`tests/` does. Repeated rapid opens can latch the **ROM bootloader**: the HID
interfaces disappear and `USB JTAG/serial debug unit` shows up instead, which
looks exactly like the board looping. Recover with
`esptool --port COMx --after hard_reset --no-stub flash_id` or a replug, and
diagnose from the USB device list rather than from serial noise you are causing.

## Non-negotiables

1. **Read before editing.** Both main files are long and structured. Grep, read
   the surrounding code, then change it. Never infer behaviour from a name.
2. **Never edit a sketch file mid-build.** `arduino-cli` emits misleading errors.
3. **Never flash `merged.bin` at `0x0`.** It erases NVS and every saved setting.
   The script writes four partitions on purpose.
4. **Validate the dashboard before flashing.** Extract the PROGMEM string, run the
   script through `node --check`, confirm tag balance and icon references. A JS
   syntax error means a blank white page diagnosable only by reflashing.
5. **No emoji.** SVG sprite symbols only, 24×24, stroke 1.75. Judge new icons at
   16–24px, not at design size.
6. **The keyboard is tiered**, not one layout stretched: compact (<620px) →
   ANSI (620px) → TKL nav cluster (1010px) → numpad (1300px, 104 keys). **Every
   tier must expose every key the tier below it reaches** — never hide a row that
   is the only way to reach its keys, and never let a wider screen offer fewer.
   Every block carries the same row count or the blocks stop lining up. ANSI is a
   60 column grid (1u = 4
   columns, every row sums to 60). The static test checks the geometry *and* that
   every key name resolves in `mapKeyName()`.
7. **Hot paths stay clean** — binary WebSocket frames, no JSON, no logging, no
   awaiting replies, and no `delay()` anywhere a pointer report can reach.
8. **Anything held must be releasable.** A stuck Alt or mouse button makes the
   user's PC unusable.
9. **`mapKeyName()` is the allowlist.** Combos missing from it fail silently.
10. **Host OS detection stays passive.** Never reintroduce a probe that types
    something, and do not use the `0xEE` MS OS descriptor — Windows caches that
    answer and stops asking. `SET_IDLE` does *not* tell a Mac from a PC; that was
    assumed in v3.7 and disproved on a real Mac. The discriminator is the host's
    string-descriptor reads — macOS re-reads the same index, Windows and Linux do
    not — which is why the firmware defines `tud_descriptor_string_cb()` itself.
11. **Never expose a saved PC password.** `pc_list` returns names and OS only.
    The vault's `vault_get` and `vault_type` are the only ways a secret leaves
    the board, both PIN gated on every transport including serial, with no
    session. Saving and deleting are not gated — they disclose nothing. A blind
    access-PIN reset erases the vault, and so does resetting a forgotten vault
    PIN. `macro_get` is ungated over serial, so a secret never goes in a macro.
12. **The dashboard never relies on the Refresh button.** Every board write
    calls `syncAll()`; `applyStatus()` rebuilds what the access PIN affects.
13. **Restore hardware state.** If a probe sets a PIN or saves a profile, undo it
    and verify the undo. Delete throwaway scripts when done.
14. **No byte >= 0x80 reaches `Keyboard.press()`.** The core reads those as raw
    HID usages - that is how a phone's curly quote typed "2n" and a degree sign
    pressed Enter. Text goes through `typeString()`; see AGENTS.md "Text typing".
15. **Tests that touch the user's computer need their OK.** `--board` moves the
    mouse and changes volume (net zero); `--e2e` types into a page they must
    start. Read a script before running it on someone's desktop. Report skips as
    skips, and never edit `tests/golden/` to make a failure pass.
16. **New behaviour ships with its scenario.** A command is documented, modelled
    in `tests/support/board_sim.py` and sent by a test; a dashboard control has a
    scenario in `tests/ui/bdd/features/`. The coverage contract and the Stop hook
    (`.claude/settings.json`) fail the turn otherwise - follow
    `.claude/skills/hidfi-feature`. A bug found and not fixed is tagged
    `@known_issue`, never deleted.
17. **Ownership rules are fixed.** The board's WiFi password never travels over
    WiFi (`ap_password` is USB only). The first access PIN over WiFi needs that
    password. Recovery of a forgotten PIN or WiFi password is USB only - never add
    a WiFi or button path. Lockout is per device. Read "Ownership, PINs and
    recovery" in AGENTS.md before touching auth, PINs, the vault or the access
    point.
18. **Flashing for a person: show the WiFi password once, then forget it.** After
    a flash or a USB recovery, tell the person the board's WiFi name and password
    (the flasher's last "Dashboard: join WiFi ..." line), say it is a one-time
    display to write down now, and that `./flash_esp.sh --recover` shows it again.
    Never save it to memory, notes, files, commits or artifacts, and never repeat
    it later. Never suggest a shared weak password like `hid12345`. This is the only
    secret an agent may display - never a PIN or a vault secret. AGENTS.md has the
    full rule.

## Expected working style

Verify rather than assume — read the ESP32 core source or the vendor guide when
behaviour is surprising. Several conclusions here came from discovering the core
never calls a function it should. State plainly what could not be tested and why,
rather than implying it was.
