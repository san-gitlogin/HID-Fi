<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/banner-light.svg">
    <img alt="HID-Fi" src="docs/assets/banner-light.svg" width="820">
  </picture>
</p>

# HID-Fi

**Turn a small dev board into a wireless trackpad, keyboard, media remote and
gamepad for any computer — with nothing installed on that computer.**

The board plugs into a PC over USB and presents itself as a keyboard and mouse. It
also runs its own WiFi access point serving a dashboard. Open that dashboard on
your phone and you are driving the PC.

Because it is a plain USB HID device, the host needs **no drivers, no agent and no
admin rights**. It works on the lock screen, in the BIOS, and on machines you have
no rights to install anything on.

```
     phone  ──WiFi──▶  ESP32-S3 N16R8  ──USB HID──▶  the PC
  (dashboard)             (this repo)          (sees a keyboard and mouse)
```

> [!IMPORTANT]
> **This is an ESP32-S3 project, tested on one board: the YD-ESP32-S3 N16R8.**
> Being a keyboard and mouse needs the S3's native USB peripheral, which the
> original ESP32, the S2, C3, C6 and H2 either lack or expose differently.
> Everything here is built and tested on an **ESP32-S3 N16R8** (16 MB flash,
> 8 MB OPI PSRAM) and nothing else — no other chip has been tried, and no other
> board has been verified.

---

## Contents

- [What it does](#what-it-does)
- [What you need](#what-you-need)
- [The two USB ports are not interchangeable](#the-two-usb-ports-are-not-interchangeable)
- [Quick start](#quick-start)
- [Change the password first](#change-the-password-first)
- [How it works](#how-it-works)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## What it does

| | |
|---|---|
| **Trackpad** | One finger moves, tap clicks, two fingers scroll and pinch, three swipe desktops. Press and hold to grab a window. Speed, acceleration, momentum and scroll direction all adjustable. |
| **Keyboard on the pad** | The trackpad card flips to a keyboard and back from one button, so a click and the typing that follows it do not cost a trip across the app. |
| **Keyboard** | On-screen keys with real press and release, so you can hold Ctrl with one thumb and strike another key with the other. Letters and a full symbol layer covering every printable ASCII character. Type a whole string, or fire a custom combo. |
| **Mouse** | Left, right and a scroll wheel you actually drag — each notch is a detent with a haptic tick, and a tap on it middle-clicks. |
| **Shortcuts** | Hundreds of macOS, Windows and Linux shortcuts, grouped, searchable, and filtered to the OS you pick. Tap one and it fires on the PC. |
| **App switcher** | Hold the tile and Alt stays down, so the host keeps its own switcher open. Slide to pick a window, let go to raise it. |
| **Media** | Volume knobs you turn with a finger, playback keys, brightness, and editable quick actions for mic-mute and call keys. |
| **Gamepad** | Optional second USB device: two sticks, D-pad, twelve buttons. |
| **Session** | Unlock a locked PC by typing its password, lock it again, sleep and wake, presenter controls, and a net-zero cursor jiggle to stay awake. |
| **Knows the computer** | Detects whether it is plugged into a Mac or a PC by watching the Num Lock light, and switches the lock shortcut, gestures, app switcher and keyboard to match — or pin it by hand. See [docs/MACOS.md](docs/MACOS.md). |
| **Knows itself** | Both MAC addresses, what is in the board's permanent memory, and every client on its access point — without ever handing back a password or PIN. |

Flick sideways across empty space to move between tabs. It is deliberately fussy:
it will not fire on the trackpad, on a key, during a hold, or on anything slower
than a flick.

Everything you customise — knobs, quick actions, macros, saved PCs — is stored
**on the board**, so every phone that opens the dashboard sees the same setup.

---

## What you need

- **An ESP32-S3 N16R8 board with two USB-C ports.** Built and tested on the
  YD-ESP32-S3 N16R8 (16 MB flash, 8 MB OPI PSRAM). The board used by the author:
  [amzn.in/d/0b1t9Zab](https://amzn.in/d/0b1t9Zab) *(Amazon India)*.
- **Two USB-C data cables.** Both must carry data. A charge-only cable is the
  single most common reason nothing works.
- **A computer to flash from**, with [arduino-cli](https://arduino.github.io/arduino-cli/).

**Only the ESP32-S3 N16R8 has been tested.** The firmware is compiled for it,
uses the S3's native USB peripheral to be a HID device, and has run on nothing
else. Other ESP32 variants are not a matter of a different board setting — the
original ESP32 and the C3, C6 and H2 have no USB device peripheral capable of
this at all.

Other ESP32-S3 boards with a native USB port should work, and a different flash
or PSRAM size only needs a different `PartitionScheme` and `PSRAM` flag at build
time — but none has been tried. [docs/HARDWARE.md](docs/HARDWARE.md) explains
what actually matters and what to check before buying something else — and if you
do test one, please say so, it is the most useful report you can send.

---

## The two USB ports are not interchangeable

This is the thing that trips up almost everyone. The board has two USB-C sockets
and they do completely different jobs:

| Port | Marked | What it is | Use it for |
|---|---|---|---|
| One side | **COM** | CH343 USB-to-serial bridge | **Flashing**, and serial control |
| Other side | **USB** | The ESP32-S3's own USB peripheral | **Being the keyboard and mouse** |

- Flash over **COM**.
- Once flashed, plug **USB** into the machine you want to control.
- To flash *and* control the same machine, plug both in.

If you flash successfully but the PC never sees a keyboard, you are almost
certainly still on the COM port. Full detail in
[docs/HARDWARE.md](docs/HARDWARE.md).

---

## Quick start

**1. Flash it**, with the board on its **COM** port:

```powershell
.\flash_esp.ps1 -Compile         # Windows
```

```bash
./flash_esp.sh -c                # macOS / Linux
```

The script finds the board, the toolchain and esptool by itself.
[docs/FLASHING.md](docs/FLASHING.md) walks through it from nothing, including
installing the ESP32 core. **On a Mac there are a couple of one-time setup
steps** — the accessory prompt and the keyboard assistant — all covered in
[docs/MACOS.md](docs/MACOS.md).

**2. Plug the USB port** into the computer you want to control.

**3. Connect your phone** to the WiFi network **`ESP32-HID-XXXXXX`**, password
`hid12345`, then open **<http://192.168.4.1/>**.

`XXXXXX` is the last three bytes of that board's MAC address, so several boards
never collide.

**4. Change the password.** Read the next section — this one matters.

---

## Change the password first

> [!WARNING]
> Every board flashed with this firmware starts with the **same** access point
> password, `hid12345`, and it is printed in this README. Until you change it,
> anyone in WiFi range who has seen this project can type on your computer.

Both settings are in the **Settings** tab:

**1. Change the access point.**
Settings → Access point → new name and password → *Change access point*.
Minimum 8 characters. The access point restarts immediately, so your phone drops
off the network. The dashboard shows a full-screen panel with the new name and
password and reconnects by itself once you rejoin.

**2. Set an access PIN.**
Settings → Access PIN → four digits → *Set PIN*.

The PIN gates every wireless request. Four digits is only 10,000 combinations, so
the board **locks out for longer after each wrong guess** — one free retry, then
5s, 15s, 60s, then five minutes per attempt. Walking the whole keyspace would take
about a month.

**USB serial is never gated and never locked out.** If you forget the PIN or lock
yourself out, plug into the COM port and set a new one over serial. That is the
deliberate way back in.

### Joining your home network is not the same as using the access point

The board can run its own access point and be joined to your home network at the
same time, and both routes work at once. But they are not equally safe.

On the access point, an attacker has to be within WiFi range of you. On your home
network, **every device on that network can reach the board** — the smart TV, a
guest's laptop, anything already compromised. Nothing about the dashboard looks
different, so the firmware enforces the difference instead of just warning:

> Once the board is on a home network, wireless control from outside its own
> access point is **refused until a PIN is set**. Reading status and setting the
> PIN stay available, so you can fix it from where you are standing.

More detail, including what this project does *not* protect against, in
[SECURITY.md](SECURITY.md).

---

## Power

Almost all of this board's draw is the radio, not the processor: roughly
100–120 mA with WiFi awake against 30–40 mA with modem sleep on.

That is an awkward tradeoff, because keeping the radio awake is exactly what makes
the pointer feel instant. Modem sleep parks the radio between router beacons and
you can see the stutter. So rather than making you choose once and live with it,
the default decides per moment.

| Mode | What it does |
|---|---|
| Performance | Radio always awake. Lowest latency, highest draw. |
| **Balanced** *(default)* | Awake while a dashboard is connected, asleep once none is. Costs nothing you can feel. |
| Saver | Radio always asleep. Lowest draw, and the pointer stutters. |

The bigger saving is **turning the access point off when it is unused**. An access
point beacons whether or not anyone is listening, so once the board is reachable
over your home network the AP is pure cost — and pure attack surface. Turn it on
in Settings → Power. **Holding BOOT for two seconds always brings it back**, which
is the guaranteed way in if anything goes wrong.

CPU frequency scaling is deliberately not offered. It saves far less than the
radio does, and it changes the clock the serial port derives from — not worth
risking the cable that is your recovery path for a few milliamps.

---

## How it works

Three ways in, one way out. All three speak the same JSON commands:

```
serial (CH343, 115200)   ─┐
HTTP   :80 /api/command   ─┼─▶  command dispatch  ─▶  USB HID  ─▶  the PC
WebSocket :81             ─┘
```

- **WebSocket** carries pointer, joystick and knob traffic as **binary frames**.
  This matters more than it sounds: the ESP32 core's `WebServer` has no HTTP
  keep-alive, so one POST per pointer event costs a full TCP handshake. That was
  the difference between a laggy cursor and a smooth one.
- **HTTP** serves the dashboard and stays as a fallback for commands.
- **Serial** is never gated by the PIN, so a cable is always a way back in.

**Nothing in the main loop is allowed to block.** That loop services the socket,
the web server and the pointer stream, so anything that waits freezes the
dashboard — and a long enough freeze drops the connection. Joining a network and
scanning for networks both used to block for seconds; both are now state machines
that report progress the dashboard polls and narrates live.

Settings live in NVS and survive a reboot *and* a firmware upgrade —
`flash_esp.ps1` deliberately writes only the four firmware partitions and leaves
NVS alone.

More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Documentation

| Document | What is in it |
|---|---|
| [docs/HARDWARE.md](docs/HARDWARE.md) | The board, the two ports, cables, the RGB LED, power |
| [docs/FLASHING.md](docs/FLASHING.md) | Flashing from nothing, rebuilding, troubleshooting |
| [docs/MACOS.md](docs/MACOS.md) | Mac setup — the accessory prompt, the keyboard assistant, and what auto-switches |
| [docs/COMMANDS.md](docs/COMMANDS.md) | Every JSON command and WebSocket opcode |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the firmware and dashboard fit together |
| [SECURITY.md](SECURITY.md) | Threat model, and what is deliberately not protected |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Building, testing, and the constraints that will bite you |

---

## Contributing

Issues and pull requests are welcome, and so is simply telling me it broke on your
board.

**If this is useful to you, please star the repo.** It is the main signal that
tells me whether to keep working on it.

Fork it, build something, send it back if others would want it.
[CONTRIBUTING.md](CONTRIBUTING.md) covers the build, the test suites, and the two
hardware constraints that are not obvious from the code.

Good places to start:

- **Keyboard layouts other than US.** The board types US-layout ASCII today, so
  passwords and symbols come out wrong on other layouts. This is the most
  valuable open problem.
- **More shortcuts**, especially Linux desktops beyond GNOME.
- **Testing on other ESP32-S3 boards** and reporting what needed to change.

---

## License

**[PolyForm Noncommercial 1.0.0](LICENSE.md)** — free for personal projects, hobby
use, study, research, charities, schools and government bodies. Keep the copyright
notice and you are fine.

**Commercial use needs a separate license.** If you want to sell boards with this
on them, ship it inside a product, or use it as part of paid work, get in touch
and we will agree terms — usually a flat fee or a revenue share depending on what
you are building.

This is **source-available, not OSI open source**. That is deliberate: the work
took real effort, and I would rather share it openly with one condition attached
than not share it at all.

---

## Credits

Built by **Santhosh**.

If you build on this, credit is required by the license and appreciated well
beyond it. A link back to this repo is enough.
