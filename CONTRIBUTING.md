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

```powershell
python tests\test_dashboard_static.py  # no board needed - run this first
python tests\test_responsive.py       # no board needed - 68 screen sizes
python tests\test_v34_features.py      # 24 checks, reversible actions only
python tests\test_power_health.py      # power modes, heap reporting, input bounds
python tests\test_inventory.py         # MACs, stored settings, AP clients
python tests\test_wifi_join.py         # join state machine, proves loop() never blocks
python tests\test_memory_soak.py       # 2000 commands, checks for a leak
python tests\test_lock_state.py        # lock-state machine
python tests\test_mouse_led.py --skip-clicks
```

`test_dashboard_static.py` needs no hardware. It extracts the dashboard from the
PROGMEM string, runs `node --check` over the script, and verifies that every
`$('id')` and every icon reference actually resolves. A JavaScript error in that
string compiles perfectly and serves a blank white page, so run it before you
flash.

`test_responsive.py` needs no hardware either, but it does need Playwright
(`python -m playwright install chromium`). It drives a real browser over 68
viewports from a 240px feature phone to 8K, checking every tab for content wider
than the screen, key labels clipped inside their own button, and the trackpad card
changing size when it flips to the keyboard. Run it after any CSS change - none of
those failures are visible on the one screen you happen to be developing on.

`test_v34_features.py` is the one to run after any firmware change. It is safe on
a live desktop — it only sends reversible things and restores what it touched.

`test_power_health.py` reads the current power mode, exercises all three, and puts
the original back. `test_memory_soak.py` takes about a minute and is the only way
to tell a leak from normal churn.

`test_wifi_join.py` drives the join state machine using an SSID that does not
exist, so it needs no credentials. It refuses to run if the board already has
saved credentials, and clears what it sets. The part that matters is that it
pings continuously *during* the join and compares latency against the idle
baseline &mdash; that is the regression test for the blocking-join bug, and it is
the only test that would catch it coming back.

**What none of these cover:** anything that needs a client on the board's own
access point. A successful join, the `lan_locked` exposure gate, `/api/scan`, the
PIN lockout actually engaging, and the dashboard itself are all still manual.

**`test_mouse_led.py` without `--skip-clicks` sends real mouse clicks** into
whatever is focused. Do not run it on a desktop you care about.

### Checking dashboard changes without flashing

The whole dashboard is one PROGMEM string in `usb_hid_unlock/web_ui.h`. You can
pull it out and open it in a browser, which is far faster than reflashing to find
a typo:

```python
import pathlib, re
html = pathlib.Path("usb_hid_unlock/web_ui.h").read_text(encoding="utf-8")
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
- Say which board you tested on, and paste the output of
  `tests\test_v34_features.py`.
- If you changed the dashboard, a screenshot at phone width (360–390px) helps a
  lot. Most of this UI is used one-handed on a phone.
- If you changed anything about timing, feel, or latency, say what you measured.

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
