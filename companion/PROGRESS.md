# HID-Fi Companion: progress log

Newest first. Read REQUIREMENTS.md and DESIGN.md before continuing any work.

## Phases

| Phase | What | State |
|---|---|---|
| 0 | Requirements, design, tracking files | done 2026-10-21 |
| 1 | Rust toolchain; `core` crate: board discovery (mDNS + USB iSerial match), heartbeat, pairing crypto, with unit tests | wip: crypto, proto, matching done; OS USB listing, mDNS, HTTP client, heartbeat loop todo |
| 2 | Firmware: `/api/companion`, `companion_list/pair/unpair`, `screen_ticket`; NVS `compan`; board_sim + contract + docs + SECURITY rows | wip: written, compiles, model-tested; hardware not run |
| 3 | Capture + encode + WebRTC on macOS first (owner's machine), then Windows, Linux | todo |
| 4 | Dashboard Screen tab, split layout, floating window, zoom/pan, BDD + responsive | done in code + tests; not flashed |
| 5 | Tray + first-run window in HID-Fi style; installers; auto-start; update | todo |
| 6 | Resilience + soak + DoS tests; cross-OS / low-end CPU matrix | todo |

## Log

### 2026-10-21
- Studied AGENTS.md, ARCHITECTURE.md, SECURITY.md and the firmware's WS, mDNS,
  status and USB identity code.
- Findings that shaped the design: a resident WebSocket would break
  release-on-last-disconnect, radio sleep and AP auto-off (F2). The USB iSerial
  is the efuse MAC, which lets the app match the board on its own USB to the
  board on the network without a descriptor change (A7, F8). `ap_share` NAPT
  makes the phone-on-board-WiFi case possible (DESIGN topologies).
- Wrote REQUIREMENTS.md, DESIGN.md and this file. Nothing in firmware or the
  dashboard has been touched.

### 2026-10-21 (later)
- Rust 1.99.1 installed (internal disk, owner's OK). Workspace `companion/`,
  crate `hidfi-core`: `pairing` (X25519 + commit + SAS + HKDF + HMAC), `proto`
  (wire types, strict hex checks), `discovery` (USB iSerial ↔ status MAC match).
  27 tests, clippy -D warnings clean, rustfmt.
- `vectors/pairing_v1.json` (golden) + `vectors/check.py` (Python `cryptography`).
  The board's `hid_fi/companion_crypto.h` was built on this Mac against mbedTLS
  3.6.4 source (same as the ESP32 core) and matches every vector. Harness kept in
  the scratchpad only: needs to become a repeatable script (TODO).
- Firmware: `hid_fi/companion.h` (pairing state machine, `/api/companion`,
  `companion_list/pair/unpair`, `screen_ticket`), hooks in `hid_fi.ino` (include,
  `compLoad`, `compTick`, route, dispatch, set_auth wipe, status counts).
  Compiles: 47% flash, 21% RAM. NOT flashed.
- Model: `tests/support/companion_sim.py` (pure-Python X25519, RFC 7748 vector
  checked) wired into `board_sim.py`; `tests/unit/test_companion.py` 34 tests;
  contract lines in `tests/board/test_sim_contract.py`; COMMANDS.md and SECURITY.md.
- Found: HID-Fi enumerates as 303A:1001, the default for every generic S3 and the
  bootloader's JTAG port, so VID/PID can never identify a board. Only the MAC can.

### 2026-10-21 (agent crate)
- `crates/hidfi-agent`: `env` (Http/UsbList/Browse/Clock traits), `real` (ureq
  with whole-request timeout + 32 KB reply cap, nusb USB listing, mdns-sd browse
  with a bounded table), `locate` (candidates: remembered URL → `esp-hid-<mac6>.local`
  → mDNS; probe `/api/status`; MAC match), `session` (pairing + heartbeat state
  machine with backoff 1 s → 30 s).
- `companion/tests/model_board.py` serves the Python board model over real HTTP;
  `crates/hidfi-agent/tests/model.rs` drives the real agent against it: pair,
  online, ticket verifies with the app's key, 3 wrong codes, "Not now", expiry,
  unpair → re-pair, blind PIN reset → re-pair, PIN change keeps it, no PIN, board
  gone (backoff, key kept), old firmware (404) named. 9/9.
- Bugs found by those tests and fixed before they shipped: (1) polling `hello`
  after the reveal re-created the request, so "paired" was never seen; (2) after
  "Not now" the app re-asked at once. Now: `hello` once, then `state` only.
  (3) firmware without the endpoint showed a vague error; now "update the firmware".
- `python tests/run.py` after the firmware + model changes: PASSED in 630 s
  (static 24, unit 138, ui 467, build 4; skips: 312 Linux-only hostsim, 35 pwsh).
  Hardware tiers NOT run; firmware NOT flashed. Nothing committed yet.

## Next (in order)
1. Owner OK to flash; then `python tests/run.py --board` (includes the companion
   contract lines in test_sim_contract.py) and a real pairing over the LAN.
2. Phase 3 on macOS: ScreenCaptureKit capture, VideoToolbox H.264, str0m WebRTC,
   signaling server (tickets checked with `TicketNonces` + `pairing::ticket_ok`).
3. Phase 4 dashboard Screen tab.

### 2026-10-21 (owner away; working alone, not flashing)
- Owner reported: Settings "Connected clients" stuck on "Asking the board...".
  Root cause: `loadClients()`/`loadStorage()` ran once at page load over HTTP,
  before the PIN; the "PIN required" reply never reached their callbacks. Also
  stale ("No dashboards are open" from a dashboard). Fixed in web_ui.h: load on
  socket open, in `syncAll()`, and on opening Settings. Reproduced on the live
  v3.11 board (192.168.0.106) and verified the fix against it by serving the new
  page to Chrome with the socket going to the real board. Two scenarios added;
  CHANGELOG "Unreleased". Board NOT flashed.
- `crates/hidfi-stream`: `frame` (Frame, Encoder trait, Annex B splitter), `vt`
  (VideoToolbox H.264: real-time, constrained baseline, no reordering, low-latency
  rate control, forced keyframes; 3 tests on the M3 Pro), `pattern` (synthetic
  source), `pipeline` (capture+encode thread that exists only while watched;
  drops instead of queueing), `rtc` (str0m 0.24 ice-lite, pure-Rust DTLS, H.264
  baseline/pm1, TWCC bitrate → encoder), `server` (WS signaling: private peers
  only, 2 viewers, 6 conns, 5 s hello, 64 KB, 5 bad tickets → 10 min block, ticket
  one-use + tn liveness + HMAC). `companion/web/viewer.js` (browser half, checks
  the answer's fingerprint against the board's `fp`).
- `companion/tests/e2e_view.py`: Rust streamer + real Chrome: 1280x720 decoded,
  ~25 fps, first frame 1.7 s, used/forged ticket refused, wrong fingerprint
  refused, 0.0% CPU idle before and after. PASS.
- Screen Recording permission is NOT granted to this terminal (checked with
  CGPreflightScreenCaptureAccess), so real capture can only be verified by the owner.
- ScreenCaptureKit source (`sck.rs`) via objc2 bindings (the `screencapturekit`
  crate needs the macOS 26 SDK - rejected so the app builds with plain Command
  Line Tools). GPU-scaled to <=1920 long edge, newest-frame-only, Idle frames
  skipped. Compiles, clippy clean; NOT run (no Screen Recording permission here).
- Pipeline now rebuilds the encoder on any size change (A14), gives up cleanly
  after 50 consecutive failures; 6 behaviour tests (idle costs nothing, key on
  join, size change, still screen = 1 key / 2 s, slow viewer drops not queues,
  failing encoder ends session).
- e2e in real Chrome after refactor: 720p 28.6 fps, 1080p 28.4 fps, first frame
  ~450 ms; still screen ~0.4 fps (keyframes only). CPU of the streamer process at
  1080p30: ~15% of one core, of which ~9% is drawing the synthetic pattern =>
  ~6% for pipeline + hardware encode + WebRTC. Idle 0.0%.
- `crates/hidfi-companion` (lib + bin): `store` (per-user app dir, atomic writes,
  owner-only key/cert files, corrupt state recovered; 5 tests), `service` (one
  thread: USB check every 3 s, locate, session, server open only while the board
  is on THIS computer's USB, key persisted on change).
- SAFETY FIX found while writing: if the board is unplugged and moved to another
  PC, heartbeats over WiFi would still succeed and a viewer would watch this PC
  while typing into the other. Now: board gone from USB → session dropped, server
  closed (current viewers ended, new ones refused "closed").
- `companion/tests/system.py`: real service (fake USB, pattern) + board model on
  the LAN address + real Chrome with viewer.js: pair by code → online → owner-only
  key file → board-signed ticket → live 1280x720 → tray counts 1 viewer → unplug
  ends viewing in ~2 s → new viewer refused:closed → plug → streams again. PASS
  (after fixing an ordering bug: "open" was decided before re-finding the board).
- Tray app (`hidfi-companion` bin): tao + tray-icon, menu-bar only (no Dock icon),
  single instance (file lock), log file capped 1 MB, start at login (LaunchAgent /
  HKCU Run / XDG autostart; KeepAlive restarts after a crash, not after Quit),
  icons rendered from docs/assets/logo.svg (template on macOS; cyan elsewhere;
  status dot in a cleared corner, checked at 22 px). `menu::model` decides every
  word the tray shows: 10 tests across every board x session state.
- REAL HARDWARE (board plugged into this Mac, v3.11, not flashed): USB iSerial is
  `B81F3FDA1A2C` = status mac `B8:1F:3F:DA:1A:2C` (matching rule confirmed). The
  real app found it in ~2 s, recognised old firmware, and said "Update the board's
  firmware to use screen sharing". RSS 36 MB, CPU 0.0% idle.
- FOUND on real hardware: on macOS every lookup of `esp-hid-xxxx.local` takes 5.0 s
  (AAAA query unanswered by the ESP32 mDNS; `curl -4` is 2 ms). The app now finds
  the board by name once, then uses `wifi_ip` from its status; candidate order is
  addresses first, name last; HTTP timeout 7 s. (Same 5 s hits a dashboard opened
  by name on a Mac - firmware mDNS issue, reported to owner, not changed.)
- Dev overrides: HIDFI_HOME (app folder), HIDFI_NO_AUTOSTART (no login item).
- Dashboard Screen tab (web_ui.h): stage + moved #padwrap, Computers card (pair /
  code / not now / show / remove), floating window, zoom/pan, auto-retry with
  backoff that keeps the reason, stops when hidden / tab left / page hidden.
  New sprite icons (screen, float, dock, zoomin, zoomout, fit).
- Tests: tests/support/fake_pc.py (a second page streams a canvas over real
  WebRTC; signaling via route_web_socket; the board vouches for its real
  fingerprint), tests/ui/bdd/features/screen.feature (23 scenarios, 4 clean runs),
  float overlays in the responsive matrix, static guard: watchScreen identical in
  web_ui.h and companion/web/viewer.js (checked it catches a planted change).
- companion/tests/dashboard_live.py: the real dashboard in Google Chrome + real
  Rust service + one board model: tap Pair, type the code, live 1280x720 H.264,
  zoom, float over Media inside the viewport, dock, unplug, auto-recovery. PASS.
- Fixed during review: phone-landscape #pad height (id rule outranked the slot),
  "on screen" tag stretching, duplicate name in the float, wheel hijacking page
  scroll (now Ctrl+wheel), retry message losing its reason.
- Responsive matrix with the floating window: first run 2607 problems, all in the
  new float states (nothing else regressed). Fixed real bugs: the minimised pill
  stayed mid-screen (now tucks to the bottom edge, or on screens <400px tall
  shrinks to a restore button over the logo tile); on 2400px+ displays (root zoom)
  the window was placed off-screen (all hand placement now converts through the
  page zoom); the expanded window is capped to the height available; while
  floating, the page gains scroll room the window's height. Probe: expanded =
  its own controls reachable; minimised = every page control reachable.
- Firmware size golden updated on purpose: +59 KB flash (+4.1%) for companion.h
  and the Screen tab, +2.5 KB static RAM (pairing tables). companion.h moved to
  ArduinoJson 7's current API: 0 compiler warnings from the new headers.
- Removed unused `key()` from the viewer (WebRTC asks for keyframes itself);
  added a scenario where the computer refuses the connection (ws.onerror).
- `python tests/run.py`: PASSED in 500 s - static 25, unit 138, ui 496, build 4;
  coverage contract OK (178/178 controls). Skips: 312 Linux-only, 35 pwsh.
  Board NOT flashed; hardware tiers NOT run.
- Windows (not run, no Windows here): dxgi.rs (Desktop Duplication, pointer
  drawn in, access-lost recovery, >2560 halved), mf.rs (Media Foundation H.264,
  low latency, constrained baseline, CBR, SPS/PPS guarantee), convert.rs (NV12,
  halve, pointer drawing: 6 tests). Whole workspace compiles and is clippy clean
  for x86_64-pc-windows-msvc. DTLS on native crypto (Apple / CNG): no C crypto
  library on macOS or Windows; Chrome re-verified (~375 ms first frame, ~28 fps).
  `examples/check_capture.rs` = the one-command on-device check for the owner.
- macOS packaging: package/macos.sh -> 7.9 MB app (LSUIElement, bundle id
  in.hidfi.companion, ad-hoc signed; Developer ID + notarize supported), 4.5 MB
  dmg (verifies). Launched from the bundle: 34 MB RSS, 0.0% CPU.
- Fresh-install fix: 20 s quiet search before "not on this network" (first start
  said it before mDNS answered). Measured: found in ~5 s, no false message.
- Commits on branch `companion`: e2a8660, 652b046, bc173d3 (not pushed).
- The app's window (panel.rs): HID-Fi look, steps (find board -> pair -> show),
  the code in 44px type, buttons from the same model as the tray. Opens by itself
  on first run and when the user must act (needs_window, tested), not again for
  something already dismissed. 34 MB RSS without it, 73 MB while open.
