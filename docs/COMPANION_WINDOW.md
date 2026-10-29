# The Companion's window: a board per card

The plan and the progress log, like docs/OTA.md and docs/AUDIO.md.

Owner's brief (2026-10-29): the window lists the boards as rounded, bordered
cards in a grid instead of a column of buttons - organised and easy to read,
designed professionally, with online/offline and latency (ms) indicators, the
Companion's properties laid out neatly, more of them in an expandable view,
cards selectable one at a time or several at once. Padded buttons that scale
with the window; responsive and smooth; any number of boards; pairing and every
other flow in the cards; nothing in Rust slowed down. It ships as an update the
Companion already installed on the owner's Mac finds and installs. Tests as
strict as the firmware's, automated on macOS, Windows and Linux.

## What was there (v4.1)

`panel.rs`: one page in a web view (wry: WKWebView on macOS, WebView2 on
Windows; no window on Linux - the tray menu says everything). With two boards
it was one headline, steps whose text ran both boards together, and every
action as a full-width button suffixed "(HID-Fi 0A0BB8)". Clicks were sent as
the button's *position* (`act:2`) - a board appearing between drawing and
clicking would shift the list and the click would do something else, Forget
included.

## Verified at the source (2026-10-29)

| Question | Answer | Where |
|---|---|---|
| Latency without new firmware | `GET /api/ping` answers a fixed 30-byte JSON, nothing computed - in every release, 4.0.4 included. | `hid_fi.ino` routes |
| What the board already reports per heartbeat | Nothing timed; a beat every 20 s (challenge + beat). | `hidfi-agent/src/session.rs` |
| Who watches which board | The gate already knows: `Admit::Yes(tag)`, tag = the board's MAC; only a total was counted. | `hidfi-stream/src/server.rs` |
| Paired boards not plugged in | The store keeps each board's pairing key and last address. | `store.rs` `keys`, `remembered` |
| Engines the page runs in | macOS: WebKit (WKWebView). Windows: Chromium (WebView2). Linux: the person's browser. Tests run the page in Playwright's WebKit and Chromium, and once in the real app's engine. | wry |

## Design

### The cards

- One card per board: HID-Fi `XXXXXX` (what its WiFi and dashboard are called).
  A status dot and word: **Online**, **Live** (someone watching through it),
  **Connecting**, **Not paired**, **Needs you** (a code, a PIN, a failed
  pairing), **Offline** (plugged in but unreachable, or paired and not plugged
  into this computer).
- Latency chip: the median of the last five `/api/ping` round trips, in ms,
  with signal bars - under 50 ms good, under 150 ms fair, above poor; "-" when
  offline. Probed every 3 s only while the window is open; the tray costs
  nothing (A3).
- At a glance: address, firmware version, an update badge, viewers.
- The pairing code, large, inside the card that asks for it - two boards can
  show two codes.
- Buttons: the one next step first (filled), the rest outlined; padded, at
  least 36 px tall, wrapping inside the card at any width.
- **Details** expands in place: MAC, address, firmware and the newest release,
  update state, latency (median, last, spread), last answer, pairing, viewers,
  plugged into this computer or not - each value copyable.
- Order: needing the person first, then live, online, connecting, offline;
  stable within each.

### Selecting

- Click a card: it alone is selected. Cmd/Ctrl-click: add or remove. Shift-click:
  a range. A check box on each card does the same for touch and keyboard.
  Space toggles, arrows move, Enter expands, Escape clears.
- With anything selected a bar shows what the selection can do together - open
  the dashboards, update, pair again, forget - only what applies to every
  selected board; forgetting asks first, naming them.

### The computer

Above the grid: this computer's name, the Companion's version, the sound
switches, and one banner each for what concerns the computer, not a board -
Screen Recording, the network permission, a Companion update. Empty states: no
board on this computer (with the next step), looking.

### Many boards

The grid fills the width (cards at least 300 px), one column in a narrow
window. From four boards, a filter (All / Online / Offline / Needs you) and the
counts. Fifty cards render and update without a frame dropped (measured).

### Transport

The page receives one JSON (`window::model`) and patches the cards it has, by
MAC - an update never collapses an expanded card or drops a selection. A click
sends an action *id* (`open:DCB4D90A0BB8`, `forget:...`), never a position; the
app looks the id up in the current model and ignores one that no longer
applies. macOS and Windows: wry's IPC. Linux: the same page from the app's
loopback server (token in the path, Host checked, POST only for actions), opened
in the browser from the tray.

## Tests

- **Rust:** the window model for every state and mix of states, the action ids
  (a stale id does nothing), latency (median, spread, offline), per-board
  viewers, remembered boards.
- **Page, in Playwright (Chromium and WebKit):** every state from the app's own
  model; widths 380 / 600 / 880 / 1280 - nothing clipped, no sideways scroll,
  buttons inside their card and at least 36 px; 0, 1, 2, 5 and 50 boards;
  expand, select (click, Cmd/Ctrl, Shift, keyboard), the bulk bar, the confirm;
  state kept across updates; names with markup shown as text; reduced motion;
  render and update time. In the main suite's ui tier, with a coverage contract:
  every control on the page has a scenario.
- **The real engine:** the app opens its window with a test view and runs the
  page's own checks inside WKWebView (macOS) and WebView2 (Windows CI); Linux
  CI loads the page from the real app's loopback server.
- **The service:** the board model answering `/api/ping` slowly, not at all,
  then again: the card's latency and status follow.
- **The update:** the installed Companion finds the new release and installs it
  (a local signed test release in CI; the real one on the owner's Mac).

## Build order

1. [x] Data: latency probe, per-board viewers, remembered boards, `window::model`
       with action ids; unit tests.
2. [x] The page: cards, grid, details, selection, bulk bar, filter; Playwright
       tests in both engines; coverage contract.
3. [x] Wiring: wry IPC by id (macOS, Windows); the loopback page (Linux).
4. [x] Real-engine self-check; CI on three OSes (workflow steps added; runs on
       the next push).
5. [~] Release, and the installed Companion's update on the owner's Mac.

## Verified (2026-10-29)

- **The real window on this Mac** (WKWebView), with DA1A2C and 09F7C8: both
  cards online with their round trips; `--window-check`: all 17 states fit at
  920 and 380 px, no script error, the slowest draw 9 ms, fifty boards 6 ms to
  draw and 3 ms to update, a click selects, Details opens, copy and click reach
  the app (`companion/tests/window_real.py`, 14 checks).
- **The page in Chromium and WebKit:** 64 scenarios each (128 tests, 15 s in
  parallel), contract: 42 control kinds touched, 26 functions run. Mutation
  check: removing the `[hidden]` rule, skipping the Forget confirm, or
  rebuilding cards on update each fails a scenario. Found and fixed by them: the
  bulk bar ignored the update panel's and Details' buttons; WebKit does not
  focus a clicked button (the dialog lost its way back); buttons under 34 px at
  380 px; a confirm button with no name until opened.
- **Latency:** the board's round trip on the owner's network swings 6-103 ms (a
  board in WiFi modem sleep), the whole answer about 10 ms more - so the number
  shown is the connection's round trip, the answer kept for Details.
- **The Companion updating itself** (macOS, `companion/tests/self_update_mac.py`):
  an installed copy in a folder of its own finds a newer release, downloads and
  checks it, installs in place, the new one takes over in 8 s, the old one
  quits, what it replaced and the disk image are gone, the signature holds, the
  app's folder untouched. Found on the way: `open` starts an app without the
  caller's environment - a copy with its own folder would have come back on the
  owner's; the app's HIDFI_* settings now go with it (`open --env`).
- **Copy:** WebKit allows the page's copy only with focus and a person's
  gesture (failed 1 run in 2 under test); in the app's own window the app now
  copies (`pbcopy` / `clip`), proven to the clipboard (and the owner's
  clipboard put back); in a browser (Linux) the browser's own clipboard.
- **Linux:** the routes (token, Host, Origin, long poll) tested through the
  real server; the VM run builds and passes 98 of 99 Rust tests - the golden
  file is macOS-worded (now exact on macOS, same states elsewhere).

## Open: the macOS app's signature

The released app is signed ad hoc: macOS knows it only by its code hash, so
each new version is a stranger - Screen Recording and System Audio Recording
must be allowed again after every update, and a browser download is refused by
Gatekeeper. Two ways out, the owner's choice: an Apple Developer ID (paid;
also no Gatekeeper warning), or a HID-Fi signing certificate of the owner's own,
kept on this Mac like the release keys (permissions survive updates from the
first version signed with it; the warning stays).
