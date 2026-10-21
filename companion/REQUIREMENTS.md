# HID-Fi Companion: requirements

The single list of what the companion app and the Screen tab must do. Every item
has an ID so the plan, the tests and commits can point at it. **U** = the owner
asked for it (2026-10-21). **D** = derived from HID-Fi's existing rules or found
while studying the code; the owner did not say it, but HID-Fi breaks without it.

Status: `todo` / `wip` / `done (how verified)` / `blocked (why)`. Never mark
`done` without saying how it was verified.

## The app on the PC

| ID | Requirement | Src | Status |
|---|---|---|---|
| A1 | Written in Rust. | U | todo |
| A2 | Live screen streaming to the dashboard, not only screenshots. | U | wip: live H.264/WebRTC stream works end to end in real Chrome (system test); real screen capture written, unverified (no permission here) |
| A3 | Very fast; low CPU, memory and power; runs 24/7 in the tray. Idle (no viewer) must cost ~0% CPU: no capture, no encode. | U | wip: idle 0.0% CPU measured (no viewer = no capture, no encode); ~6% of one core for encode+send at 1080p30 measured; tray/app RAM not yet measured |
| A4 | Very easy to install: one download, a short guided first run, no confusing state. Everything that can be automated is. | U | todo |
| A5 | Tray / menu-bar icon on Windows, macOS, Linux that shows state (no board, pairing, ready, someone watching, error). | U | todo |
| A6 | Supports every OS it can, not just the three. Unsupported ones are listed honestly with why. | U | todo |
| A7 | Finds the board automatically, and never picks the wrong board, even another HID-Fi or Espressif device from the same maker on the same PC. | U | wip: selection + locator done, unit-tested with fakes (wrong boards, non-HID-Fi answers, duplicates); real USB/mDNS untested on hardware |
| A8 | Survives WiFi drops, network changes, board reboots, PC sleep/wake, address changes; resumes the stream without user action. | U | wip: board link survives an unreachable board (backoff, pairing kept) and a board that forgets us (re-pair), tested against the model; stream resumption todo |
| A9 | Every error handled; no panic reaches the user; a crash restarts itself. | U | wip: agent never panics on malformed replies (tested); app-level crash restart todo |
| A10 | Secure: authenticated, encrypted, resistant to man-in-the-middle and DoS. | U | wip: pairing/ticket crypto agreed by 4 implementations; DTLS-SRTP; fingerprint pinned via board (refusal tested in Chrome); used/forged tickets refused; private peers only; conn/viewer caps; bad-ticket lockout |
| A11 | Looks like HID-Fi (same palette, icon family, type), not generic or "AI generated": real icons, clear buttons, good state management. | U | todo |
| A12 | Follows industry standards (signed installers, auto-update with signatures, platform conventions, standard protocols). | U | todo |
| A13 | Tested end to end, every function, across OSes and across weak and strong CPUs. | U | todo |
| A14 | Multiple monitors: pick which one; follows hot-plug, resolution, DPI and rotation changes. | D | wip: encoder rebuilt on any size change (tested); display choice stored; hot-plug follow via SCK unverified |
| A15 | Shows on the PC that the screen is being watched (tray state change), so watching is never covert. | D | wip: service counts watchers (tested); tray indicator todo |
| A16 | Starts at login (opt-out), one instance only. | D | todo |
| A17 | Uninstalls cleanly: login item, firewall rule, files, keys. | D | todo |
| A18 | Adapts quality to the link and the CPU (resolution, fps, bitrate), and sends nothing while the screen does not change. | D | wip: TWCC estimate drives encoder bitrate; still screen sends a keyframe every 2 s only (tested) |

## The board (firmware)

| ID | Requirement | Src | Status |
|---|---|---|---|
| F1 | Communication with the app is seamless and needs no user action after pairing. | U | todo |
| F2 | The app must NOT hold a WebSocket to the board. Three features count WS clients and would break: release-all-held-keys on last disconnect (`onWsEvent` DISCONNECTED), radio sleep in `PWR_BALANCED` (`updatePower`), and AP auto-off. | D | done (design): app uses `POST /api/companion` only; verified by reading `onWsEvent`, `updatePower` |
| F3 | Video never flows through the board on the main path: its single radio carries the HID hot path, and "latency is the product". The board is a directory and trust anchor only. | D | todo |
| F4 | Screen viewing is refused unless an access PIN is set (same rule as saved PC passwords). | D | done in firmware + model; unit-tested (`tests/unit/test_companion.py`) |
| F5 | New commands follow the AGENTS.md feature recipe: handler + prototype + `processCommand` branch, `board_sim.py` model, `test_sim_contract.py` line, `docs/COMMANDS.md`, SECURITY.md table row. | D | wip: handler, dispatch, model, contract lines, COMMANDS.md, SECURITY.md done; not run on hardware |
| F6 | Pairing data lives in its own NVS namespace, survives reflash, is bounded (max PCs), and never returns a secret. | D | done in firmware: NVS `compan`, max 4, key never in a reply (unit-tested on model); hardware unverified |
| F7 | Nothing new blocks `loop()`; every new input from outside is bounded before copying. | D | done: bodies capped at 768 before parse, rate limit, `compTick` 1 Hz expiry; X25519 runs once per pairing (tens of ms) |
| F8 | No change to the USB descriptor without the owner's decision (it changes enumeration, OS detection and the macOS "allow accessory" prompt for every board). | D | kept: no USB descriptor change |

## The dashboard

| ID | Requirement | Src | Status |
|---|---|---|---|
| D1 | New Screen tab. | U | done: Screen tab (8th tab, after Trackpad); 23 BDD scenarios incl. live WebRTC; real Chrome H.264 e2e (dashboard_live.py) |
| D2 | Portrait: screen on top, the combined mouse + keyboard widget from the landing page below. Landscape: screen left, widget right. | U | done: portrait stacked, landscape side by side (moves the real #padwrap); checked by screenshot on 4 sizes + responsive matrix |
| D3 | Floating screen window, draggable within the dashboard, usable while using the other tabs. | U | done: floating window over any tab, drag/resize/minimise/dock/close, kept inside the viewport and clear of the tab bar; scenarios + e2e |
| D4 | Zoom in and out of the screen (pinch, wheel, buttons), with pan, and a reset. | U | done: pinch, double tap, Ctrl+wheel, buttons, pan when zoomed, fit; scenarios |
| D5 | Clear connection states: no app, app offline, pairing, connecting, live, reconnecting, phone cannot reach PC (and what to do). | D | done: messages for no PIN, nothing paired, choose, offline, connecting, busy, fingerprint, unplugged, unreachable (AP vs LAN); retry keeps the reason |
| D6 | Follows every dashboard rule in AGENTS.md: SVG sprite icons only, no emoji, positional WS reply queue kept 1:1, socket handlers check `ws === sock`, nothing needs Refresh, BDD scenario for every control, responsive matrix covers the new tab, sheet and floating window. | D | wip: sprite icons, positional queue via send(), scenarios, coverage contract and responsive matrix - full run pending |
| D7 | Works in the phone's normal browser over plain http (no secure context, so no WebCrypto). | D | done: viewer.js uses no WebCrypto; fingerprint compare is plain string |
| D8 | The floating window never covers a control permanently: it can be moved, minimised and closed, and it remembers its place per device. | D | done: drag/resize/minimise/close, position remembered per device (localStorage), re-clamped on resize |
| D9 | Input stays on the existing HID path, so the mouse and keyboard keep working if the app dies. | D | done by design: video is separate; HID path untouched |

## Process

| ID | Requirement | Src | Status |
|---|---|---|---|
| P1 | HID-Fi stays stable: zero regressions, `python tests/run.py` PASSED before "done", hardware tiers only with the owner's OK. | U | ongoing |
| P2 | Keep requirements and progress in files that survive compaction: this file, `DESIGN.md`, `PROGRESS.md`, plus a memory entry. | U | ongoing |
| P3 | Ask before flashing, pushing, installing toolchains, or any test that touches the owner's computer. | D | ongoing |
