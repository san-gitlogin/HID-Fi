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
| `vectors/check_board.sh` | the board's own crypto (`hid_fi/companion_crypto.h`, mbedTLS 3.6.4) gives the same bytes |
| `tests/e2e_view.py` | hardware H.264 → WebRTC → real Chrome; refused tickets and certificates; CPU |
| `tests/system.py` | the real service + board model + Chrome: pair, live, unplug, replug |
| `tests/dashboard_live.py` | the real dashboard Screen tab with live H.264, zoom, float, unplug |

The last three need Google Chrome and build their binaries first:
`cargo build --release -p hidfi-stream --example serve -p hidfi-companion --example system_app`.

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
