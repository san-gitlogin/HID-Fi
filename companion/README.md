# HID-Fi Companion

A small tray / menu-bar app for the computer HID-Fi is plugged into. It shows
that computer's screen on the HID-Fi dashboard, live, so you can see what your
mouse and keyboard are doing.

- The picture goes **straight from the computer to your phone**, encrypted
  (WebRTC). The board only pairs the two and hands out one-use tickets; its radio
  keeps carrying just the mouse and keyboard.
- **Nothing runs while nobody watches.** No capture and no encoding until a
  dashboard opens the Screen tab; the app idles at 0% CPU.
- **It only shows the computer the board is plugged into.** It finds the board on
  this computer's USB, and stops at once if the board is unplugged.

Status, decisions and the work log: [REQUIREMENTS.md](REQUIREMENTS.md),
[DESIGN.md](DESIGN.md), [PROGRESS.md](PROGRESS.md).


**What it does:** shows the computer's screen live on the dashboard's Screen tab
- each monitor, up to six viewers, the all-monitors wall (`#wall`), a floating
window, watch-only screens (`#watch`). Install and use: [docs/COMPANION.md](../docs/COMPANION.md).

## Using it

1. Set an access PIN on the board (dashboard, Settings).
2. Start HID-Fi Companion on the computer. Its icon appears in the menu bar /
   system tray and says what it is doing.
3. On the dashboard, open **Screen**, tap **Pair**, and type the six-digit code
   the computer shows (in the menu bar on a Mac, in the tray menu elsewhere).
4. The picture appears. Pinch or use the buttons to zoom; the float button keeps
   it on top while you use the other tabs.

macOS asks once for **Screen Recording** permission; the tray offers a button
that opens the right page in System Settings.

## Building

Rust 1.89 or newer. No other toolchain: macOS needs only the Command Line Tools.

```bash
cd companion
cargo build --release            # target/release/hidfi-companion
cargo test                       # unit + model integration tests (needs python3)
cargo clippy --all-targets -- -D warnings
```

`HIDFI_HOME=<dir>` keeps its files elsewhere and `HIDFI_NO_AUTOSTART=1` stops it
adding a login item - for development and tests.

## Tests beyond `cargo test`

| Command | What it proves |
|---|---|
| `vectors/check.py` | the pairing vectors, recomputed with Python `cryptography` |
| `vectors/check_board.sh` | the board's own crypto (`hid_fi/companion_crypto.h`, the mbedTLS the pinned core ships) gives the same bytes |
| `tests/e2e_view.py` | hardware H.264 → WebRTC → real Chrome; refused tickets and certificates; CPU |
| `tests/overload.py` | everything at once: six viewers on two monitors, a link swinging 0.4-12 Mbit/s, a stalling device, a viewer rejoining every 5 s, a link that drops to 1.5 Mbit/s, three viewers on jittery WiFi (`WIFI=mild`: measured on a real home network; default: a crowded channel), busy or light content (`CONTENT=bar`); `ONLY=p1|drop|wifi` runs one phase |
| `tests/system.py` | the real service + board model + Chrome: pair, live, unplug, replug |
| `tests/open_dashboard.py` | the menu's Open the dashboard page in Chrome: goes to the board, prefers its own name, explains (and opens the setting) when the browser is kept off the network |
| `tests/boards.py` | two board models + the real service + Chrome: a board paired with another (now off) computer offered here, both pair and watch, unplug one (its viewers end, the other plays), replug (no new pairing), one removes this computer (only it re-pairs) |
| `tests/dashboard_live.py` | the real dashboard Screen tab with live H.264, zoom, float, unplug |
| `tests/ui/companion` (repo root, `python tests/run.py --tier ui`) | the window page in Chromium and WebKit: every state the app's model produces (`tests/golden/companion_window_states.tsv`, regenerate with `cargo run --example window_states`), at every width, every button, selection, keyboard, filter, timing; its own coverage contract (docs/COMPANION_WINDOW.md) |
| `tests/window_real.py` | the window in this OS's own web view (WKWebView / WebView2): every state at two sizes, clicks, copy, fifty boards against the clock |
| `tests/window_life.py` | the window's own life on this OS (macOS, Windows), through the app's code: never opened, minimized, hidden, behind another window, the app hidden (macOS), closed, minimized twice - each time Open brings it to the front |
| `tests/permissions_mac.py` | macOS: the real app under a bundle id of its own - a first start with Screen Recording not working resets its own entries once (Screen Recording, System Audio, Microphone) through the real tccutil, a restart resets nothing, a new signature resets once more; never the installed app's |
| `tests/opened_again.py` | the real app (test build, no USB board, its own folder): started again it shows the running one's window; macOS: opened again from Finder (a reopen event) does the same with no second copy; then the normal build is put back in `target/release` and checked for the TEST key |
| `tests/linux/window_browser.py` | Linux: the real app serves the window to a browser; it draws, the state long-polls, a click arrives, another site's POST is refused |
| `tests/self_update_mac.py` | the Companion updating itself on a Mac: finds the release, downloads and checks it, installs in place, the new one takes over, the old is removed, the app's folder untouched |
| `tests/board_pages.py` | the REAL board: 8 dashboard pages at once, the 9th told the board is full (`HIDFI_PIN`) |
| `tests/linux/run.sh` | Linux end to end in Docker: build, tests, the app without a tray, real X11 capture + OpenH264 + WebRTC on two virtual monitors, the window in a browser, a second start opening the running app's window |
| `tests/multi_monitor_mac.py` | REAL monitors on a one-screen Mac: `tools/vdisplay` makes 3 virtual displays; the real app, board and Chrome show each at its shape, two at once, and an unplugged one falls back (`HIDFI_PIN`) |
| `tests/hardware.py` | the REAL board and service: pair by code, live picture, four viewers, one PIN (`HIDFI_PIN`) |

The last four need Google Chrome and their binaries built first:
`cargo build --release -p hidfi-stream --example serve -p hidfi-companion --example system_app --example hw_app`.
`hardware.py` also needs the board on this computer's USB and the home WiFi; it
removes its test pairing when done.

## Layout

| Crate | What it holds |
|---|---|
| `hidfi-core` | pairing crypto, wire format, board selection - no OS code |
| `hidfi-agent` | finding the board (USB + network), pairing and heartbeat session |
| `hidfi-stream` | capture (ScreenCaptureKit), H.264 (VideoToolbox), WebRTC (str0m), the viewer server |
| `hidfi-companion` | the tray app: service, storage, login item, tray menu |
| `web/viewer.js` | the browser half; the dashboard carries an identical copy (a static test checks) |

## Not yet

Windows and Linux capture and encoding, installers and signing, the lock screen
and login window (need a system service). See REQUIREMENTS.md for the full list.
