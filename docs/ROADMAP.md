# Roadmap - decided plans, not built yet

Each item records what was decided and why, so work can resume without
re-researching. Order is the owner's current priority.

## 1. Updates over the air (next)

See [OTA.md](OTA.md).

## 2. Jev / Laya: say what you want, the board does it

**Decision (2026-10-23):** the **board calls the decision model itself** - no
Companion needed for one-shot commands, so it works on any computer with
nothing installed, and the board stays the authority.

- **The model is a decider, not a writer.** Jev (TypeSafe, `POST
  https://api.typesafe.ai/v1/systemone`, question types `choice` (≤255 options),
  `noul` (yes/no probability), `score` (≤10 levels), `box`; 70-500 ms;
  $0.042 per million input tokens; cloud only, closed weights, data retention
  undocumented) picks from menus the board builds. Laya (Convai, Apache-2.0,
  ~421M ModernBERT, local, same three question types) is the open, offline
  alternative.
- **The menu already exists:** `actions` (the ~80 action names valid on the
  detected OS), `do`, `open`, `steps` (docs/COMMANDS.md, docs/AGENT_CONTROL.md).
- **Phase 1 (board only):** a command box on the dashboard (typed, or the
  phone keyboard's dictation). The board asks ~10 questions in one request:
  kind of request, which action, which words name the app, which words to type
  (spans of the person's own sentence only - never text from the model or the
  screen), is it a command, is it complete, is it destructive. Confident and
  safe: run; unsure or destructive: confirm on the phone. Shows what it
  understood and the confidence, with Stop.
- **Board work:** HTTPS client in its own FreeRTOS task (never on the input hot
  path); TLS with the core's CA bundle and SNTP time; the API key stored like
  the WiFi password (set over USB or WiFi with the PIN, never readable back);
  a configurable endpoint so a **Companion or LAN host running Laya behind the
  same API shape** works unchanged (sold boards: Jev with the buyer's own key,
  or fully offline with Laya). Says so when there is no internet.
- **Phase 2 (needs the Companion):** multi-step goals that must see the screen:
  the Companion reads the OS accessibility tree (UIA, AX, AT-SPI), offers
  "click element N" candidates, the board executes with `mouse_abs`; verify
  each step; step budget. Multi-monitor coordinate mapping is the risk.
- **Tests:** a fake decider in board_sim; recorded real answers replayed; an
  instruction benchmark per OS for Jev and Laya; safety scenarios (wrong
  certificate, no key, no internet, low confidence, destructive, hostile screen
  text); an opt-in live run with the key from the environment.
- References: typesafe.ai, docs.typesafe.ai, github.com/ZJU-REAL/CUA-JEV,
  github.com/moritzkremb/jev-voice-browser,
  madewithjev.com/builds/computer-use-without-screenshots.

## 3. Voice

- **The microphone is the phone** (a mic on the board defeats remote control).
  Browsers allow the mic (getUserMedia) only on secure pages; the dashboard is
  plain http on a LAN address.
- **Step 1:** the phone keyboard's own dictation into the command box (works on
  http, no license question) - feeds item 2.
- **Step 2:** HTTPS with a publicly trusted certificate per board (Plex-style
  name like `09f7c8.<domain>` resolving to the LAN IP, Let's Encrypt via a small
  service the vendor runs) so the page can stream audio; the board relays to the
  Companion for transcription. Self-signed certificates are ruled out (warning
  on every phone). A phone app is the alternative.
- **Speech to text in the Companion:** Moonshine (MIT code and models; avoid
  its legacy non-streaming non-English models, non-commercial) first -
  streaming, Windows/macOS/Linux, command recognition. Cactus Whistle (Apache
  model, runs on the Apache `needle` engine; Windows x64 unconfirmed) as a
  candidate to benchmark. **Never ship the main Cactus engine**
  (github.com/cactus-compute/cactus: free only under $2M funding and revenue).
- **INMP441 (desk use only):** VDD 3V3, GND, L/R GND, SCK GPIO5, WS GPIO6,
  SD GPIO4 (free on the N16R8: avoid 0/3/45/46, 19/20, 26-32, 35-37, 43/44, 48).

## 4. Ten-year upkeep

- Upgrade the ESP32 core deliberately (the red `latest-dependencies` job: core
  3.3.12 adds ~16 KB static RAM; the board has ~217 KB free) - skill
  `hidfi-dependency-upgrade`.
- Settings schema version + in-place migration; tests that upgrade from the
  previous release with data and check it survives.
- Archive the exact core and libraries with each release (rebuildable later).
- Companion: Developer ID signing + notarization (macOS), Authenticode
  (Windows), an "update available" notice; CI on each new OS release.
- Check Espressif's ESP32-S3 longevity commitment before selling boards.
