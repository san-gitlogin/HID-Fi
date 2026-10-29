#!/usr/bin/env bash
# Signs the Companion's macOS app in a release disk image with HID-Fi's own
# certificate (RELEASING.md), in place:
#
#   tools/sign_mac_release.sh dist/companion-vX.Y/HID-Fi-Companion-X.Y.Z-macos.dmg
#
# The companion workflow builds the app ad hoc: macOS then knows it only by its
# code hash, and every update looks like a new app - Screen Recording and System
# Audio Recording asked again. Signed with one certificate, the app is
# "in.hidfi.companion, certificate root <this one>" in every version, and macOS
# keeps what the person allowed. The certificate lives with the release keys
# (~/.hidfi-release, HIDFI_RELEASE_KEY_DIR): its own keychain, its password file,
# a .p12 backup - never in the repo. Its SHA-1 is recorded in
# tools/release_keys.json ("macos_signing"); a different certificate is refused.
#   tools/sign_mac_release.sh --check <dmg>   only says whether it is signed so
#                                               (make_release.sh refuses one that is not)
set -euo pipefail
check=0; [ "${1:-}" = "--check" ] && { check=1; shift; }
dmg="${1:?usage: tools/sign_mac_release.sh [--check] <the release .dmg>}"
[ -f "$dmg" ] || { echo "no such disk image: $dmg"; exit 2; }
root="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$check" = 1 ]; then
  want="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['macos_signing']['sha1'].lower())" "$root/tools/release_keys.json")"
  m="$(mktemp -d)"; hdiutil attach -quiet -nobrowse -readonly -mountpoint "$m" "$dmg"
  dr="$(codesign -d -r- "$(ls -d "$m"/*.app)" 2>&1 | sed -n 's/^designated => //p')"; hdiutil detach -quiet "$m"
  [ "$dr" = "identifier \"in.hidfi.companion\" and certificate root = H\"$want\"" ] \
    && { echo "signed with HID-Fi's certificate: $dmg"; exit 0; } \
    || { echo "NOT signed with HID-Fi's certificate ($dr): tools/sign_mac_release.sh $dmg"; exit 1; }
fi
dir="${HIDFI_RELEASE_KEY_DIR:-$HOME/.hidfi-release}"
kc="$dir/hidfi-macos-signing.keychain-db"
[ -f "$kc" ] || { echo "no signing keychain at $kc - is the release drive/folder here?"; exit 1; }
want="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['macos_signing']['sha1'].upper())" "$root/tools/release_keys.json")"
security unlock-keychain -p "$(cat "$dir/hidfi-macos-signing.pw")" "$kc"
have="$(security find-identity -p codesigning "$kc" | sed -n 's/^ *1) \([0-9A-F]*\) .*/\1/p')"
[ "$have" = "$want" ] || { echo "the keychain's certificate ($have) is not the recorded one ($want) - refusing"; exit 1; }
# codesign finds an identity only in a keychain on the search list: add ours for
# the signing, and put the person's list back however this ends.
before=()
while IFS= read -r k; do before+=("$(echo "$k" | tr -d '" ')"); done < <(security list-keychains -d user)
restore() { security list-keychains -d user -s "${before[@]}"; }
trap restore EXIT
security list-keychains -d user -s "${before[@]}" "$kc"
work="$(mktemp -d)"; mnt="$work/mnt"; stage="$work/stage"; mkdir -p "$mnt" "$stage"
hdiutil attach -quiet -nobrowse -readonly -mountpoint "$mnt" "$dmg"
app="$(ls -d "$mnt"/*.app)"; name="$(basename "$app")"
ditto "$app" "$stage/$name"
hdiutil detach -quiet "$mnt"
codesign --force --keychain "$kc" --sign "$want" --timestamp=none "$stage/$name"
dr="$(codesign -d -r- "$stage/$name" 2>&1 | sed -n 's/^designated => //p')"
lower="$(echo "$want" | tr 'A-F' 'a-f')"
[ "$dr" = "identifier \"in.hidfi.companion\" and certificate root = H\"$lower\"" ] \
  || { echo "unexpected designated requirement: $dr"; exit 1; }
ln -s /Applications "$stage/Applications"
hdiutil create -quiet -volname "HID-Fi Companion" -srcfolder "$stage" -ov -format UDZO "$work/out.dmg"
mv -f "$work/out.dmg" "$dmg"
rm -rf "$work"
echo "signed: $dmg"
echo "  $dr"
