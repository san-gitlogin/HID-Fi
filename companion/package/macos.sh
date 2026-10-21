#!/usr/bin/env bash
# Builds "HID-Fi Companion.app" and a .dmg to drag it from, for this Mac's
# architecture (or both with --universal).
#
# Without a Developer ID the app is signed ad hoc: it runs here, and macOS keeps
# its Screen Recording permission across rebuilds of the same bundle id, but
# Gatekeeper warns on other Macs. With one, pass --sign "Developer ID
# Application: Name (TEAMID)" and --notarize <keychain-profile> (xcrun notarytool
# store-credentials) for a download that opens without warnings.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/.." && pwd)"
sign="-"; notarize=""; universal=0
while [ $# -gt 0 ]; do
  case "$1" in
    --sign) sign="$2"; shift 2;;
    --notarize) notarize="$2"; shift 2;;
    --universal) universal=1; shift;;
    *) echo "unknown option $1" >&2; exit 2;;
  esac
done
ver="$(grep -m1 '^version' "$root/Cargo.toml" | cut -d'"' -f2)"
out="$root/target/package"
app="$out/HID-Fi Companion.app"
rm -rf "$app"; mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"

cd "$root"
if [ "$universal" = 1 ]; then
  rustup target add aarch64-apple-darwin x86_64-apple-darwin >/dev/null
  cargo build --release -p hidfi-companion --target aarch64-apple-darwin
  cargo build --release -p hidfi-companion --target x86_64-apple-darwin
  lipo -create -output "$app/Contents/MacOS/hidfi-companion" \
    target/aarch64-apple-darwin/release/hidfi-companion target/x86_64-apple-darwin/release/hidfi-companion
else
  cargo build --release -p hidfi-companion
  cp target/release/hidfi-companion "$app/Contents/MacOS/hidfi-companion"
fi

cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>HID-Fi Companion</string>
  <key>CFBundleDisplayName</key><string>HID-Fi Companion</string>
  <key>CFBundleIdentifier</key><string>in.hidfi.companion</string>
  <key>CFBundleExecutable</key><string>hidfi-companion</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$ver</string>
  <key>CFBundleVersion</key><string>$ver</string>
  <key>LSMinimumSystemVersion</key><string>12.3</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSHumanReadableCopyright</key><string>PolyForm Noncommercial 1.0.0</string>
  <key>NSLocalNetworkUsageDescription</key><string>HID-Fi Companion finds your HID-Fi board on your home network and sends this screen to your phone over it.</string>
  <key>NSBonjourServices</key><array><string>_hidfi._tcp</string></array>
</dict>
</plist>
PLIST

# App icon: the HID-Fi mark on its tile, every size macOS asks for.
iconset="$out/AppIcon.iconset"; rm -rf "$iconset"; mkdir -p "$iconset"
src="$here/icon-1024.png"
for s in 16 32 128 256 512; do
  sips -z $s $s "$src" --out "$iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) "$src" --out "$iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$iconset" -o "$app/Contents/Resources/AppIcon.icns"
rm -rf "$iconset"

# The launch agent restarts a crashed app from this path, so it must be the
# installed copy; the app writes it on first run from wherever it runs.
if [ "$sign" = "-" ]; then
  codesign --force --sign - --timestamp=none "$app"
else
  codesign --force --options runtime --timestamp --sign "$sign" "$app"
fi
codesign --verify --strict "$app"

dmg="$out/HID-Fi-Companion-$ver-macos.dmg"
stage="$out/dmg"; rm -rf "$stage" "$dmg"; mkdir -p "$stage"
cp -R "$app" "$stage/"
ln -s /Applications "$stage/Applications"
hdiutil create -quiet -volname "HID-Fi Companion" -srcfolder "$stage" -ov -format UDZO "$dmg"
rm -rf "$stage"
if [ -n "$notarize" ]; then
  [ "$sign" != "-" ] || { echo "notarizing needs --sign" >&2; exit 2; }
  codesign --force --sign "$sign" --timestamp "$dmg"
  xcrun notarytool submit "$dmg" --keychain-profile "$notarize" --wait
  xcrun stapler staple "$dmg"
fi
echo "$app"
echo "$dmg"
