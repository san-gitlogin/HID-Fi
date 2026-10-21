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

## Next (in order) - updated 2026-10-21, end of the owner-away session

Everything is on branch `companion` (local, not pushed). Each step needs the owner.

1. **Flash the board** (`./flash_esp.sh -c`; keeps NVS). Then `python tests/run.py
   --board` (moves the mouse net zero, volume down/up) - it now includes the
   companion lines in test_sim_contract.py, which prove the model against the
   real firmware.
2. **macOS Screen Recording**: open `companion/target/package/HID-Fi Companion.app`
   (or the .dmg), allow Screen Recording when asked (System Settings opens from
   the app's window/menu). Then `cargo run --release -p hidfi-stream --example
   check_capture` at the Mac confirms real capture + encode and writes a sample.
3. **Pair for real**: dashboard -> Screen -> Pair -> type the code the app shows.
   Watch the phone; try zoom, float, unplug/replug.
4. **Windows PC**: `cargo build --release -p hidfi-companion` and run
   `cargo run --release -p hidfi-stream --example check_capture` there (needs Rust
   on that PC, or a build made on it). First run of the Windows backends.
5. Owner decisions: code-signing certificates (Apple Developer ID, Windows), and
   whether to push / open a PR.

Still to build: Linux capture/encode (X11/PipeWire, VA-API or Cisco's openh264
loaded at runtime), Windows hardware (async) encoder, Windows installer, lock
screen / login window (system service), multi-display picker in the window.

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

### 2026-10-21 (owner at work, remote; "go")
- Flashing BLOCKED: only the board's native USB enumerates on this Mac (serial
  B81F3FDA1A2C); the COM cable carries no data (no CH343 on the bus). Firmware
  has no OTA or reboot-to-bootloader, so a data cable in COM is required.
- Multi-viewer: limit 4, bitrate follows the slowest viewer (per-viewer
  estimates), busy viewers wait and join. multi_view.py: 4 Chrome viewers at
  1080p 26-30 fps, CPU flat 1 -> 4 viewers.
- Watch only (#watch): no dashboard flash (:target CSS), fading bar, full screen,
  keys, chooser, wording; bug fixed: bar never faded after going live.
- dashboard_live.py: phone + watch screen + third dashboard, one PIN, together.
  "closed" reason sent when unplugged.
- Linux: ksni tray + notifications, X11 capture (XDamage, XFixes cursor),
  OpenH264 via Cisco binary (SHA-checked; bitrate via rebuild). Zig as a cross C
  compiler for checking (tools/). All three targets clippy -D warnings clean.
- Media on UDP 47820-47859 (firewall-friendly). Installers + docs/COMPANION.md +
  dashboard links. Bug fixed: links inside the picture were untappable.
- CI workflow companion.yml (manual) to RUN everything on real Windows/Ubuntu.
- Full suite PASSED (ui 503).

### 2026-10-21 (owner back; both cables in)
- Flashed twice (COM /dev/cu.usbmodem5CBC4099421). New firmware answers
  /api/companion; status has comp_*.
- Bug found on the real Mac (macOS 15.6): host OS read as Windows (string reads
  2,2,1,1,3,3,4,4,0 -> 4 re-reads of 9). Fix: host_detect.h adds the per-index
  pairing rule; 11 cases + 5 planted mutations in tests/unit/test_host_detect.py.
  Real board: "mac" on 4/4 boots.
- `--board` tier: 87 passed, 2 failed - both the sim contract racing a board
  still joining WiFi after the port reset it. Fixture now waits for the phase to
  settle; rerun 56/56.
- tests/hardware.py (new): REAL board + REAL service (real USB/network/mDNS,
  test-pattern picture), Chrome loads the dashboard from the board with NO flags:
  pairing by code, first frame 1.3 s, 28 fps, four screens at once (25-54 fps),
  a fifth waits then joins, board counts 5 dashboards on one PIN, no page
  errors, test computer removed afterwards. 11/11.
- Still not verified: real macOS capture (needs Screen Recording permission);
  Windows/Linux runtime (needs the manual CI run, which needs a push).

### 2026-10-21 evening - owner's live test with three phones (their findings)
Owner: "works really well", but: lag then fine; 1 fps after using float, then
everywhere; float badge covers the picture; "5 connected" with 3 devices; the Mac
app froze on step 2; steps advanced with no explanation; several macOS
permission prompts nobody warned about; Screen tab with no app = just a link
(404 until pushed), no guidance; multiple monitors (3-4) a big concern.
- DONE 051923d: the 1 fps collapse (keyframe storm + one encoder for all +
  no BWE probe target). Two qualities, per-viewer keyframes, VT hard limit.
- TODO (in order): Mac app freeze (diagnose; log a main-thread stall);
  window explains each step (what it found, how); macOS permissions documented
  + guided (Screen Recording, "Quit & Reopen", macOS 15 monthly "bypass the
  private window picker", Local Network); ad-hoc signature = permission lost
  on every update (needs Developer ID); float badge into the float title bar;
  device count (board counts pages: count distinct devices, show pages);
  multiple displays (choose per viewer, hot-unplug falls back); Screen tab
  empty state = install/open/pair steps that work offline; first-frame 14 s
  seen once (not reproduced: 0.6 s).
- DONE 27c088a: window focus stealing (the "freeze"); steps explain themselves.
- DONE f384487 + this commit: several displays (per viewer, unplug falls back,
  Windows multi-GPU, Linux RandR crop); phone-slept-then-PIN stuck ticket
  (2 scenarios); devices not pages; float badge in title; offline setup steps;
  the newly paired computer is selected; board served only 5 pages (library
  limit) -> 8 via build_opt.h + "board is full" message; runner names test
  files that fail to load. Real board: hardware.py 11/11 with the owner's Mac
  paired, board_pages.py 5/5, sim contract 56/56.
- Owner's idea, parked by them ("leave it"): watching PCs without a board.
  As built: only a computer with this board on its USB can pair or show its
  screen (unplugged, its picture ends: "HID-Fi is not plugged into ..."), so
  whatever the Screen tab shows is the computer the board controls.

### 2026-10-21 night - "the Rust app freezes the Mac"
- Cause 1: the owner still ran the morning build (focus stolen every 500 ms,
  fixed in 27c088a). Cause 2 ("not on this network" after a flash): macOS
  withdrew the app's local network access when I rebuilt its bundle under it.
  The board never lost WiFi (0 drops).
- Fixed: event-driven window loop (no timer, stalls >100 ms logged); restart
  into a replaced bundle (macOS); re-ask pairing after a board restart;
  unreachable addresses logged with the reason; per-OS cause + settings
  button when unreachable; later steps show only their title; render check
  for all 12 window states (panel_review.py); per-OS guidance on the
  dashboard (what each OS asks; where the app lives).
- Linux (VM on the SSD): companion/tests/linux/run.sh passes - build, unit
  tests, app without tray, real X11 + OpenH264 + WebRTC on two monitors,
  display switch 0.1 s. HID-Fi hostsim tier: 312/312.
- Mac: full suite green on a quiet machine (two "nothing is drawn" layout
  flakes appear only when the Mac is loaded by a VM or e2e runs); all four
  companion e2e suites; real board: board_pages 5/5, hardware 11/11.
- Waiting on the owner: Screen Recording for the new build (one step), and
  approval to push for the Windows CI run.
