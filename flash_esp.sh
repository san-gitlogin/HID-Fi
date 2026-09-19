#!/usr/bin/env bash
# ============================================================================
#  flash_esp.sh - build, flash and verify hid_fi on macOS and Linux
#
#  The macOS/Linux twin of flash_esp.ps1. Same guarantee: it writes only the
#  four firmware partitions and never merged.bin, so NVS - your WiFi
#  credentials, access PIN, macros, saved PC passwords - survives a firmware
#  upgrade. Only --erase clears it.
#
#  Usage:
#     ./flash_esp.sh                 find the board, flash it, verify it
#     ./flash_esp.sh -c              rebuild from source first
#     ./flash_esp.sh -p /dev/cu.wchusbserial123   skip auto-detection
#     ./flash_esp.sh -e              wipe the chip completely, then flash
#     ./flash_esp.sh -e -f           the same, without the confirmation prompt
#     ./flash_esp.sh -n              skip the serial check at the end
#
#  Exit codes:  0 ok | 1 flash failed | 2 compile failed | 3 no image |
#               4 no board found | 5 esptool missing | 10 several boards, pick one
#
#  Needs arduino-cli and the esp32 core (see docs/FLASHING.md). esptool ships
#  inside the core, so nothing else is required to flash.
# ============================================================================
set -euo pipefail

FQBN='esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=default,PSRAM=opi,FlashSize=16M,UploadSpeed=921600,PartitionScheme=huge_app'
HERE="$(cd "$(dirname "$0")" && pwd)"
SKETCH="$HERE/hid_fi"
BUILD="$SKETCH/build"        # same as flash_esp.ps1, and covered by .gitignore

COMPILE=0 ERASE=0 FORCE=0 VERIFY=1 PORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    -c|--compile)  COMPILE=1 ;;
    -e|--erase)    ERASE=1 ;;
    -f|--force)    FORCE=1 ;;
    -n|--no-verify) VERIFY=0 ;;
    -p|--port)     PORT="${2:-}"; shift ;;
    -h|--help)     sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

say()  { printf '   %s\n' "$*"; }
step() { printf '== %s\n' "$*"; }
bad()  { printf '!! %s\n' "$*" >&2; }

# ---- find the CH343 serial (COM) port ------------------------------------
# The board flashes through its CH343 bridge. Its /dev name depends on the driver:
# cu.wchusbserial* with WCH's own driver, cu.usbserial* with some, and
# cu.usbmodem* with macOS's built-in CDC-ACM driver (no WCH driver installed).
# Linux shows ttyUSB* / ttyACM*. The native USB port is a HID device and does not
# appear here while the firmware runs with USB CDC off, so there is no clash.
find_ports() {
  ls /dev/cu.wchusbserial* /dev/cu.usbserial* /dev/cu.usbmodem* \
     /dev/ttyUSB* /dev/ttyACM* 2>/dev/null || true
}
if [ -z "$PORT" ]; then
  step "Finding the board"
  FOUND=()                              # read loop, not mapfile: macOS ships bash 3.2
  while IFS= read -r line; do [ -n "$line" ] && FOUND+=("$line"); done < <(find_ports)
  if [ "${#FOUND[@]}" -eq 0 ]; then
    bad "No ESP32-S3 board detected. Plug the cable into the COM port, check it is a data cable, and install the CH343 driver (wch-ic.com) if macOS shows no serial device."
    exit 4
  elif [ "${#FOUND[@]}" -gt 1 ]; then
    bad "Several serial ports found - name one with -p:"; printf '     %s\n' "${FOUND[@]}" >&2
    exit 10
  fi
  PORT="${FOUND[0]}"
  say "Using $PORT"
fi

# ---- locate esptool and boot_app0 inside the installed core ---------------
step "Finding esptool"
ARDUINO15="${ARDUINO_DATA_DIR:-$HOME/Library/Arduino15}"
[ -d "$ARDUINO15" ] || ARDUINO15="$HOME/.arduino15"   # Linux
ESPTOOL=""
if [ -d "$ARDUINO15/packages/esp32/tools/esptool_py" ]; then
  ESPTOOL="$(find "$ARDUINO15/packages/esp32/tools/esptool_py" -maxdepth 2 -name esptool -type f 2>/dev/null | sort | tail -1)"
fi
[ -z "$ESPTOOL" ] && command -v esptool >/dev/null 2>&1 && ESPTOOL="$(command -v esptool)"
[ -z "$ESPTOOL" ] && command -v esptool.py >/dev/null 2>&1 && ESPTOOL="$(command -v esptool.py)"
if [ -z "$ESPTOOL" ]; then
  bad "esptool not found. Install the esp32 core with arduino-cli (see docs/FLASHING.md)."
  exit 5
fi
say "$(basename "$ESPTOOL")"

BOOT_APP0="$(find "$ARDUINO15/packages/esp32/hardware/esp32" -maxdepth 4 -name boot_app0.bin 2>/dev/null | sort | tail -1 || true)"
if [ -z "$BOOT_APP0" ]; then
  bad "boot_app0.bin not found - the esp32 board package does not look installed."
  exit 5
fi

# ---- compile -------------------------------------------------------------
if [ "$COMPILE" -eq 1 ]; then
  step "Compiling"
  if ! arduino-cli compile --fqbn "$FQBN" --output-dir "$BUILD" "$SKETCH"; then
    bad "Compile failed."; exit 2
  fi
fi

APP="$BUILD/hid_fi.ino.bin"
if [ ! -f "$APP" ]; then
  bad "No firmware image at $APP. Run with -c to build first."
  exit 3
fi

# ---- erase (optional, destroys NVS) --------------------------------------
if [ "$ERASE" -eq 1 ]; then
  if [ "$FORCE" -ne 1 ]; then
    printf '   A full erase wipes NVS - WiFi credentials, access PIN, macros, saved PC passwords -\n   and none of it can be backed up first. Type ERASE to continue: '
    read -r ans
    [ "$ans" = "ERASE" ] || { bad "Cancelled."; exit 1; }
  fi
  step "Erasing $PORT"
  "$ESPTOOL" --chip esp32s3 --port "$PORT" --baud 921600 erase-flash
fi

# ---- flash the four partitions (never merged.bin at 0x0) -----------------
step "Flashing $PORT"
say "writing 4 partitions (NVS at 0x9000-0xdfff is left untouched)"
if ! "$ESPTOOL" --chip esp32s3 --port "$PORT" --baud 921600 \
      write-flash --flash-mode dio --flash-freq 80m --flash-size 16MB \
      0x0     "$BUILD/hid_fi.ino.bootloader.bin" \
      0x8000  "$BUILD/hid_fi.ino.partitions.bin" \
      0xe000  "$BOOT_APP0" \
      0x10000 "$APP"; then
  bad "Flash failed. Hold BOOT, tap RST, release BOOT, then try again."
  exit 1
fi
say "Flashed $PORT"

# ---- verify --------------------------------------------------------------
if [ "$VERIFY" -eq 1 ]; then
  step "Verifying $PORT"
  if command -v python3 >/dev/null 2>&1 && python3 -c 'import serial' >/dev/null 2>&1; then
    python3 - "$PORT" <<'PY' || say "Could not read the board back - open the dashboard to confirm it is running."
import sys, time, json, serial
port = sys.argv[1]
try:
    # DTR/RTS off, or opening the port reboots the board.
    s = serial.Serial()
    s.port = port; s.baudrate = 115200; s.dtr = False; s.rts = False
    s.timeout = 2; s.open()
    time.sleep(2.5)              # let it boot
    s.reset_input_buffer()
    s.write(b'{"cmd":"status"}\n')
    deadline = time.time() + 5
    while time.time() < deadline:
        line = s.readline().decode(errors="ignore").strip()
        if not line: continue
        try: d = json.loads(line)
        except Exception: continue
        if d.get("firmware"):
            print(f'   Running {d["firmware"]}, USB HID {"ready" if d.get("hid_ready") else "not ready"}')
            if d.get("host_os"): print(f'   Host OS: {d["host_os"]} ({d.get("host_os_source","?")})')
            print(f'   Dashboard: join WiFi \'{d.get("ap_ssid","ESP32-HID-XXXXXX")}\' then open http://192.168.4.1/')
            break
    s.close()
except Exception as e:
    print(f'   Verify skipped: {e}')
PY
  else
    say "pyserial not installed, skipping the serial check (pip install pyserial to enable it)."
  fi
fi

step "Done."
