---
name: hidfi-dependency-upgrade
description: Move HID-Fi to a new version of the ESP32 Arduino core, ArduinoJson, WebSockets or arduino-cli, or investigate a red "latest-dependencies" CI run. Use when asked to upgrade, update or bump a library/core/toolchain, when doctor reports a version difference, or when a build breaks after an upstream release.
---

# Upgrading a HID-Fi dependency

Firmware dependencies are exact pins in `tests/deps.json`, because they are
compiled into the product: a different version is a different product until the
suite says otherwise. Background: `docs/TESTING.md#upgrading-a-dependency`.

## Where HID-Fi depends on internals

| Dependency | Relied on | Guard |
|---|---|---|
| ESP32 core `USBHIDKeyboard` | `press()` treats bytes >= 0x88 as raw usages, 0x80-0x87 as modifiers; `KEY_*` values; en_US table | unit: canary + golden ASCII traces + typing bench (compiles the installed core's own source) |
| ESP32 core TinyUSB | firmware defines `tud_descriptor_string_cb()` for OS detection | board: `test_host_os`; perf: detection time |
| ESP32 core `USBHIDMouse` | only one mouse object may exist (descriptor registered in the constructor) | board: pointer mode checks |
| Build options | `USBMode=default`, `CDCOnBoot=default` in the FQBN | static: FQBN agreement |
| ArduinoJson 7 | deprecated `StaticJsonDocument`, `DynamicJsonDocument`, `createNested*` | build: warnings ratchet. A major release will remove them - migrate to `JsonDocument`, `doc[k].to<JsonArray>()`, `add<JsonObject>()` first, as its own change |
| WebSockets | binary frames, `sendBIN`, `broadcastTXT`, client IPs | build; perf WiFi tests |

## Procedure

1. Record the current state: `python tests/run.py doctor`,
   `python tools/dep_watch.py` (pinned vs latest, and every advisory against a
   pinned version), and a green `python tests/run.py` on the current pins.
   Read the candidate's release notes and advisories **at the source** (GitHub
   release feed, advisory database) for every row of the table above, plus
   WebServer, TLS, Update/OTA, NVS and the DHCP server - and date what you found.
2. Install the candidate (`arduino-cli core install esp32:esp32@X.Y.Z`, or
   `arduino-cli lib install Name@X.Y.Z`). Note the disk cost of a core (~1 GB)
   if the user's machine is short of space.
3. `python tests/run.py` - **do not** pass `--update-golden`.
   - unit canary failing: the core changed a behaviour the typing code depends
     on. Read the new `USBHIDKeyboard.cpp` before touching anything.
   - golden typing traces differing: plain typing would change for every user.
     Find out why from the core diff; this is a regression until proven otherwise.
   - build failing or new warnings: read them; fix the code, not the ratchet.
   - size grew past budget: find what grew.
4. If code changes are needed, make them as a separate, explained change.
5. Ask the user to flash a board and run `python tests/run.py --all` on at least
   one OS (USB enumeration and OS detection only show on hardware). Ask before
   flashing - their settings survive a normal flash, but it is their board.
6. Only then: update `tests/deps.json` (pins and the `verified` block), update
   golden files whose change is understood (`--update-golden`, then review
   `git diff tests/golden/`), and add the version to the CHANGELOG entry.

## A red latest-dependencies CI run

That job installs the newest core and libraries every Monday and runs the
no-board tiers without strict pins. Read its uploaded `latest-deps-reports`
artifact: the failing tier tells you which row of the table above to look at.
It is an early warning, not a broken build - the pinned job is what gates PRs.
