# Updates - the board and the Companion (plan; building started 2026-10-28)

The owner's brief (2026-10-28): the board updates itself the way an iPhone
does. When it is on the internet and a new release exists, the dashboard says
so; a clear **Update** button installs it, only with the person's consent, after
showing **what changed since the version this board runs** and **how big the
download is**. It must be smooth, never leave a board unusable, follow the
guidelines for over-the-air updates, and be covered by automated tests.

The owner's second brief (2026-10-28, same day):

- **The Companion takes part.** It knows every board connected to this
  computer, the firmware each runs, whether each is compatible with this
  Companion, and whether the board or the Companion needs updating - and it
  takes care of it.
- **When an update cannot go over the air, the Companion installs it over the
  cable** - COM (CH343), the native USB port, or both - carefully.
- **Checkpoints at every step**, so we always know exactly where an update
  stopped, and whether to go back or carry on.
- **Cancel at any moment**, safely: no data lost, no board left unusable.
- **Nothing may harm the hardware.** Every situation is handled, none left to
  chance.
- **Tests at every level** - unit, regression, feature, product, end-to-end
  system - mature enough to carry what comes next (Jev, which needs the
  internet; bill and usage monitoring; more).
- **Everything taken from the internet is the latest**, and the agent rules say
  so: check the web, check whether a dependency's new release affects the
  product, test it.

## What the person sees

1. Settings shows *HID-Fi 4.0.4 - up to date* or *Update available: 4.1.0*.
   The board checks when it is online (at start, then about once a day), never
   while a key or button is held, never on the board's own WiFi without internet.
2. *Update available* opens a sheet:
   - what changed: every CHANGELOG section between the running version and the
     new one (not only the newest), in plain words; a security fix says so first,
     with the risk it removes (ETSI 5.3-11);
   - the download size, and that settings, PINs, passwords, macros, tiles and
     pairings are kept;
   - that the board restarts once, about how long keyboard and mouse are gone
     (ETSI 5.3-12);
   - **Update now** / **Later**. Nothing downloads before Update now.
3. While updating: a progress bar (downloaded / verified / installing /
   restarting). Typing and the mouse keep working until the restart.
4. After the restart the dashboard reconnects by itself and says *Updated to
   4.1.0*. If the new firmware fails to start or to come online, the board goes
   back to the previous version by itself and the dashboard says so.

## Verified at the source (2026-10-28)

Each line was checked in the installed core (esp32 3.3.1, IDF release/v5.5
libs `idf-release_v5.5-129cd0d2-v4`) or in the published text, not assumed.

| Question | Answer | Where |
|---|---|---|
| Does `default_16MB` keep NVS where it is? | Yes. `nvs` 0x9000 size 0x5000 and `otadata` 0xe000 size 0x2000 are identical in `huge_app.csv` and `default_16MB.csv`. `app0` stays at 0x10000 and grows 3 MB -> 6.4 MB; `app1` is new at 0x650000. | `tools/partitions/*.csv` |
| Does anything live in the partitions that move (`spiffs`, `coredump`)? | No. The firmware uses no file system and no partition API; all data is NVS (namespaces `wifi_cfg`, `hid_cfg`, `macros`, `pcprof`, `vault`, `compan`). | grep of `hid_fi/` |
| Does the S3 board menu offer `default_16MB`? | No (only `huge_app`, `app3M_fat9M_16MB`, ...). The build copies a `partitions.csv` from the sketch folder before any menu choice, so the layout lives in the repo as `hid_fi/partitions.csv`. | `platform.txt` prebuild hooks 1-3 |
| Does the prebuilt bootloader support rollback? | Yes: `CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`, and the bootloader ELF carries the `ESP_OTA_IMG_PENDING_VERIFY` / `ABORTED` handling. Hardware anti-rollback (eFuse secure version) is **off** and cannot be turned on without rebuilding the libs. | `esp32s3/sdkconfig`, `bootloader_*.elf` |
| Who marks a new app valid? | **The core, before `setup()`**, unless the sketch says otherwise: `initArduino()` calls the weak `verifyRollbackLater()` (default `false`) and then marks a pending app valid at once. HID-Fi must define `bool verifyRollbackLater() { return true; }` and mark valid itself after the self-test, or rollback protects nothing. | `cores/esp32/esp32-hal-misc.c:277-291` |
| Does the core verify signatures? | No. `Update` (`Updater.cpp`) checks the image header and an optional MD5, nothing more, and sets the boot partition inside `end()`. HID-Fi uses `esp_ota_*` directly so nothing can switch slots before our checks pass. | `libraries/Update/src` |
| What crypto is available without new dependencies? | mbedtls ECDSA with P-256 (`MBEDTLS_ECDSA_C`, `ECP_DP_SECP256R1`), hardware SHA and big-number units. **No Ed25519** (Curve25519 is there for key exchange only). TLS with the full CA bundle (`MBEDTLS_CERTIFICATE_BUNDLE_DEFAULT_FULL`), TLS 1.2 only. SNTP is in lwIP; the firmware does not start it today. | `esp32s3/sdkconfig` |
| Does a USB flash after an OTA still work? | Yes. The flasher writes `boot_app0.bin` at 0xe000 every time: an `otadata` whose only winning entry (sequence 1, state undefined) selects `app0`, so a board running from `app1` boots the freshly written `app0`. The four offsets in FLASH.md do not change. | `flash_esp.sh:213-219` |
| Where does the board download from? | `https://github.com/san-gitlogin/HID-Fi/releases/latest/download/<asset>`. No REST API call, so no 60-requests-an-hour limit. It answers 302 to `/releases/download/<tag>/<asset>`, then 302 to `release-assets.githubusercontent.com` (not `objects.` any more) with a **~1.3 KB Location header** - the board's HTTP client must take a header that long. Must be proven on a board. | `curl -IL`, measured |
| Is the image small enough? | 1,562,576 bytes today (v4.0.4), 24% of a 6.4 MB slot. | `hid_fi/build/hid_fi.ino.bin` |

### Found while checking: shipped firmware is exposed - fix before any OTA work

Checked 2026-10-28 against GitHub's advisory database and release feeds (no
search snippets):

| Advisory | Severity | Affects | Reaches HID-Fi? |
|---|---|---|---|
| GHSA-8cmm-3887-r32j: `WebServer` multipart boundary stack overflow, remote crash, potential code execution | critical | core <= 3.3.7 | **Yes.** Read in the installed 3.3.1 `Parsing.cpp`: any POST with `Content-Type: multipart/...` reaches `_parseForm()` before HID-Fi's handlers, so the PIN and the home-network gate never see it. Anyone on the board's WiFi - or on the home network when joined - can crash the board. Every release up to v4.0.4. |
| GHSA-28hv-fwm3-rpcq: Digest auth URI replay | high | <= 3.3.7 | No: HID-Fi never calls `authenticate()`. |
| GHSA-w887-pg2w-mfj2: `authenticate()` bypass | critical | <= 3.3.11 | No (as above). |
| GHSA-vqp8-ppw3-8mc7: `serveStatic()` path traversal | high | <= 3.3.11 | No: no file system, no `serveStatic`. |
| GHSA-g764-gwc3-75m5: lwIP DHCP **server** option parser out-of-bounds read | medium | IDF <= 5.5.4 | **Yes**: the board's access point runs that DHCP server. Our core is IDF v5.5.1. |
| GHSA-3j8v-xgrq-5vg8: `esp_http_server` WebSocket NULL dereference | high | IDF 5.5.4 | No: HID-Fi's WebSockets come from Links2004, not `esp_http_server`. |

Core **3.3.12** (2026-09-18, IDF v5.5.5) fixes both that reach us, and also:
`NetworkClientSecure` lost its CA bundle after `stop()` (fixed 3.3.9 - a daily
HTTPS check would fail from the second try on 3.3.1); TLS writes failing with
`MBEDTLS_ERR_NET_CONN_RESET` (3.3.2); `WebServer` hangs (3.3.12); NVS page
reclaim not surviving a sudden power-off (IDF 5.5.3). The core's own `Update`
gained signature support in 3.3.5 and needed a bypass fix in 3.3.8 - one more
reason HID-Fi checks signatures itself. No NVS format change within IDF 5.5.x.

So **step 0 is upgrading the core to 3.3.12** as a security release, by the
`hidfi-dependency-upgrade` procedure, before any OTA code; every later step is
then built and tested on the core it will ship with. It reaches users over the
cable, once - which is the case for building OTA at all.

### What "latest" was on 2026-10-28

arduino-esp32 3.3.12 (pinned 3.3.1); ESP-IDF v5.5.6 (core 3.3.12 ships v5.5.5);
esptool v5.6.0 (the core ships 5.1.0); espflash 4.6.0; ArduinoJson 7.4.3 and
arduinoWebSockets 2.7.2 (= pinned); Playwright 1.64.1; ETSI EN 303 645 V3.1.3
(still the newest); CRA harmonised standards EN 40000-1-x approved by CENELEC,
**not yet in the Official Journal** (expected around 25 Nov 2026 - re-check
then). `tools/dep_watch.py` repeats these checks on demand.

### A danger found while checking: NVS format and rollback

`initArduino()` **erases the whole NVS partition** when `nvs_flash_init()`
returns `ESP_ERR_NVS_NEW_VERSION_FOUND` (`esp32-hal-misc.c:294-298`). That
happens when an *older* IDF meets NVS that a *newer* IDF has rewritten in a
newer format. So a release built on a newer core, rolled back to an older one,
could wipe every setting, PIN, vault entry and macro. Rules that follow:

- a release's manifest records its core and IDF versions;
- the release gate refuses to publish an over-the-air image whose IDF NVS
  format differs from the oldest version it may update from (that one goes out
  as a USB-only release);
- the board test "update, then roll back, everything still there" runs for
  every release, against the previous release's build.

## The guidelines, read at the source

**ETSI EN 303 645 V3.1.3 (2024-09), 5.3 "Keep software updated"** (PDF from
etsi.org):

| Provision | Says | HID-Fi answer |
|---|---|---|
| 5.3-1 / 5.3-2 | Software securely updateable; a secure update mechanism (shall). The first-stage bootloader may stay immutable (Example 2). | OTA of the app; the bootloader is never updated over the air. |
| 5.3-3 | Simple for the user to apply (shall); a web interface on the device counts. | One sheet, one button. |
| 5.3-4A / 4B | One mechanism configurable to be automated; Note 6: "automated" includes *processing updates after the user's consent with minimal interaction*. Activate it at initialization after consent. | Automatic checking on, install on one tap. |
| 5.3-5 | Check for security updates after initialization. | Check at start, then daily. |
| 5.3-6A / 6B | User can enable/disable automatic updates and postpone; enable/disable notifications. | *Later*; Settings switches for checking and (if chosen) auto-install. |
| 5.3-7 | Best practice cryptography (shall). | ECDSA P-256 + SHA-256, TLS 1.2 with the CA bundle. |
| 5.3-8 | Security updates timely (shall). | Release process: security fixes released on their own, without waiting for features. |
| 5.3-9 / 5.3-10 | Verify authenticity and integrity; over a network via a trust relationship, e.g. a digital signature (shall). Do not tell the sender why an update failed; tell the owner. | Signed manifest, hash of the image; the failure shows on the dashboard, not to the server. |
| Example 7 | Anti-rollback by version check prevents downgrades. | Refuse older versions unless the person asks. |
| 5.3-11 | User told that a security update is needed and what risk it mitigates. | Manifest carries `security` and `risk`; the sheet shows them first. |
| 5.3-12 | Tell the user when an update disrupts the device, with the expected downtime. | The sheet says so before Update now. |
| 5.3-13 | Publish the support period (shall). | A line in README and SECURITY.md (decision below). |
| 5.3-16 | Model designation recognisable via the UI. | Settings already shows the version; add the board model. |

**EU Cyber Resilience Act, Regulation (EU) 2024/2847** (Official Journal text
via its mirror):

- Annex I Part I (2)(c): vulnerabilities addressable through security updates,
  "where applicable, through automatic security updates ... enabled as a
  default setting, with a clear and easy-to-use opt-out mechanism, through the
  notification of available updates to users, and the option to temporarily
  postpone them".
- Annex I Part II: an SBOM "covering at the very least the top-level
  dependencies"; security updates "where technically feasible ... provided
  separately from functionality updates"; mechanisms to distribute updates
  securely; updates "free of charge, accompanied by advisory messages".
- Article 13(8): support period "at least five years".
- Article 71: applies from **11 December 2027**; Article 14 (reporting actively
  exploited vulnerabilities) from 11 September 2026.
- Recital 18: free and open-source software is in scope only when "supplied ...
  in the course of a commercial activity". HID-Fi (PolyForm Noncommercial, not
  monetised) is most likely outside the CRA; following it anyway is the owner's
  choice. **Conflict:** the CRA's default is *automatic* installation of security
  updates with an opt-out; the brief says *never without consent*. ETSI accepts
  consent (Note 6). Decision below.

## Decisions (owner, 2026-10-28)

- **Layout:** reserve a second NVS and a spare partition now (above).
- **Installing:** checking and notifying are on by default; every install needs
  a tap on *Update now*. Settings has an off-by-default switch *Install security
  updates automatically*. This meets ETSI EN 303 645 (Note 6); it is not the
  CRA's default-on, which most likely does not apply to a non-commercial
  project - SECURITY.md says so openly.
- **Support period (ETSI 5.3-13):** publish nothing we cannot keep. Agreed: a
  dated, rolling statement renewed with each release - *"Security fixes are
  released for the latest version. This is committed until at least <release
  date + 12 months>; the date moves with every release, and an end of support
  will be announced here first."* - in README, SECURITY.md and the update sheet.
  `make_release.sh` writes the date; a test fails if it is in the past.
- **Build:** approved, "very carefully" - every step tested before the next.

## Design

**Layout** (decided 2026-10-28). `hid_fi/partitions.csv`, flashed once over USB:

| Name | Type | Offset | Size | Why |
|---|---|---|---|---|
| `nvs` | data/nvs | 0x9000 | 20 KB | unchanged - all of today's data |
| `otadata` | data/ota | 0xe000 | 8 KB | unchanged |
| `app0`, `app1` | ota_0, ota_1 | 0x10000, 0x650000 | 6.4 MB each | today's image is 1.56 MB |
| `nvs_ext` | data/nvs | 0xc90000 | 256 KB | reserved, empty: room to grow beyond the 630-entry `nvs` |
| `spare` | data/undefined | 0xcd0000 | 3.2 MB | reserved, empty |
| `coredump` | data/coredump | 0xff0000 | 64 KB | as in `default_16MB` |

The FQBN becomes `PartitionScheme=custom`; a
static test pins the table and fails if the image outgrows a slot. This table
can never change over the air afterwards - whatever we will want in ten years
has to be reserved now.

**Release side** (`tools/make_release.sh`, on the owner's Mac, never in CI):

- assets per release: `hidfi-ota.json` (manifest), `hid_fi-<ver>.bin` (the app
  image), `hidfi-ota.sig`, plus the existing zip and `SHA256SUMS`, and an SBOM
  generated from `tests/deps.json`;
- manifest: `version`, `size`, `sha256`, `min_from` (oldest version it can
  update), `core`, `idf`, `security` and `risk`, `changelog` (the CHANGELOG
  sections back to `min_from`, capped at 16 KB);
- signature: ECDSA P-256 over the manifest bytes, private key kept off GitHub
  and off the repo; a second, offline key is also trusted by the firmware, so a
  lost or leaked key can be replaced. The script refuses to release without the
  key and refuses a manifest whose `idf` NVS format differs from `min_from`'s.

**Board side** (a new `hid_fi/ota.h`, state machine, no blocking in `loop()`):

```
idle -> checking -> up_to_date | available | check_failed
available -> (Later) postponed | (Update now) downloading
downloading -> verifying | download_failed
verifying -> installing | verify_failed      (signature, sha256, size, chip, version, NVS format)
installing -> restarting -> pending_verify -> updated | rolled_back
```

- check: TLS GET of the manifest, signature checked with the built-in public
  keys **before** anything is shown; version compared; nothing else downloads;
- download: a background task pinned to core 0, low priority, streaming into the
  inactive slot with `esp_ota_begin(..., OTA_WITH_SEQUENTIAL_WRITES)` so flash is
  erased sector by sector; SHA-256 while streaming; WiFi drop -> retry with HTTP
  `Range` from the last byte, the running slot untouched;
- verify: `esp_ota_end()` (IDF's own image check), then
  `esp_partition_get_sha256()` re-read from flash equals the manifest; then the
  new image's app descriptor; only then `esp_ota_set_boot_partition()`;
- restart only at a safe moment: everything held is released first;
- after the restart: `verifyRollbackLater()` returns true; the self-test (setup
  finished, the loop ran 60 s without a reset, WiFi back as before the update,
  the web server answering, USB HID up if the host was awake before) marks the
  app valid. Any reset before that - crash, watchdog, power cut - makes the
  bootloader go back to the old slot. A failed check calls
  `esp_ota_mark_app_invalid_rollback_and_reboot()`;
- an NVS record (`ota` namespace: from, to, result) lets the dashboard say
  *Updated to 4.1.0* or *Went back to 4.0.4 because ...* after either restart;
- intentional reboots (the mouse mode switch) wait until the app is marked
  valid, or they would roll the update back;
- settings data gets a schema version; a new firmware only adds keys during
  `pending_verify`, so the old firmware still reads everything after a rollback;
- the test source and test key exist only in a test build (a USB-only
  `ota_source` command, like `net_fault`); a release gate fails if the test
  public key's bytes are found in a release image.

**Checkpoints and cancel, on the board.** Each step is recorded in NVS
(`ota` namespace: `step`, `from`, `to`, `sha`, `bytes`, `why`) before it starts
and after it ends, sparingly (no per-chunk NVS writes - flash wear), so the
dashboard and the Companion can always say where it is, and a restart resumes
or reverts from a known point:

| Step | What has changed on the board | Cancel does | A power cut here |
|---|---|---|---|
| checked | nothing | - | nothing to undo |
| downloading | the **inactive** slot only | `esp_ota_abort()`; nothing else changed | old version runs; the inactive slot is ignored |
| verified | same | same | same; on start the record says "download incomplete" |
| switched (boot slot set, not restarted) | `otadata` points at the new slot | sets the boot slot back to the running one | the new version starts in *pending verify*; the self-test decides |
| pending verify | running the new version, not yet trusted | rolls back (`esp_ota_mark_app_invalid_rollback_and_reboot`) | the bootloader rolls back by itself |
| valid | done | a downgrade is a new, explicit update | nothing to undo |

Cancel is refused only inside the single `otadata` write (8 KB, about 0.1 s),
and the button says so for that moment. Resuming a download re-hashes what is
already in the inactive slot (fast, from flash) and continues with HTTP
`Range`; if the server's image changed, it starts again.

**Dashboard.** Settings row, the update sheet, progress, the after-restart
message, the switches. Commands: `ota_status`, `ota_check`, `ota_install`,
`ota_later`, `ota_settings` (PIN-gated like every command, never LAN-safe).

**Hot path.** Flash erase pauses the caches on both cores. Pointer latency
during a download is measured with the perf tier before this ships, against
v4.0.4 on the same machine; if it stutters, writes are paced.

## The Companion

### What it shows, per board on this computer

Boards are found as today (USB `iSerial` = MAC, matched to `/api/status`), plus
CH343 COM ports, which are matched to a board only by reading the MAC through
the chip's ROM - never guessed (see *identity* below). For each board:

- firmware version, layout (`two_slot` or `single` - boards before the switch
  report no `ota` fields), update state and the last result;
- **compatibility**, from numbers rather than version strings: the board
  reports `comp_api` (the Companion protocol it speaks; boards that report none
  speak 1) and the Companion knows the range it supports. The signed release
  manifest carries the same matrix, so a new Companion can say what a board
  needs before anything is installed;
- one plain verdict: *Up to date*, *Board update available*, *Companion update
  available*, *Update the board first* (its protocol is too old for this
  Companion) or *Update the Companion first*, and the one button that fixes it.

### Three ways to update a board, chosen in this order

1. **Over the air by the board itself** (home WiFi with internet). The Companion
   only shows progress, from the board's `ota` status.
2. **Over the LAN through the Companion** (the board has no internet, the
   Companion has): the Companion fetches and verifies the release, then offers
   it to the board over its paired channel; the board downloads it from the
   Companion and verifies the **same signature** itself - the Companion is a
   courier, never trusted.
3. **Over the cable** - the only way for a board on the old single-slot layout
   (the one-time switch), a board whose firmware does not start, or a board on
   no network.

### Over the cable: the connections

| Plugged in | How the Companion reaches the ROM bootloader | Notes |
|---|---|---|
| COM (CH343) | DTR/RTS reset, as `flash_esp.sh` does | Preferred when present: independent of the HID port. |
| native USB only, firmware running | asks the board (paired, signed request) to restart into download mode; it comes back as USB-Serial-JTAG `303A:1001` | Needs a network path to the board; to be proven on hardware (reset back out of download mode: esptool's watchdog reset). |
| native USB only, no network or firmware broken | guides the person: hold BOOT, tap RESET, release BOOT | BOOT here only enters the ROM bootloader; it never erases anything (AGENTS: no button erases data). |
| both | COM | The keyboard and mouse on the native port disappear while flashing; the Companion says so before starting - on DA1A2C that is the Mac's keyboard. |

### Over the cable: the flasher is ours, small and closed

Decided 2026-10-28 after reading espflash 4.6.0 and esptool 5.6.0 at the source:

- espflash's library exposes `write_efuse` (eFuse programming would be linked
  into the Companion), and its ROM read (`read_flash_rom`) adds the running total
  to the offset on every pass - from the third 64-byte block on it reads the
  wrong address. esptool is Python and GPL-2.0, not something to ship inside the
  Companion.
- HID-Fi's flasher (a new crate, `hidfi-flash`) speaks the documented serial
  protocol with a **closed command list**: SYNC, READ_REG, SPI_ATTACH,
  SPI_SET_PARAMS, FLASH_BEGIN/DATA/END and their compressed forms,
  SPI_FLASH_MD5, READ_FLASH, CHANGE_BAUDRATE, MEM_BEGIN/DATA/END (only to load
  the stub), GET_SECURITY_INFO. WRITE_REG only to named reset/watchdog
  registers. ERASE_FLASH is not in the list. A test pushes every other command
  and register write through the client and expects a refusal before a byte
  leaves the computer - so burning an eFuse is impossible by construction, not
  by care.
- Reading flash on an ESP32-S3 needs the stub (its ROM has no read; esptool
  implements the slow ROM read only for the original ESP32): Espressif's
  `esp-flasher-stub` (Apache-2.0), `esp32s3.json` from v1.4.1 (2026-10-14),
  vendored with its licence and pinned by SHA-256.
- The same crate holds the simulated ESP32-S3 (NOR flash that only clears bits
  until erased, the loader protocol, the bootloader's slot choice with
  rollback, faults) that the fault sweep runs the real flasher against.

### Over the cable: never the wrong device

Before the first byte is written, all of these must hold, or nothing is
written and the board is reset back into its firmware:

- the chip says ESP32-S3 and 16 MB flash;
- the MAC read from the ROM equals the board the person chose (a second HID-Fi,
  or any other CH343 / Espressif device, never matches);
- the flash holds a HID-Fi image (the app descriptor at the running slot) - an
  ESP32-S3 running someone else's project is refused, with no override;
- the image to write is a signed HID-Fi release for this chip;
- a backup of 0x0-0x10000 (bootloader, table, NVS, `otadata`) has been read and
  saved, encrypted with a key kept in the OS keychain - it holds the vault.

### Over the cable: the journal (checkpoints)

The Companion writes a journal (one JSON file per board, written to a new file,
flushed, then renamed - never half-written) before and after each step. On a
two-slot board the cable update works **exactly like OTA**: it never overwrites
the running firmware.

| # | Step | Changes on the board | Cancel | Crash or unplug here -> on restart |
|---|---|---|---|---|
| C0 | planned: board, from, to, image hash, signature checked | nothing | drop the journal | nothing to resume |
| C1 | preflight: identity, chip, layout, `otadata`, backup saved | nothing | reset into the firmware | redo preflight |
| C2 | image written to the **inactive** slot, flash MD5 equals the image | inactive slot only | reset into the firmware | check the slot's MD5: rewrite or carry on |
| C3 | switch: new `otadata` entry written in the sector that is **not** the current winner, read back | boot choice | write the old choice back | read `otadata`: the switch either happened or did not |
| C4 | restart; new version starts *pending verify* | running new | ask the board to roll back | the board's own self-test or rollback decides |
| C5 | confirmed: the board reports *valid*; inventory hashes equal | done | (a downgrade is a new update) | finished |

**The one-time switch from the old layout** gets the same protection: the new
image goes to 0x650000 (outside every partition of the old table, so harmless
while the old table is active), then `otadata` (seq 2), then - the one risky
moment - the 3 KB partition table. The bootloader is **not** rewritten (phase 0
proved 3.3.1's has rollback; it is compared byte for byte, and written only if
missing). After the table lands, the board starts the new firmware in
`app1` *pending verify*, with the old firmware untouched in `app0` - so even
the switch rolls back by itself if the new firmware fails. If power is lost
during the table write, the ROM bootloader (in mask ROM, cannot be erased) is
still there: the journal says "table in progress" and the Companion writes it
again.

**Never open the COM port during *pending verify*.** On macOS every open resets
the board (AGENTS), and a reset before the self-test rolls the update back. The
Companion keeps the port it flashed through open, resets through it, and reads
the new firmware's status on the same handle - or watches over the network.

### Nothing can harm the hardware - and why

A firmware update cannot apply a wrong voltage to anything; what can do
lasting harm to an ESP32-S3 from software is limited and named:

- **eFuses** are one-time: secure boot, flash encryption, the flash voltage
  (`VDD_SPI`) and *disable download mode* can be burnt and never undone; a wrong
  flash voltage can make the flash unusable. **HID-Fi never touches eFuses**: the
  Companion links no eFuse-writing code, the flashers call no `espefuse`, and a
  static test fails if either appears.
- **Download mode cannot be lost** while those eFuses are untouched: it is in
  mask ROM. So any half-written state is recoverable over the cable - the reason
  the journal can always resume.
- **Reset storms** (rapid port opens) latch the ROM bootloader and look like a
  broken board (AGENTS): port opens are rate-limited, retries are bounded with
  back-off, and the Companion stops and explains instead of looping.
- **Power**: flashing adds well under 100 mA to a USB port rated 500 mA; a
  brown-out during a write only ever leaves the inactive slot or the one table
  sector incomplete, both covered above.
- **Flash wear**: an update erases each sector of one slot once; NVS records are
  written per step, not per chunk.

### The Companion updating itself

The release manifest also lists the Companion installers with their SHA-256,
under the same signature. First version: *Companion update available* with
what changed and the size; *Update* downloads, verifies the signature and the
hash, then hands over to the installer (`handover.rs` already lets a new
version replace a running one without the person quitting). Silent replacement
comes after the installers are code-signed (ROADMAP, ten-year upkeep).

## Tests - every state

| State / story | Model + BDD (`board_sim.py`, `settings.feature`) | Unit (host-compiled firmware code) | Real board (`tests/board/test_ota.py`) |
|---|---|---|---|
| up to date / available / checking | yes | version compare | against a local HTTPS server on the Mac |
| check failed: no internet, TLS, bad JSON, bad signature | yes | manifest parser, ECDSA vectors (good, flipped bit, wrong key) | each, served by the local server |
| postponed (Later), notifications off | yes | - | - |
| downloading, progress | yes | - | yes, with pointer latency measured |
| download failed: WiFi drop, server stops, resume | yes | Range resume logic | server cut mid-file |
| verify failed: hash, size, chip, older version, NVS format | yes | each | each, old version still running after |
| installing / restarting / pending_verify / updated | yes | - | install, restart, version reported |
| rolled back: crashes on start, never comes online | yes | - | a deliberately broken image goes back by itself |
| power cut mid-download / mid-self-test | yes | - | reset by esptool at each point |
| data kept: settings, PIN, vault, macros, tiles, pairings | yes | schema migration | inventory before/after each case, compared by hash |
| upgrade path: previous release -> new, then rollback | - | - | flash previous, fill, update, roll back |
| reboot request during pending_verify | yes | - | mouse mode switch while pending |

Each new guarantee gets a planted bug in `tests/unit/firmware/mutations.json`
(e.g. "marks valid before the self-test", "sets boot partition before the hash
check", "accepts an older version"). Release-side: unit tests for the manifest
builder, signer, the key-missing refusal and the NVS-format refusal.

### Every level

| Level | What | Where |
|---|---|---|
| Unit | version compare, manifest parse, ECDSA vectors (shared by firmware, Python and Rust - the three must agree byte for byte, as the pairing vectors do), journal transitions, `otadata` encode/decode, identity checks, advisory range matching | `tests/unit`, `companion/crates/*` |
| Regression | everything that exists today stays green; the WebServer multipart crash request is sent to a real board and the board must keep serving (it would crash v4.0.4) | the whole suite; `tests/board/test_web_hardening.py` |
| Feature | every dashboard and Companion state as a scenario a person would describe | `tests/ui/bdd/features/updates.feature`, Companion panel states |
| Product (model) | a **simulated ESP32-S3**: flash as bytes, the ROM loader protocol, the bootloader's slot choice and rollback, and faults injected at **every** step (unplug, power cut, bad write, wrong chip, wrong MAC, someone else's firmware). After every fault, two invariants: the flash still boots a valid HID-Fi, and NVS is byte-identical | `tests/support/esp_model.py` + the Companion's flasher run against it |
| End-to-end system | the real Companion, a real board, a local HTTPS release server, real Chrome on the dashboard: OTA, Companion-relayed, cable (COM; USB; both), cancel at each step, unplug at each step, upgrade from the previous release with data | `tests/board/test_ota.py`, `companion/tests/updates.py` |

The model-level fault sweep is what makes "every combination" countable: it
enumerates steps x faults rather than relying on someone to think of them,
like `tab_swipe.feature` does for surfaces.

## Phase 0 - the one-time switch, proven on a board

**Done on 09F7C8, 2026-10-28 - every step passed.** The board held real data:
an access PIN, a saved PC password, four vault entries, two Companion
pairings, a home network and a dashboard layout (258 of 630 NVS entries).

| Step | Result |
|---|---|
| Backup | The full 16 MB read failed twice on the CH343 (stream stopped at 921600 and 460800 baud). On `huge_app` all data lives in 0x0-0x10000, so that 64 KB was read at 115200 instead; its bootloader and table matched the v4.0.3 release byte for byte, proving the read. |
| Switch | v4.0.4 (HEAD `v4.0.4-2-g222600a`) built with `hid_fi/partitions.csv`, flashed with the usual four writes. |
| NVS before the first boot | 0x9000-0xdfff **byte-identical**; `otadata` unchanged; the table on the board parses as the new layout. |
| After boot | Inventory over serial (status, storage, PC list, vault list, macros, layout, pairings, networks - compared by hash, volatile fields removed): **identical**. Rejoined the home network with its stored password in 9-11 s; dashboard served (6 of 6 GETs, 371 KB in 0.65-0.88 s). |
| `app1` boots | Same image written to 0x650000, `otadata` entry seq 2 state NEW: the bootloader booted it and the core turned it **VALID** at start - proof that `initArduino()` marks valid before `setup()`, which HID-Fi must override. |
| Rollback | `app1` set to PENDING_VERIFY (a reset before the self-test): the bootloader marked it **ABORTED** and booted `app0`. Data identical afterwards. |
| Way back | The v4.0.3 release zip (old layout) flashed the usual way: NVS byte-identical before boot **and to the very first backup**; bootloader, table and `otadata` identical to the first backup; inventory identical; dashboard 200. |

The board is back on v4.0.3 exactly as found, except that 0x650000-0x7d0000
(outside every partition of the old layout, never read) still holds a copy of
the app. DA1A2C was not touched; its switch happens with the owner's word,
after the build steps below.

Lessons for the tooling: a large `read-flash` over the CH343 on macOS is not
reliable - back up in small regions; and `--before no-reset --after hard-reset`
did not start the app once - use the default `--before` for the reset.

The procedure, as it will run on DA1A2C:

1. Full 16 MB backup with `esptool read-flash` into a private temporary folder
   (it contains secrets; never in the repo; deleted when the owner says).
2. Inventory over serial: `status`, `storage_info`, macro and text-shortcut
   names, `pc_list`, vault count, pairings, layout - kept as hashes, never
   printed.
3. Build v4.0.4's code unchanged except `hid_fi/partitions.csv`; flash with the
   usual four partitions, `--after no-reset`.
4. While the board is still in the bootloader, read 0x9000-0xdfff back:
   byte-identical to the backup.
5. Read the partition table back from 0x8000 and parse it: the new layout.
6. Boot; inventory again: identical. USB devices present, dashboard serves.
7. Prove both slots boot: copy the same app into `app1`, select it in
   `otadata`, boot, `status` says it runs; then back.
8. Prove the way back: flash the old `huge_app` build; data still identical.
9. Restore the board as it was, verify, delete the backup when the owner agrees.

## Build order (each step: tests green, committed on `ota-updates`, nothing pushed)

0. [x] **Security: core 3.3.1 -> 3.3.12** (skill `hidfi-dependency-upgrade`): no-board
   suite, then the board tiers on 09F7C8 with the owner's OK, the multipart crash
   regression test, heap measured (3.3.12 adds ~16 KB static RAM).
   Progress 2026-10-28 (uncommitted until the board tier passes, as the skill says):
   - every OTA fact re-checked on 3.3.12 (IDF 5.5.5, rollback on, mbedTLS 3.6.6,
     bootloader handles PENDING_VERIFY, `verifyRollbackLater` unchanged); the
     multipart parser now refuses boundaries over 70 characters;
   - no-board suite on 3.3.12: all green except the size ratchet - static RAM
     71,564 -> 87,476 bytes, which is TinyUSB **host** buffers the core's new USB
     Host library links in (`_transfer_buf`, `ep_*_sw_buf`, `_hidh_epbuf`, MSC/CDC/
     MIDI host state), unused by HID-Fi and not removable without rebuilding the
     core's libraries;
   - `tests/board/test_web_hardening.py`: on 09F7C8 at v4.0.3 the board panicked
     (`Stack canary watchpoint triggered (loopTask)`) one second after a single
     request from the home network - all four tests fail there, twice; after
     flashing v4.0.4 built on 3.3.12 all four pass. The first version of the test
     passed against the crashing board (a socket timeout let the restarted board's
     uptime pass the old value) - it now requires uptime to advance by the real
     elapsed time and no boot event;
   - data on 09F7C8 identical after five crashes and the flash. NVS gained one
     entry: ESP-IDF's own radio calibration (`phy`/`cal_data`), rewritten after
     the panics - data comparisons leave the `phy` namespace out;
   - free internal heap 221.3 KB -> 202.6 KB at 5 s; 195.6 KB low point after
     60 s on the home network;
   - `companion/vectors/check_board.sh` moved to mbedTLS 3.6.6 (pairing vectors
     pass), with a test that it follows the pinned core;
   - DA1A2C (owner's go-ahead, data compared by hash only): flashed, data
     identical, web hardening 4/4, board tier 93 passed / 0 failed (19 skipped:
     tests that refuse a board in real use, and opt-in perf). The one failure on
     the first run was a race in the test (state read at 8.0 s, changed at 8.4 s
     on 3.3.1 and 3.3.12 alike - measured by flashing the v4.0.4 release back);
   - pinned 3.3.12, size golden moved (the only golden that changed),
     `tools/dep_watch.py`: "OK - everything checked, nothing open";
   - not yet: a release (owner's decision), Windows/Linux CI on the new core,
     hostsim (Linux), e2e (needs the owner at the Mac).
1. [x] `tools/dep_watch.py`: pinned vs latest, and advisories against the pinned
   versions; agent rules (AGENTS.md, CLAUDE.md, the upgrade skill) say to run it
   and to check the web for the latest before relying on any outside fact.
   Done 2026-10-28: 38 unit tests, each planted bug caught; the first real run
   left exactly the two advisories that reach HID-Fi open (GHSA-8cmm, GHSA-g764),
   34 others acknowledged with evidence in `tools/dep_watch_ack.json`.
2. [ ] Layout in the repo (`hid_fi/partitions.csv`, FQBN `custom`), both flashers,
   FLASH.md, the release zip; static tests pin the table and the slot size.
3. [ ] Rollback core in firmware: `verifyRollbackLater`, self-test, the `ota`
   record, status fields (`ota_state`, `layout`, `running_slot`, `comp_api`),
   reboot deferral; model + contract + docs; proven on 09F7C8 by writing a test
   image into the other slot over the cable.
4. [ ] `tests/support/esp_model.py`: the simulated ESP32-S3 and the fault sweep.
5. [ ] Manifest + signature + version rules: shared vectors; firmware, Python,
   Rust.
6. [ ] Board OTA: download task, checkpoints, cancel, resume; local HTTPS server
   tests; pointer latency during download.
7. [ ] Dashboard: the update sheet and every state (BDD, responsive matrix).
8. [ ] Companion: inventory and compatibility view; LAN relay; cable path with
   the journal (COM first, then native USB); self-update notice.
9. [ ] Release tooling: signing, manifest, SBOM, support-period date;
   CHANGELOG, SECURITY.md, README.
10. [ ] Upgrade path from v4.0.4 with data, on 09F7C8, then DA1A2C with the
   owner's word.
