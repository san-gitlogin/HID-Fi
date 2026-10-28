# HID-Fi {TAG} - firmware

For the **ESP32-S3 N16R8** (16 MB flash, 8 MB PSRAM). Built from tag `{TAG}`
with ESP32 Arduino core {CORE}, ArduinoJson 7.4.3, WebSockets 2.7.2.

## The easy way (recommended)

Clone the repository at this version and run its flasher. It finds the board,
flashes, then prints the board's WiFi name and its own password:

    git clone --branch {TAG} https://github.com/san-gitlogin/HID-Fi.git
    cd HID-Fi
    ./flash_esp.sh            # macOS / Linux
    .\flash_esp.ps1           # Windows (PowerShell)

## With esptool only

Plug in the board's **COM** port (the CH343 one), then:

    esptool --chip esp32s3 --port <PORT> --baud 921600 write-flash \
      --flash-mode dio --flash-freq 80m --flash-size 16MB \
      0x0     bootloader.bin \
      0x8000  partitions.bin \
      0xe000  boot_app0.bin \
      0x10000 hid_fi.bin

`<PORT>` is e.g. `COM5` on Windows or `/dev/cu.usbmodem...` on a Mac.

**Write these four files at these four addresses - never one merged image at
0x0.** Your settings live between them (NVS, 0x9000-0xdfff); writing the four
files leaves them alone, so an upgrade keeps your PIN, WiFi and stored passwords.

`partitions.bin` gives the board two app slots so later versions can update
over the air. A board flashed with a release before it (one slot) switches on
this flash; your settings stay where they were (docs/FLASHING.md, "The flash
layout").

After flashing, the board makes its own WiFi password on first start. Read it
over the same cable: `./flash_esp.sh --recover --show-password` (or
`.\flash_esp.ps1 -Recover -RecoverShow`) from the repository.

## Check the files

`SHA256SUMS` lists each file's SHA-256: `shasum -a 256 -c SHA256SUMS`.

## License

PolyForm Noncommercial 1.0.0 (LICENSE.md): free for personal and noncommercial
use. Selling boards with this firmware, or any commercial use, needs a license
from the author.
