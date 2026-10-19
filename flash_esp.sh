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
#     ./flash_esp.sh -p /dev/cu.wchusbserial123   use this port
#     ./flash_esp.sh -s 5CBC409942   use the board with this USB serial number
#     ./flash_esp.sh -a              flash every HID-Fi board that is plugged in
#     ./flash_esp.sh -w              only say which board(s) would be flashed
#     ./flash_esp.sh --identify      ask each board what it runs (restarts them)
#     ./flash_esp.sh --recover       forgot the WiFi password or the access PIN?
#                                    show or set the WiFi password, reset the PIN -
#                                    over the USB COM cable only (see SECURITY.md)
#     ./flash_esp.sh -e              wipe the chip completely, then flash
#     ./flash_esp.sh -e -f           the same, without the confirmation prompt
#     ./flash_esp.sh -n              skip the serial check at the end
#
#  Several boards plugged in: only HID-Fi boards (their COM connector is a CH343,
#  USB 1a86:55d3) are ever picked on their own; an Arduino or another ESP32 kit is
#  listed and left alone. With more than one HID-Fi board it asks which (or, with
#  no terminal, stops with exit 10). HIDFI_PORT / HIDFI_SERIAL set a default.
#  A port that is not a HID-Fi COM bridge is refused unless --force-port.
#
#  Exit codes:  0 ok | 1 flash failed | 2 compile failed | 3 no image |
#               4 no board found | 5 esptool missing | 10 several boards, pick one |
#               11 that port is not a HID-Fi board
#
#  Needs arduino-cli and the esp32 core (see docs/FLASHING.md). esptool ships
#  inside the core, so nothing else is required to flash.
# ============================================================================
set -euo pipefail

FQBN='esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=default,PSRAM=opi,FlashSize=16M,UploadSpeed=921600,PartitionScheme=huge_app'
HERE="$(cd "$(dirname "$0")" && pwd)"
SKETCH="$HERE/hid_fi"
BUILD="${HIDFI_BUILD_DIR:-$SKETCH/build}"   # same as flash_esp.ps1, and covered by .gitignore

COMPILE=0 ERASE=0 FORCE=0 VERIFY=1 PORT="${HIDFI_PORT:-}" SERIAL="${HIDFI_SERIAL:-}"
ALL=0 WHICH=0 IDENTIFY=0 FORCE_PORT=0 RECOVER=0 RECOVER_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    -c|--compile)  COMPILE=1 ;;
    -e|--erase)    ERASE=1 ;;
    -f|--force)    FORCE=1 ;;
    -n|--no-verify) VERIFY=0 ;;
    -p|--port)     PORT="${2:-}"; shift ;;
    -s|--serial)   SERIAL="${2:-}"; shift ;;
    -a|--all)      ALL=1 ;;
    -w|--which)    WHICH=1 ;;
    --identify)    IDENTIFY=1 ;;
    --force-port)  FORCE_PORT=1 ;;
    --recover)     RECOVER=1; shift; RECOVER_ARGS=("$@"); break ;;   # the rest goes to the tool
    -h|--help)     sed -n '2,42p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

say()  { printf '   %s\n' "$*"; }
step() { printf '== %s\n' "$*"; }
bad()  { printf '!! %s\n' "$*" >&2; }

# ---- find the board ------------------------------------------------------
# tools/flash_ports.py reads each port's USB identity and never guesses between
# boards - see its header. Without pyserial it cannot tell ports apart, and the
# old rule applies: exactly one USB serial port, or name it.
legacy_ports() {
  # The board's CH343 is cu.wchusbserial* with WCH's driver, cu.usbserial* or
  # cu.usbmodem* with Apple's, ttyUSB*/ttyACM* on Linux. One device can have two
  # macOS names (wchusbserialX and usbserialX); keep the first of each serial.
  ls /dev/cu.wchusbserial* /dev/cu.usbserial* /dev/cu.usbmodem* \
     /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | awk '{ k=$0; sub(/.*(wchusbserial|usbserial-?|usbmodem)/,"",k); if (!(k in s)) { s[k]=1; print } }' || true
}
TARGETS=() CANDIDATES=()
pick_boards() {   # args for flash_ports.py; fills TARGETS / CANDIDATES, returns its code
  TARGETS=() CANDIDATES=()
  local code=12 line
  if command -v python3 >/dev/null 2>&1; then
    while IFS= read -r line; do
      case "$line" in
        "SAY "*)       say "${line#SAY }" ;;
        "TARGET "*)    TARGETS+=("${line#TARGET }") ;;
        "CANDIDATE "*) CANDIDATES+=("${line#CANDIDATE }") ;;
        "CODE "*)      code="${line#CODE }" ;;
      esac
    done < <(python3 "$HERE/tools/flash_ports.py" "$@" 2>/dev/null || echo "CODE 12")
  fi
  return "$code"
}

if [ "$RECOVER" -eq 1 ]; then
  # Recovery needs nothing from the toolchain - only the cable and pyserial.
  command -v python3 >/dev/null 2>&1 || { bad "Recovery needs Python 3 (python.org) and pyserial (pip install pyserial)."; exit 12; }
  R=()
  [ -n "$PORT" ]   && R+=(--port "$PORT")
  [ -n "$SERIAL" ] && R+=(--serial "$SERIAL")
  exec python3 "$HERE/tools/recover.py" ${R[@]+"${R[@]}"} ${RECOVER_ARGS[@]+"${RECOVER_ARGS[@]}"}
fi

step "Finding the board"
ARGS=()
[ -n "$PORT" ]          && ARGS+=(--port "$PORT")
[ -n "$SERIAL" ]        && ARGS+=(--serial "$SERIAL")
[ "$ALL" -eq 1 ]        && ARGS+=(--all)
[ "$FORCE_PORT" -eq 1 ] && ARGS+=(--force-port)
if [ "$IDENTIFY" -eq 1 ]; then
  pick_boards ${ARGS[@]+"${ARGS[@]}"} --identify || true
  exit 0
fi
set +e; pick_boards ${ARGS[@]+"${ARGS[@]}"}; CODE=$?; set -e
if [ "$CODE" -eq 10 ] && [ -t 0 ] && [ -z "${CI:-}" ] && [ "$WHICH" -eq 0 ]; then
  printf '   Which one? [1-%s, a = all, Enter = none]: ' "${#CANDIDATES[@]}"
  read -r ans || ans=""
  if [ "$ans" = "a" ]; then
    TARGETS=(${CANDIDATES[@]+"${CANDIDATES[@]}"}); CODE=0
  elif [ -n "$ans" ] && [ "$ans" -ge 1 ] 2>/dev/null && [ "$ans" -le "${#CANDIDATES[@]}" ]; then
    TARGETS=("${CANDIDATES[$((ans - 1))]}"); CODE=0
  else
    bad "Nothing chosen, nothing flashed."; exit 10
  fi
elif [ "$CODE" -eq 12 ]; then
  # No pyserial: the old way, by port name only.
  if [ -n "$PORT" ]; then
    TARGETS=("$PORT -")
  else
    FOUND=()                            # read loop, not mapfile: macOS ships bash 3.2
    while IFS= read -r line; do [ -n "$line" ] && FOUND+=("$line"); done < <(legacy_ports)
    if [ "${#FOUND[@]}" -eq 0 ]; then
      bad "No ESP32-S3 board detected. Plug the cable into the COM port, check it is a data cable, and install the CH343 driver (wch-ic.com) if macOS shows no serial device."
      exit 4
    elif [ "${#FOUND[@]}" -gt 1 ]; then
      bad "Several serial ports found - name one with -p (pip install pyserial and this script can tell which are HID-Fi boards):"
      printf '     %s\n' "${FOUND[@]}" >&2
      exit 10
    fi
    TARGETS=("${FOUND[0]} -")
  fi
  CODE=0
fi
[ "$CODE" -eq 0 ] || exit "$CODE"
for t in "${TARGETS[@]}"; do say "Using ${t% *}  (serial ${t#* })"; done
if [ "$WHICH" -eq 1 ]; then
  exit 0
fi

# ---- locate esptool and boot_app0 inside the installed core ---------------
step "Finding esptool"
ARDUINO15="${ARDUINO_DATA_DIR:-$HOME/Library/Arduino15}"
[ -d "$ARDUINO15" ] || ARDUINO15="$HOME/.arduino15"   # Linux
ESPTOOL="${HIDFI_ESPTOOL:-}"
if [ -z "$ESPTOOL" ] && [ -d "$ARDUINO15/packages/esp32/tools/esptool_py" ]; then
  ESPTOOL="$(find "$ARDUINO15/packages/esp32/tools/esptool_py" -maxdepth 2 -name esptool -type f 2>/dev/null | sort | tail -1)"
fi
[ -z "$ESPTOOL" ] && command -v esptool >/dev/null 2>&1 && ESPTOOL="$(command -v esptool)"
[ -z "$ESPTOOL" ] && command -v esptool.py >/dev/null 2>&1 && ESPTOOL="$(command -v esptool.py)"
if [ -z "$ESPTOOL" ]; then
  bad "esptool not found. Install the esp32 core with arduino-cli (see docs/FLASHING.md)."
  exit 5
fi
say "$(basename "$ESPTOOL")"

BOOT_APP0="${HIDFI_BOOT_APP0:-$(find "$ARDUINO15/packages/esp32/hardware/esp32" -maxdepth 4 -name boot_app0.bin 2>/dev/null | sort | tail -1 || true)}"
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
if [ "$ERASE" -eq 1 ] && [ "$FORCE" -ne 1 ]; then
  printf '   A full erase wipes NVS on %s board(s) - WiFi credentials, access PIN, macros,\n   saved PC passwords - and none of it can be backed up first. Type ERASE to continue: ' "${#TARGETS[@]}"
  read -r ans
  [ "$ans" = "ERASE" ] || { bad "Cancelled."; exit 1; }
fi

flash_one() {   # $1 = port. Erase (if asked), write the four partitions, read back.
  PORT="$1"
  if [ "$ERASE" -eq 1 ]; then
    step "Erasing $PORT"
    "$ESPTOOL" --chip esp32s3 --port "$PORT" --baud 921600 erase-flash || return 1
  fi
  # ---- the four partitions (never merged.bin at 0x0) ----
  step "Flashing $PORT"
  say "writing 4 partitions (NVS at 0x9000-0xdfff is left untouched)"
  if ! "$ESPTOOL" --chip esp32s3 --port "$PORT" --baud 921600 \
        write-flash --flash-mode dio --flash-freq 80m --flash-size 16MB \
        0x0     "$BUILD/hid_fi.ino.bootloader.bin" \
        0x8000  "$BUILD/hid_fi.ino.partitions.bin" \
        0xe000  "$BOOT_APP0" \
        0x10000 "$APP"; then
    bad "Flash failed on $PORT. Hold BOOT, tap RST, release BOOT, then try again."
    return 1
  fi
  say "Flashed $PORT"
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
            # The board's own WiFi password, read over this cable. Each board makes
            # its own (v3.10), so this is where a person first learns it.
            s.write(b'{"cmd":"ap_password"}\n')
            pw = None
            end2 = time.time() + 3
            while time.time() < end2:
                l2 = s.readline().decode(errors="ignore").strip()
                try: a = json.loads(l2)
                except Exception: continue
                if a.get("reply") == "ap_password": pw = a; break
                if a.get("status") == "error": break
            if pw and pw.get("open"):
                print(f'   Dashboard: join WiFi \'{pw.get("ssid")}\' (open, no password), then open http://192.168.4.1/')
            elif pw:
                print(f'   Dashboard: join WiFi \'{pw.get("ssid")}\' with password  {pw.get("password")}')
                print('              then open http://192.168.4.1/   (forgot it later? ./flash_esp.sh --recover)')
            else:
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
  return 0
}

FAILED=() DONE=()
for t in "${TARGETS[@]}"; do
  if flash_one "${t% *}"; then DONE+=("${t% *}"); else FAILED+=("${t% *}"); fi
done
if [ "${#TARGETS[@]}" -gt 1 ]; then
  step "Summary"
  for d in ${DONE[@]+"${DONE[@]}"};     do say "ok      $d"; done
  for f in ${FAILED[@]+"${FAILED[@]}"}; do say "FAILED  $f"; done
fi
[ "${#FAILED[@]}" -eq 0 ] || { bad "${#FAILED[@]} board(s) failed."; exit 1; }

step "Done."
