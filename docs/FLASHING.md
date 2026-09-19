# Flashing the ESP32-S3 USB-HID firmware

Firmware **hid_fi_v3.5** — USB HID keyboard, trackpad mouse, media and system
control, gamepad, and a WiFi dashboard served off the board itself.

**ESP32-S3 only, and tested on one board: the ESP32-S3 N16R8.** The firmware needs the S3's native USB peripheral to be a
keyboard and mouse at all, so an ESP32, S2, C3, C6 or H2 will not do. See
[HARDWARE.md](HARDWARE.md).

Everything here is done from a terminal. You do not need the Arduino IDE.

---

## The short version

Plug the cable into the port labelled **COM**, open PowerShell in this folder, and run:

```powershell
.\flash_esp.ps1
```

That is the whole thing. The script finds the board, finds the flashing tool, writes the
firmware, then asks the board what it is running and prints the answer. Expect:

```
== Flashing COM13
   Flashed COM13
== Verifying COM13
   Running hid_fi_v3.5, USB HID ready
   Dashboard: connect to WiFi 'ESP32-HID-09F7C8' (password hid12345) then open http://192.168.4.1/
   Done.
```

If PowerShell refuses to run the script, it is the execution policy, not you:

```powershell
powershell -ExecutionPolicy Bypass -File .\flash_esp.ps1
```

**On macOS or Linux**, use the twin script instead — same behaviour, same
guarantee about leaving NVS alone:

```bash
./flash_esp.sh          # or ./flash_esp.sh -c to rebuild first
```

macOS has a couple of one-time setup steps the first time the board is plugged in
(the accessory prompt and the keyboard assistant); see [MACOS.md](MACOS.md).

---

## What you need

| | |
|---|---|
| **Hardware** | The ESP32-S3-N16R8 board and one **data** USB-C cable. Charge-only cables will not work. |
| **Driver** | CH343, from [wch-ic.com](https://www.wch-ic.com/downloads/CH343SER_EXE.html). Only needed once per PC. |
| **To flash** | Nothing else. `esptool` ships inside the ESP32 board package and the script finds it. |
| **To rebuild** | `arduino-cli`, the ESP32 core, and two libraries — see below. |

### Which cable, which port

The board has **two** USB-C ports and they do different jobs:

- **COM** — the CH343 serial bridge. This is the one you flash through, and the one the
  PC-unlock service talks to. Windows shows it as `USB-Enhanced-SERIAL CH343 (COMx)`.
- **USB** — the ESP32's own USB. This is what becomes the keyboard and mouse on the target
  PC. It does **not** appear as a COM port once our firmware is running.

For flashing, only **COM** needs to be connected. If both are plugged in and the flash
misbehaves, unplug **USB** and try again.

> The **USB-OTG pads** on the underside of the board must be bridged with solder, or the
> USB port will never enumerate as a keyboard. This is a one-time hardware step.

---

## Script options

```powershell
.\flash_esp.ps1                  # find the board, flash it, verify it
.\flash_esp.ps1 -Compile         # rebuild from source first
.\flash_esp.ps1 -Port COM13      # skip auto-detection
.\flash_esp.ps1 -All             # flash every board that is plugged in
.\flash_esp.ps1 -Erase           # wipe the chip completely, then flash
.\flash_esp.ps1 -Erase -Force    # the same, without the confirmation prompt
.\flash_esp.ps1 -NoVerify        # skip the serial check at the end
```

Exit codes, for scripting: `0` ok · `1` flash failed · `2` compile failed ·
`3` no firmware image · `4` no board found · `5` esptool missing · `10` several boards, name one.

### Your settings survive an upgrade

A normal flash writes only the four firmware partitions and leaves **NVS** untouched, so the
board keeps its WiFi credentials, access PIN, pointer mode, macros, custom knobs and quick
actions, and any saved PC passwords.

`-Erase` deliberately wipes all of that. Use it only for a genuine factory reset — it is also
the only way to remove a saved PC password without going through the dashboard.

It asks before doing it, because **nothing it removes can be backed up first**: the board never
returns a password or a PIN to any command, so there is no way to read them off and put them
back. After a factory reset you retype the access point password, the home network password,
the access PIN and any saved PC passwords by hand. The dashboard layout can be saved and
restored through `ui_get` / `ui_save` if you take a copy first.

> This is why the script does **not** flash `hid_fi.ino.merged.bin`. That file is a
> full 16 MB image, and writing it at `0x0` overwrites NVS along with everything else.

### A blank or unknown board

Nothing has to be in place first. The serial bridge is a separate chip, so the board still
appears as a COM port with no firmware at all, and the flasher writes the bootloader, the
partition table, `boot_app0` and the app in one pass.

This is verified, not assumed: erasing all 16 MB leaves the board in a boot loop printing
`invalid header: 0xffffffff`, and a plain `.\flash_esp.ps1` brings it straight back — after
which the access point returns to its factory name, `ESP32-HID-XXXXXX` with the password
`hid12345`. The same applies to a board carrying somebody else's firmware.

---

## Rebuilding from source

Only needed if you changed `hid_fi.ino` or `web_ui.h`.

One-time setup:

```powershell
arduino-cli core install esp32:esp32
arduino-cli lib install ArduinoJson
arduino-cli lib install WebSockets
```

Then:

```powershell
.\flash_esp.ps1 -Compile
```

The board settings are baked into the script, but for reference the exact FQBN is:

```
esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=default,PSRAM=opi,FlashSize=16M,UploadSpeed=921600,PartitionScheme=huge_app
```

Two of those matter more than the rest:

- `USBMode=default` is **USB-OTG (TinyUSB)**. Get this wrong and the board runs fine but
  never types anything. It is the single most common mistake.
- `CDCOnBoot=default` is **USB CDC On Boot: Disabled**, because the firmware talks over
  UART0 through the CH343, not over the native USB.

The sketch is **two files** — `hid_fi.ino` and `web_ui.h` (the dashboard). Both must
be in the sketch folder.

---

## Checking it worked

The script does this for you, but to do it by hand, send `{"cmd":"status"}` to the COM port
at 115200 baud with DTR and RTS **off** (otherwise opening the port reboots the board):

```powershell
python tests/test_v34_features.py
```

That runs the full regression suite — 24 checks covering the pointer, media keys, macros,
the multi-touch keyboard and the PC-unlock contract. Every action it performs is reversible,
so it is safe to run on a live desktop.

To confirm the PC sees the HID devices:

```powershell
Get-CimInstance Win32_PnPEntity | Where-Object { $_.DeviceID -match 'VID_303A' } | Select-Object Name
```

Expect four: HID Keyboard Device, HID-compliant mouse, consumer control, system controller.

---

## When it goes wrong

| Symptom | Cause and fix |
|---|---|
| `No ESP32-S3 board detected` | Wrong port on the board (use **COM**), a charge-only cable, or the CH343 driver is missing. |
| Windows shows an unknown device | Install the CH343 driver, then replug. |
| `the port is busy` | Something else has it open — Arduino Serial Monitor, PuTTY, or a Python script. Close it. |
| Flash fails or times out | Hold **BOOT**, tap **RESET**, release **BOOT**, then run the script again. That forces the bootloader. |
| Flash works, but nothing types | `USBMode` was not USB-OTG, or the **USB** cable is not connected, or the USB-OTG pads are not bridged. |
| Serial connects but never answers | `CDCOnBoot` was enabled at build time, or DTR/RTS are being asserted and rebooting the board. |
| `several boards, name one` | More than one is plugged in. Use `-Port COMx` or `-All`. The script will not guess. |
| Script will not run at all | `powershell -ExecutionPolicy Bypass -File .\flash_esp.ps1` |

---

## Doing it manually

If you would rather not use the script, or you are not on Windows, this is exactly what it
runs. `<core>` is your ESP32 core folder, e.g.
`%LOCALAPPDATA%\Arduino15\packages\esp32\hardware\esp32\3.3.11`.

```
esptool --chip esp32s3 --port COM13 --baud 921600 write-flash \
  --flash-mode dio --flash-freq 80m --flash-size 16MB \
  0x0     build/hid_fi.ino.bootloader.bin \
  0x8000  build/hid_fi.ino.partitions.bin \
  0xe000  <core>/tools/partitions/boot_app0.bin \
  0x10000 build/hid_fi.ino.bin
```

NVS sits at `0x9000`–`0xdfff`, between the partition table and `boot_app0`, which is why
those four writes leave your settings alone.
