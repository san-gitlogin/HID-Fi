#!/usr/bin/env bash
# Build HID-Fi Companion for Windows on a Mac (no Windows needed): Rust's own
# linker plus Microsoft's C runtime and SDK libraries, fetched once by xwin.
#
#   cargo install --locked xwin
#   xwin --accept-license --arch x86_64,aarch64 splat --output "$HIDFI_WIN_XWIN"
#   companion/tests/windows/build.sh [x64|arm64|both]
#
# HIDFI_WIN_XWIN        the xwin splat (default: $HIDFI_WIN_VM_HOME/xwin)
# HIDFI_WIN_TARGET_DIR  build output (default: /Volumes/HIDFi-WinBuild/target).
#   Not on exFAT: the linker writes through memory-mapped files, and on an
#   exFAT drive some .exe files came out as zeros ("corrupted and unreadable"
#   on Windows). An APFS disk image on that drive works.
# Every .exe is checked: a real PE file, and no Visual C++ runtime DLL
# (a clean Windows has none - see companion/.cargo/config.toml).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
X="${HIDFI_WIN_XWIN:-${HIDFI_WIN_VM_HOME:?set HIDFI_WIN_XWIN or HIDFI_WIN_VM_HOME}/xwin}"
T="${HIDFI_WIN_TARGET_DIR:-/Volumes/HIDFi-WinBuild/target}"
[ -d "$X/crt" ] || { echo "build.sh: no xwin splat in $X" >&2; exit 1; }
mkdir -p "$T"
if [ "$(stat -f %T "$T" 2>/dev/null)" = "exfat" ] || mount | grep -F " on $(df "$T" | tail -1 | awk '{print $NF}') " | grep -q exfat; then
  echo "build.sh: $T is on exFAT - the linker's output can come out as zeros there" >&2; exit 1
fi
case "${1:-both}" in x64) archs=(x86_64) ;; arm64) archs=(aarch64) ;; both) archs=(x86_64 aarch64) ;; *) echo "build.sh x64|arm64|both" >&2; exit 2 ;; esac
cd "$here/../.."
for a in "${archs[@]}"; do
  tgt="$a-pc-windows-msvc"; up="$(echo "$tgt" | tr 'a-z-' 'A-Z_')"
  # CARGO_ENCODED_RUSTFLAGS replaces .cargo/config.toml's flags: repeat +crt-static.
  env "CARGO_TARGET_${up}_LINKER=rust-lld" \
      CARGO_ENCODED_RUSTFLAGS="$(printf -- '-Ctarget-feature=+crt-static\x1f-Lnative=%s\x1f-Lnative=%s\x1f-Lnative=%s' \
        "$X/crt/lib/$a" "$X/sdk/lib/um/$a" "$X/sdk/lib/ucrt/$a")" \
      CARGO_TARGET_DIR="$T" \
      cargo build --release --target "$tgt" -p hidfi-companion -p hidfi-stream --bins --examples
  python3 - "$T/$tgt/release" <<'EOF'
import pathlib, re, struct, sys
rel = pathlib.Path(sys.argv[1]); bad = []
for exe in [rel / "hidfi-companion.exe", *sorted((rel / "examples").glob("*.exe"))]:
    d = exe.read_bytes()
    pe = struct.unpack_from("<I", d, 0x3c)[0] if len(d) > 0x40 else 0
    ok = d[:2] == b"MZ" and d[pe:pe + 4] == b"PE\0\0"
    crt = sorted({m.decode().lower() for m in re.findall(rb"(?i)(?:vcruntime140[_0-9]*|msvcp140[_0-9]*|api-ms-win-crt-[a-z]+-l1-1-0)\.dll", d)})
    print(f"  {exe.name}: {len(d)} bytes, {'PE ok' if ok else 'NOT A PE FILE'}, {'C runtime DLLs ' + str(crt) if crt else 'no C runtime DLLs'}")
    if not ok or crt:
        bad.append(exe.name)
sys.exit(f"build.sh: bad output: {bad}" if bad else 0)
EOF
done
