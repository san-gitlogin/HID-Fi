# Updates over the air - the plan (not built yet)

The owner's brief (2026-10-28): the board updates itself the way an iPhone
does. When it is on the internet and a new release exists, the dashboard says
so; a clear **Update** button installs it, only with the person's consent, after
showing **what changed since the version this board runs** and **how big the
download is**. It must be smooth, never leave a board unusable, follow the
guidelines for over-the-air updates, and be covered by automated tests.

## What the person sees

1. Settings shows *HID-Fi 4.0.4 - up to date* or *Update available: 4.1.0*.
   The board checks when it is online (at start, then about once a day), never
   while a key or button is held, never on the board's own WiFi without internet.
2. *Update available* opens a sheet:
   - what changed: every CHANGELOG section between the running version and the
     new one (not only the newest), in plain words;
   - the download size, and that settings, PINs, passwords, macros, tiles and
     pairings are kept;
   - **Update now** / **Later**. Nothing downloads before Update now.
3. While updating: a progress bar (downloaded / verified / installing /
   restarting). Typing and the mouse keep working until the restart.
4. After the restart the dashboard reconnects by itself and says *Updated to
   4.1.0*. If the new firmware fails to start or to come online, the board goes
   back to the previous version by itself and the dashboard says so.

## How (to verify before building - several are assumptions)

- **Two app slots.** Today's layout (`huge_app`) has one 3 MB slot, so no OTA
  is possible. The ESP32 core's `default_16MB.csv` has two 6.4 MB slots
  (`ota_0`, `ota_1`) with `nvs` at the same offset and size (0x9000, 0x5000),
  so a one-time USB flash to the new layout should keep every setting. Prove
  that on a board before relying on it (flash old layout + data, switch, read
  everything back). flash_esp.sh then writes the new partition table; the
  release zip and FLASH.md change too.
- **Rollback.** Boot the new slot as "pending verify"; mark it valid only after
  a self-test (USB HID up, WiFi back, the dashboard served). Check whether the
  Arduino core's prebuilt bootloader has `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`;
  if not, the app must do it (`esp_ota_mark_app_valid_cancel_rollback` /
  boot-count fallback). Never mark valid before the self-test.
- **Authenticity.** The board must refuse firmware that is not ours: sign the
  image at release (an Ed25519 or ECDSA key kept off GitHub), verify on the
  board before switching slots, plus SHA-256 from SHA256SUMS. TLS to GitHub with
  the core's CA bundle and real time (SNTP). An anti-rollback rule: never
  install an older version than the running one unless the person asks.
- **Where it comes from.** GitHub Releases: a small manifest asset per release
  (version, size, sha256, signature, minimum firmware it can update from, the
  CHANGELOG sections) plus the app image. Mind GitHub API rate limits and
  redirects to objects.githubusercontent.com.
- **Never blocks the hot path.** Download and write in a background task on
  the second core; pointer and keyboard frames stay first (AGENTS rule 7). Stop
  safely if the WiFi drops (resume or restart the download; the running slot is
  untouched until the image is complete and verified).
- **Data.** Settings get a schema version; a new firmware migrates old data in
  place, never deletes it (`docs/TESTING.md` lessons: a test once deleted a
  person's macro).
- **Guidelines to check at the source:** ETSI EN 303 645 (consumer IoT: secure
  update mechanism, timely updates, telling the user), the EU Cyber Resilience
  Act (security updates for the support period, SBOM), Espressif's OTA and
  secure-boot documentation.

## Tests (all automated)

- Board model (`board_sim.py`): update check, manifest parsing, consent flow,
  progress, a failed download, a bad signature, a bad hash, an older version,
  rollback reported after restart. Dashboard BDD scenarios for every state.
- Unit: version compare, manifest validation, signature check against vectors.
- Real board (`tests/board/`): download from a local HTTPS server on the Mac,
  install, restart, version reported; a deliberately broken image rolls back;
  power cut mid-download (USB unplug simulated by reset) leaves the old version
  running; settings, macros and tiles intact after each.
- Upgrade path: flash the previous release, fill it with data, update over the
  air, everything still there.

## Before this, once

The one-time switch to the two-slot layout over USB, tested as above, on both
boards (DA1A2C, 09F7C8), keeping their data.
