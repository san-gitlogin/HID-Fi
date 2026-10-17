# Contributing

Thanks for looking. This is a small project and I am happy to take changes.

---

## Before you start

**This is an ESP32-S3 project and it has been tested on exactly one board, the
YD-ESP32-S3 N16R8.** Being a keyboard
and mouse needs the S3's native USB device peripheral, which the original ESP32,
C3, C6 and H2 do not have; the S2 has one but has never been tried here. Patches
that add another chip are welcome, but they need real hardware behind them — please
say what you actually ran it on.

Two hardware facts explain most of the odd-looking code. Neither is obvious, and
both have cost real debugging time:

**1. Only one mouse object may ever exist.**
`USBHIDMouseBase` registers its HID descriptor in its *constructor*, behind a
class-wide `static bool`. Only the first instance ever registers. Relative and
absolute mice therefore cannot coexist — the mouse is heap allocated at boot from
a saved flag, and switching modes reboots the board. Do not "simplify" this by
constructing both.

**2. The board cannot read the PC back.**
HID is one way. Anything the dashboard shows about host state — mute, volume
level, whether the PC is locked — is what we *believe* we set, not what is true.
This is why the volume knob is an endless encoder rather than a 0–100 dial: an
encoder only sends up and down ticks, so it cannot drift out of step with
reality. Please keep new features honest about this.

---

## Building

```powershell
.\flash_esp.ps1 -Compile          # build and flash the connected board
.\flash_esp.ps1                   # flash the last build, no rebuild
.\flash_esp.ps1 -Compile -Port COM7
```

You need [arduino-cli](https://arduino.github.io/arduino-cli/), the ESP32 core,
and two libraries. [docs/FLASHING.md](docs/FLASHING.md) has the exact commands.

A normal flash writes only the four firmware partitions and **leaves NVS alone**,
so your settings survive an upgrade. `-Erase` wipes everything, including saved
PC passwords.

---

## Testing

One command, from the repository root:

```bash
python tests/run.py install-hooks  # once: checks run themselves before a commit and a push
python tests/run.py            # no board needed - run this before every commit
python tests/run.py --all      # with a board on COM and USB - run this after any firmware change
```

It runs every test your machine can run, skips the rest with the reason, and
ends with a pass/fail/skip table per tier. **[docs/TESTING.md](docs/TESTING.md)**
is the full guide: what each tier needs and proves, testing on macOS, Windows and
Linux, the safety rules, golden files, performance budgets and upgrading a
dependency. The first time: `pip install -r tests/requirements.txt` and
`python -m playwright install chromium`.

What matters most when you change something:

- **Firmware typing or key handling** - the unit tier compiles the real typing
  code against the installed ESP32 core and compares plain-text typing, report by
  report and delay by delay, against `tests/golden/typing_ascii.json`. If you
  meant to change it, `--update-golden` and say why.
- **The dashboard** - the static tier catches the blank-white-page class of bug
  (a JS error inside the PROGMEM string compiles fine), and the ui tier checks
  67 screen sizes, that every control still sends what it sent before, and runs
  ~280 BDD scenarios (`tests/ui/bdd/features/`) against a model of the board.
  It ends with a coverage contract: a control no scenario touches, or code no
  scenario runs, fails the run. New UI means a new scenario - see
  `docs/TESTING.md#the-dashboard-scenario-by-scenario`.
- **Anything a computer receives** - only the e2e tier proves it. On Linux it is
  exact (`sudo -E python tests/run.py --e2e`, the board's devices are grabbed so
  nothing reaches your desktop); on macOS and Windows it types into a test page
  that you start with one click.

The original scripts in `tests/` still run on their own - `python
tests/test_v34_features.py` and so on - and the tiers run them for you.
`test_lock_state.py` is the exception: it really locks the computer, so it is
never run automatically. `test_mouse_led.py` without `--skip-clicks` sends real
clicks into whatever is focused; the board tier always passes `--skip-clicks`.

**Still manual on real hardware:** a successful WiFi join against a real router,
the `lan_locked` gate and `/api/scan` from a real LAN client, the PIN lockout on
the board itself. The dashboard side of all of these is covered against the
model. The WiFi performance tests run if you point them at
the board (`--board-url`).

### Checking dashboard changes without flashing

The whole dashboard is one PROGMEM string in `hid_fi/web_ui.h`. You can
pull it out and open it in a browser, which is far faster than reflashing to find
a typo:

```python
import pathlib, re
html = pathlib.Path("hid_fi/web_ui.h").read_text(encoding="utf-8")
start = html.index('R"rawliteral(') + len('R"rawliteral(')
page  = html[start:html.rindex(')rawliteral";')]
pathlib.Path("_preview.html").write_text(page, encoding="utf-8")
```

The WebSocket will not connect, but layout, icons and most logic will work. Run
the extracted `<script>` through `node --check` before you flash — a JavaScript
syntax error gives you a blank white dashboard that is only diagnosable by
reflashing.

---

## Code style

Match what is there. Specifically:

- **No emoji anywhere in the dashboard.** Every glyph is an inline SVG symbol from
  one sprite, 24×24, stroke 1.75, round caps. New icons join that sprite and
  follow those rules.
- **Comments say why, not what.** If the next line explains itself, do not narrate
  it. Comments earn their place by recording something the code cannot show —
  a platform quirk, a measured value, a constraint.
- **Nothing on an input hot path allocates, logs, or waits for a reply.** The
  pointer path is the reason this feels good to use.
- **Nothing in `loop()` may block.** This rule has been broken twice, both times
  by a WiFi call that looked harmless: `connectWifi()` waited up to 15 seconds and
  `WiFi.scanNetworks()` waited 2–4. `loop()` services the socket, the web server
  and the pointer stream, so blocking there freezes the dashboard and can drop the
  connection outright. If your change needs to wait, make it a state machine that
  reports progress through status fields.
- **Bound anything that arrives from outside** before you copy it. See
  `WS_TEXT_MAX` and `SERIAL_LINE_MAX`.
- Keep the firmware's one-JSON-object-per-command contract.

---

## Things that will break other people

- **Do not change the shape of the existing serial commands.** Other tools drive
  boards over serial. Adding commands is safe; renaming or altering the reply
  shape of `unlock`, `lock`, `status`, `ping` or `set_lock_state` is not.
- **Do not add a way to read a saved PC password back out.** See
  [SECURITY.md](SECURITY.md). `pc_list` returns names only, and `unlock` takes a
  slot number so the secret never leaves the board.
- **Route any new authenticated transport through `pinCheck()`**, so the lockout
  cannot be bypassed.
- **Do not weaken the exposure gate.** With the board on a home network and no PIN
  set, non-read-only commands from outside the access point subnet are refused.
  That gate is what makes joining a network defensible; if you add a command,
  think about whether it belongs on the allowed list, and default to no.

---

## Pull requests

- One logical change per PR.
- Say which board and which OS you tested on, and paste
  `tests/.reports/summary.md` from `python tests/run.py` (plus `--all` if you
  touched firmware and have a board).
- If you changed the dashboard, a screenshot at phone width (360–390px) helps a
  lot. Most of this UI is used one-handed on a phone.
- If you changed anything about timing, feel, or latency, run `--perf` and say
  what moved - `tests/.reports/perf.json` has the numbers.

I would rather have a small tested change than a large untested one.

---

## Good first issues

- **Keyboard layouts other than US.** The board types US-layout ASCII, so symbols
  and passwords come out wrong on other layouts. This is the most valuable open
  problem and affects real users.
- **More shortcuts**, particularly Linux desktops beyond GNOME. The table is at
  the top of the shortcuts section in `web_ui.h`. Every entry must be a combo the
  firmware can actually send — check `mapKeyName()` before adding one.
- **Other ESP32-S3 boards.** Everything here is verified on an N16R8 only.
  Report what worked and what needed changing.
- **Reducing the flash footprint.** The dashboard is around 145 KB of HTML.
