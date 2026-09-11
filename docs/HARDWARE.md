# Hardware

Everything physical: what board to buy, which socket does what, and the two
hardware quirks that will otherwise cost you an afternoon.

---

## The board

Built and tested on a **YD-ESP32-S3 N16R8**:

| | |
|---|---|
| Chip | ESP32-S3 N16R8 (dual core, WiFi, BLE) |
| Flash | 16 MB |
| PSRAM | 8 MB, OPI |
| USB | Two USB-C sockets — one native, one via a CH343 bridge |
| RGB LED | WS2812 on GPIO48, **not connected by default** (see below) |

The board used by the author: [amzn.in/d/0b1t9Zab](https://amzn.in/d/0b1t9Zab)
*(Amazon India)*.

### Using a different board

> [!IMPORTANT]
> **Tested on exactly one board: the YD-ESP32-S3 N16R8.** Other ESP32-S3 boards
> are expected to work and the requirements are listed here, but none has been
> tried. Other chips in the ESP32 family are not a "change a setting" problem:
> the original ESP32, the C3, C6 and H2 have no USB device peripheral that can
> present as a keyboard, and the S2 has not been tested either. If you do run
> this on something else, please report what happened.

Any ESP32-S3 board should work if it has:

- **A native USB port** wired to the ESP32-S3's own USB pins (GPIO19/GPIO20).
  This is the part that becomes the keyboard. A board with only a USB-to-serial
  chip **cannot** do this, no matter what firmware you flash.
- **8 MB PSRAM**, or drop `PSRAM=opi` from the build flags.
- **16 MB flash**, which is what `FlashSize=16M` and the `huge_app` partition
  scheme in `flash_esp.ps1` assume. A smaller part needs both changed.

If your board has only one USB-C socket, check whether it is native USB or a
bridge. If it is a bridge, this project will not work on it.

---

## The two USB ports

**This is the single most common source of confusion.** The two sockets are not
interchangeable and are not a redundancy.

| Port | Marked | Chip behind it | USB ID | Job |
|---|---|---|---|---|
| One side | **COM** | CH343 USB-to-serial | `1A86:55D3` | Flashing and serial commands |
| Other side | **USB** | ESP32-S3 native USB | `303A:1001` | Being the keyboard and mouse |

### Which do I plug in, and when?

**To flash:** COM only.

**To use it:** USB into the machine you want to control.

**Both:** perfectly fine, and what you want while developing. You can flash and
watch the serial log on one machine while the HID side drives another — or the
same one.

### Symptoms of getting it wrong

| What you see | What it means |
|---|---|
| Flashing works, but the PC never sees a keyboard | You are still only on COM. Plug the USB port in. |
| No serial port appears at all | You are only on USB, or the cable is charge-only. |
| Board powers up but nothing enumerates | Charge-only cable. Swap it. |

### Cables

Both cables must be **data** cables. A charge-only USB-C cable will power the
board, light it up, and do nothing else. If something inexplicable is happening,
change the cable before you change anything else.

---

## Power

The board is powered from whichever USB port is plugged in. With both connected it
draws from both, which is fine.

**If the host sleeps and cuts VBUS, the board loses power entirely** and its WiFi
access point disappears with it. If you want the dashboard to stay reachable while
the target machine sleeps, power the COM port from something that stays on — a
phone charger or a powered hub.

This also matters for waking a sleeping machine: see
[the wake section in docs/COMMANDS.md](COMMANDS.md#waking-a-sleeping-host).

---

## The RGB LED needs solder

The board has a WS2812 RGB LED on GPIO48 that **is not connected out of the
factory**. From the vendor's own guide:

> It is equipped with a WS2812-RGB LED (note: it is not directly controlled by
> GPIO).

> If you want to interface the RGB LED, you need to bridge the pads marked "RGB".
> Please note that once this is done, the specific pin (GPIO48) can only be used
> to interface the RGB LED.

So the `led_set` and `led_blink` commands run, report success, and light nothing.
This is not a firmware bug and no firmware change can fix it — GPIO48 simply is
not wired to the LED until you bridge those pads with solder.

Because of that, **the dashboard has no LED controls**. A row of colour buttons
that cannot light anything is worse than no row at all. The commands remain
available over serial and HTTP for anyone who has bridged the pads.

---

## Buttons

| Button | What it does |
|---|---|
| **RST** | Resets the board. |
| **BOOT** (GPIO0) | Hold while resetting to force the ROM bootloader. Also readable as a user button once running. |

You should not normally need either — `flash_esp.ps1` resets the board into the
bootloader over DTR/RTS by itself. If a board ever refuses to be flashed, hold
BOOT, tap RST, release BOOT, and flash again.

---

## What the host sees

Once flashed and plugged in over the **USB** port, the machine enumerates a single
composite device with several HID collections:

| Collection | Appears as |
|---|---|
| COL01 | Keyboard |
| COL02 | Consumer control (media keys) |
| COL03 | System control (sleep, wake, power) |
| COL04 | Mouse — relative or absolute, chosen at boot |
| Optional | Gamepad, only when enabled |

All are standard HID. No driver is installed and no software runs on the host.
This is why it works on a lock screen, in a BIOS, and on a machine you have no
rights to.
