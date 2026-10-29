#!/usr/bin/env bash
# Build the firmware zip for a GitHub Release (RELEASING.md, step 7).
#
#   tools/make_release.sh v3.11            -> dist/HID-Fi-v3.11-firmware.zip
#
# Refuses unless the checkout is exactly at that tag with no local changes, and
# the last build is of that firmware - so the zip is the release, nothing else.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${1:-}"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+(\.[0-9]+)?$ ]] || { echo "usage: tools/make_release.sh vX.Y"; exit 2; }

[ "$(git rev-parse HEAD)" = "$(git rev-parse "refs/tags/$TAG^{commit}" 2>/dev/null)" ] \
    || { echo "HEAD is not at tag $TAG - check it out first (git checkout $TAG)"; exit 1; }
[ -z "$(git status --porcelain -- hid_fi)" ] || { echo "hid_fi/ has local changes - the zip would not be $TAG"; exit 1; }

BUILD=hid_fi/build
grep -q "hid_fi_$TAG" "$BUILD/hid_fi.ino.bin" 2>/dev/null \
    || { echo "the last build is not $TAG - run ./flash_esp.sh -c (or arduino-cli compile) first"; exit 1; }
CORE=$(python3 -c "import json;print(json.load(open('tests/deps.json'))['firmware']['esp32:esp32'])")
# The Arduino data folder is ~/Library/Arduino15 on macOS, ~/.arduino15 on Linux;
# find fails on the one that does not exist, which must not end the script.
BOOT_APP0=$( (find "$HOME/Library/Arduino15" "$HOME/.arduino15" ${ARDUINO15:+"$ARDUINO15"} \
    -path "*esp32/$CORE/tools/partitions/boot_app0.bin" 2>/dev/null || true) | head -1)
[ -n "$BOOT_APP0" ] || { echo "boot_app0.bin for ESP32 core $CORE not found"; exit 1; }

OUT=dist/HID-Fi-$TAG-firmware
rm -rf "$OUT" "$OUT.zip" && mkdir -p "$OUT"
cp "$BUILD/hid_fi.ino.bootloader.bin" "$OUT/bootloader.bin"
cp "$BUILD/hid_fi.ino.partitions.bin" "$OUT/partitions.bin"
cp "$BOOT_APP0" "$OUT/boot_app0.bin"
cp "$BUILD/hid_fi.ino.bin" "$OUT/hid_fi.bin"
cp LICENSE.md "$OUT/"
sed "s/{TAG}/$TAG/g; s/{CORE}/$CORE/g" tools/release_FLASH.md > "$OUT/FLASH.md"
(cd "$OUT" && shasum -a 256 *.bin > SHA256SUMS)
python3 tools/sbom.py firmware "${TAG#v}" > "$OUT/sbom-firmware.cdx.json"
(cd dist && zip -qr "HID-Fi-$TAG-firmware.zip" "HID-Fi-$TAG-firmware")
echo "dist/HID-Fi-$TAG-firmware.zip"
(cd "$OUT" && cat SHA256SUMS)

# Over the air (docs/OTA.md step 9): the signed manifest, its signature, the image
# and the partition table, as release assets at the root of the release - the
# board and the Companion read them from releases/latest/download/. Signed only
# with HIDFI_RELEASE_KEY (a file outside the repo, mode 600); every gate in
# tools/ota_release.py release_gate must pass or nothing is written.
if [ -n "${HIDFI_RELEASE_KEY:-}" ]; then
    MIN_FROM="${HIDFI_MIN_FROM:?set HIDFI_MIN_FROM: the oldest version this release may update from}"
    IDF=$(python3 -c "import json;print(json.load(open('tools/release_history.json'))['cores'].get('$CORE',''))")
    [ -n "$IDF" ] || { echo "the IDF of core $CORE is not on record (tools/release_history.json)"; exit 1; }
    OTA=dist/HID-Fi-$TAG-ota
    rm -rf "$OTA"
    python3 tools/ota_release.py release --version "${TAG#v}" --image "$BUILD/hid_fi.ino.bin" \
        --table "$BUILD/hid_fi.ino.partitions.bin" --min-from "$MIN_FROM" --core "$CORE" --idf "$IDF" \
        --key "$HIDFI_RELEASE_KEY" --out-dir "$OTA" \
        ${HIDFI_SECURITY_RISK:+--security --risk "$HIDFI_SECURITY_RISK"} \
        ${HIDFI_COMPANION_DIR:+--companion-dir "$HIDFI_COMPANION_DIR" --companion-version "$(sed -n 's/^version = "\(.*\)"/\1/p' companion/Cargo.toml | head -1)"}
    python3 tools/sbom.py companion "${TAG#v}" > "$OTA/sbom-companion.cdx.json"
    cp "$OUT/sbom-firmware.cdx.json" "$OTA/"
    echo "$OTA: upload every file in it as a release asset"
else
    echo "(no HIDFI_RELEASE_KEY: no over-the-air files - this release is for the cable only)"
fi
