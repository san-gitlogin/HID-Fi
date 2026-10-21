# Releasing HID-Fi

Every release is a version number, a tag, a changelog entry and a GitHub
Release - all four saying the same thing. A test fails if they disagree.

## Version numbers

| Part | Example | Changes when |
|---|---|---|
| `vMAJOR.MINOR` | `v3.11` | anything the board does differently: firmware, dashboard, commands |
| `vMAJOR.MINOR.PATCH` | `v3.11.1` | a fix to a released version that people should take; firmware reports the new number |

The firmware reports its version in `status` as `firmware` (`hid_fi_v3.11`), set
by `FW_VERSION` in `hid_fi/hid_fi.ino`.

**Published tags never move.** A tag that reached GitHub is permanent, even if a
fix lands a minute later: that fix is the next version.

## Checklist

| # | Step | Command |
|---|---|---|
| 1 | Version: `FW_VERSION` and the top `## vX.Y - date` in CHANGELOG.md agree | `python tests/run.py --tier static` checks it |
| 2 | Every no-board test | `python tests/run.py` |
| 3 | The Linux tier | `tests/docker/run.sh` (or natively on Linux / WSL) |
| 4 | Flash a board and run the hardware tiers | `./flash_esp.sh -c`, then `python tests/run.py --all` (ask the owner: it types and moves the mouse) |
| 5 | Commit, then tag the commit that passed | `git tag -a vX.Y -m "HID-Fi vX.Y: <one line>"` |
| 6 | Push the branch and the tag | `git push origin main vX.Y` |
| 7 | The firmware zip | `tools/make_release.sh vX.Y` - the four flash files, `FLASH.md`, `SHA256SUMS`, the license |
| 8 | The GitHub Release | `gh release create vX.Y --verify-tag --latest --notes-file <changelog section> <zip>` |
| 9 | Check it | download the zip from the release page and `shasum -a 256 -c SHA256SUMS` |

### HID-Fi Companion (from v4.0)

The app ships in the same GitHub Release, so the installers' "latest" links find
it. Run the manual `companion` workflow on the release commit; its artifacts are
the files the installers expect:

| File | From |
|---|---|
| `HID-Fi-Companion-<ver>-macos.dmg` | macos-14 (Developer ID signed and notarized when the secrets exist; otherwise ad hoc) |
| `hidfi-companion-windows-x86_64.exe` | windows-2022 |
| `hidfi-companion-linux-x86_64`, `hidfi-companion-linux-aarch64` | ubuntu-24.04, ubuntu-24.04-arm |

Put them in the release with the firmware zip and **one** `SHA256SUMS` covering
all of them - `install-windows.ps1` and `install-linux.sh` refuse a download whose
line in it is missing or wrong:

```bash
gh run download <run-id> -D dist && cd dist && cp */dist/* . 2>/dev/null
cp ../hidfi-vX.Y-firmware.zip . && shasum -a 256 HID-Fi-Companion-* hidfi-companion-* hidfi-*.zip > SHA256SUMS
gh release upload vX.Y HID-Fi-Companion-* hidfi-companion-* SHA256SUMS --clobber
```

The GitHub test workflow is manual (Actions > tests > Run workflow); run it by
hand if a release needs a clean Windows or Linux machine you do not have.

## Releases so far

`v3.5`, `v3.6`, `v3.8`, `v3.9`, `v3.10` - each with its changelog notes and a
source zip; `v3.10` onwards also with the firmware zip.

`v4.0` (2026-10-22): the firmware zip and HID-Fi Companion for macOS, Windows and
Linux (x86-64, ARM64), with one `SHA256SUMS` for all of them.
