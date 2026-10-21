# HID-Fi Companion: design

Decisions and the reasons for them. When a decision changes, edit it here and say
why in `PROGRESS.md`. Requirement IDs refer to `REQUIREMENTS.md`.

## The shape of it

```
 phone (dashboard, served by the board)
   │  1. status: which PCs are paired, their LAN address and DTLS fingerprint
   │  2. screen_ticket (PIN-gated WS command)
   ▼
 ESP32-S3 board ── directory + trust anchor only, no video (F3)
   ▲  HTTP heartbeat every ~20 s, HMAC-signed (F2: never a WebSocket)
   │
 PC: hidfi-companion (Rust, tray)
   ◀── 3. phone connects straight to the PC: ws signaling + WebRTC media (encrypted)
```

Mouse and keyboard keep going through the board's HID, exactly as today (D9).

## Why WebRTC for the video

- Mandatory encryption (DTLS-SRTP), so the screen is never plaintext on the LAN.
  The dashboard is plain http, so WebCrypto is unavailable (D7) and we cannot do
  our own encryption in the browser cheaply. `RTCPeerConnection` works in an
  insecure context for receive-only use.
- Phones decode H.264 / VP8 in hardware or efficiently in software. Congestion
  control and jitter buffering come with it.
- Server side: `str0m` (pure Rust, sans-IO, ICE-lite). The browser's host
  candidates are hidden as mDNS names, so the agent is ICE-lite with real host
  candidates and learns the phone's address as a peer-reflexive candidate.

**Codec plan.** Use the OS's own hardware H.264 encoder (VideoToolbox, Media
Foundation, VA-API), which is OS-licensed, and fall back to software VP8
(libvpx, BSD, royalty-free) everywhere. No software H.264: openh264 built from
source is not covered by Cisco's patent licence.

**Idle costs nothing (A3, A18).** No viewer: no capture, no encoder. Viewer and
static screen: damage-driven capture, no frames sent.

## Man in the middle

Signaling travels over plain ws on the LAN. An attacker could swap the SDP and
relay. The agent registers its DTLS certificate fingerprint with the board in
each HMAC-signed heartbeat. The dashboard reads the expected fingerprint from the
board and refuses any answer whose `a=fingerprint` differs. Faking the
registration needs the pairing key. This trusts the dashboard-to-board link
exactly as much as the rest of HID-Fi does (PIN, vault, keystrokes).

## Two surfaces on the board

- **`POST /api/companion`**: the app's own endpoint, outside `processCommand`, so
  the PIN gate and the LAN gate stay exactly as they are. Authenticated by the
  pairing key, not the access PIN (the app never knows the PIN). Body capped at
  768 bytes before it is copied. Rate limited per IP. Refuses everything with
  `pin_required` while no access PIN is set (F4).
- **Dashboard commands** through `processCommand`, PIN-gated like all others:
  `companion_list`, `companion_pair`, `companion_unpair`, `screen_ticket`.

## Pairing (first run): numeric comparison with commitment

This is the Bluetooth LE Secure Connections pattern. The commitment matters:
without it, a man in the middle could grind keys until both six-digit codes match
(about 10^6 X25519 operations, a few seconds).

All values are lowercase hex. `H` is SHA-256. `||` is concatenation of the raw bytes.

1. App → board `{op:"hello", v:1, id, name, a_pub, commit}`.
   `id` is 16 random bytes, fixed per install. `commit = H("hidfi-commit" || a_pub || nA)`,
   where `nA` is 16 random bytes kept secret for now. The board keeps it as
   *pending* in RAM (max 4, dropped after 2 min). The app polls `hello` every 1 s
   while pending.
2. The dashboard lists pending PCs. The user taps Pair (`companion_pair {id}`).
   The board makes `b`, `b_pub` and `nB` (16 bytes).
3. The app's next `hello` gets back `{state:"confirm", b_pub, nB}`. The app sends
   `{op:"reveal", id, nA}`. The board checks `commit`.
4. Both compute `code = (u32_be(H("hidfi-sas" || a_pub || b_pub || nA || nB)[0..4]) mod 10^6)`,
   shown as six digits. The PC shows it. The user types it into the dashboard
   (`companion_pair {id, code}`). Three wrong codes abort the pairing.
5. Both derive `K = HKDF-SHA256(ikm = X25519(secret, peer_pub), salt = H(a_pub || b_pub || nA || nB), info = "hidfi-companion-v1", 32 bytes)`.
   The board stores `{id, name, K}` in NVS namespace `compan` (max 4 PCs, F6). `K`
   is never returned by any command. Unpairing deletes it. Resetting the access
   PIN deletes every pairing (same family as the vault rule).
6. The app knows the pairing worked when its first `beat` is accepted.

## Heartbeat (the app announcing where it is)

Every 20 s (1 s while a viewer is waiting):

1. `{op:"chal", id}` → `{chal}`: 16 random bytes, single use, valid 30 s.
2. `{op:"beat", id, port, fp, tn, chal, mac}`. Here `fp` is the SHA-256 of the
   app's DTLS certificate (stable, kept in app data), and `tn` is a fresh ticket
   nonce (16 bytes) the app remembers for 90 s. The proof is
   `mac = HMAC-SHA256(K, "beat|" + id + "|" + port + "|" + fp + "|" + tn + "|" + chal)`.

The board records the **source IP of the request** (not a claimed one), port, fp,
tn and last-seen, in RAM only. It counts as online for 60 s after a beat.

## Viewer tickets

The dashboard sends `screen_ticket {id}`. The board makes `vn` (16 random bytes)
and returns `{ip, port, fp, tn, vn, ticket}`, with
`ticket = HMAC-SHA256(K, "tkt|" + tn + "|" + vn)`. The dashboard opens
`ws://ip:port/v1/signal` and sends `{t:"hello", tn, vn, ticket}` first.

The app checks: `tn` is one of its own, under 90 s old; `vn` has not been used
before; the HMAC is right. It allows at most 2 viewers.

Then SDP offer and answer. The dashboard refuses an answer whose `a=fingerprint`
is not `fp`.

## Finding the right board (A7)

- The network side: mDNS `_hidfi._tcp` and `/api/status`, which carries `mac`.
- The USB side: the board's USB serial number is its efuse MAC in hex. The ESP32
  core turns `"__MAC__"` into `%02X x6` in `USB.cpp` `ESPUSB::begin`. The agent
  lists USB devices with Espressif VID 0x303A and reads `iSerial` driverlessly
  (SetupAPI / IOKit / sysfs).
- **A board is only auto-selected when its network MAC equals the iSerial of a
  device on this PC's own USB.** Another HID-Fi on the LAN, or another Espressif
  device on this PC, never matches. Two matches: the tray asks, once, and
  remembers.
- No firmware USB change is needed (F8).

## Topologies

| Phone | PC | Board | Result |
|---|---|---|---|
| home WiFi | home WiFi | joined home WiFi | Direct, best |
| board's WiFi, sharing on (`ap_share`, NAPT) | home WiFi | joined | Works through the board's NAT. One radio carries it twice, so quality is capped and HID latency is guarded |
| board's WiFi, sharing off | home WiFi | joined | Dashboard explains and offers to turn on sharing |
| any | any | board's WiFi only | The agent cannot reach the board, so it cannot pair. Needs a USB vendor channel (F8, owner decision) |

## Network resilience (A8)

- The heartbeat backs off 1 s to 30 s and follows `.local` and address changes
  (the board's address moves: `lan_url()` lesson).
- The agent rebinds on interface changes, and on PC sleep/wake.
- The dashboard reconnects with a new ticket. A WebRTC ICE restart is tried
  before a full renegotiation, and the last frame stays shown, marked
  "reconnecting".

## DoS (A10)

- The agent listens only on private LAN addresses, never 0.0.0.0 on a public
  interface.
- Per-IP connection rate limits, bounded message sizes, and a signaling socket
  that is closed if it shows no valid ticket within 5 s.
- Only an HMAC check is done before authentication. At most 2 viewers.
- Board side: the pending pairing list is bounded (4) and expires (2 min), and
  `companion_hello` is rate limited per IP.

## The app

- Rust workspace in `companion/`: `core` (board link, discovery, pairing,
  protocol, no OS code), `capture` (per-OS backends behind one trait), `encode`,
  `rtc` (str0m), `app` (tray, settings window, service lifecycle).
- Tray: `tray-icon` + `muda`. Settings / first-run window: Tauri v2, styled with
  HID-Fi's tokens and sprite. **The window is destroyed when closed**, so the
  resident process is the tray plus an idle runtime.
- Capture backends: Windows Graphics Capture / DXGI Desktop Duplication; macOS
  ScreenCaptureKit (12.3+); Linux PipeWire via the xdg-desktop-portal with a
  persisted restore token (Wayland), XShm (X11).
- Installers: MSI (Windows), signed + notarized dmg (macOS), deb/rpm/AppImage
  (Linux). Auto-update via Tauri updater with signature check.

## Known limits, stated up front

- Lock screen, UAC secure desktop and the login window cannot be captured by a
  user-session app. That needs a SYSTEM service / root daemon. Phase later; the
  HDMI-capture route still covers BIOS.
- Android, iOS and ChromeOS hosts need their own app (MediaProjection etc.), not
  this one.
- Code signing needs the owner's certificates (Apple Developer ID; a Windows
  signing certificate). Without them, macOS Gatekeeper and Windows SmartScreen
  warn, and macOS resets the Screen Recording permission on every unsigned build.
