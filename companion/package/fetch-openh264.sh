#!/bin/sh
# Downloads Cisco's OpenH264 (the H.264 encoder HID-Fi Companion uses on Linux)
# from Cisco, as Cisco's licence requires, into DIR (default:
# ~/.local/share/hidfi-companion). Cisco serves it over plain http, so the file
# is checked against its known SHA-256 and refused if it differs; the app checks
# it again before loading it.
set -eu
dir="${1:-${XDG_DATA_HOME:-$HOME/.local/share}/hidfi-companion}"
case "$(uname -m)" in
  x86_64|amd64) lib=libopenh264-2.6.0-linux64.8.so;     sha=2f0cde7c6a6abcf5cae76942894ea42897fa677bce4ed6c91a24dd1b041d5f04 ;;
  aarch64|arm64) lib=libopenh264-2.6.0-linux-arm64.8.so; sha=12e7b33623667cdab0e575170c147b1b36eadb77d0d2aa7ceb5afd3e58902140 ;;
  *) echo "OpenH264: no Cisco build for $(uname -m)" >&2; exit 2 ;;
esac
mkdir -p "$dir"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL --retry 3 -o "$tmp/$lib.bz2" "http://ciscobinary.openh264.org/$lib.bz2"
bunzip2 "$tmp/$lib.bz2"
if command -v sha256sum >/dev/null; then got="$(sha256sum "$tmp/$lib" | cut -d' ' -f1)"; else got="$(shasum -a 256 "$tmp/$lib" | cut -d' ' -f1)"; fi
if [ "$got" != "$sha" ]; then
  echo "OpenH264: checksum mismatch ($got) - not installed" >&2
  exit 3
fi
install -m 0644 "$tmp/$lib" "$dir/$lib"
echo "$dir/$lib"
