#!/usr/bin/env bash
# Compiles the board's pairing crypto (hid_fi/companion_crypto.h) on this
# computer against the same mbedTLS release the ESP32 core ships (3.6.4), and
# checks it against pairing_v1.json - the vectors the Rust app is held to.
#
# The mbedTLS source is downloaded once into companion/target/ (gitignored),
# checked against the SHA-256 Mbed-TLS publishes for that release.
# Needs: curl, a C/C++ compiler (cc/c++ or clang). No install, no sudo.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
ver=3.6.4
sha=ec35b18a6c593cf98c3e30db8b98ff93e8940a8c4e690e66b41dfc011d678110
work="$repo/companion/target/mbedtls-check"
src="$work/mbedtls-$ver"
mkdir -p "$work/o"

if [ ! -d "$src" ]; then
  tar="$work/mbedtls-$ver.tar.bz2"
  curl -fsSL -o "$tar" "https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-$ver/mbedtls-$ver.tar.bz2"
  got="$( (command -v sha256sum >/dev/null && sha256sum "$tar" || shasum -a 256 "$tar") | cut -d' ' -f1)"
  if [ "$got" != "$sha" ]; then echo "mbedTLS archive checksum mismatch: $got" >&2; rm -f "$tar"; exit 2; fi
  tar -xjf "$tar" -C "$work"
fi

CC="${CC:-cc}"; CXX="${CXX:-c++}"
lib="$src/library"
for f in ecp ecp_curves ecp_curves_new bignum bignum_core bignum_mod bignum_mod_raw constant_time \
         platform_util platform sha256 sha1 sha512 sha3 md5 ripemd160 md hkdf; do
  [ -f "$work/o/$f.o" ] || "$CC" -O1 -w -I"$src/include" -I"$lib" -c "$lib/$f.c" -o "$work/o/$f.o"
done
"$CC" -O1 -w -c "$here/psa_stub.c" -o "$work/o/psa_stub.o"
"$CXX" -std=c++17 -O1 -I"$src/include" -I"$repo/hid_fi" "$here/board_check.cpp" "$work"/o/*.o -o "$work/board_check"
"$work/board_check" "$here/pairing_v1.json"
