# Using HID-Fi with a Mac

On Windows this board is plug-and-play: connect the USB port and the PC has a
keyboard and mouse. **macOS adds a few one-time steps**, none of them hard, but
none of them obvious either. This is the page nobody wrote down until now.

Two of the steps are macOS asking about a keyboard it has never seen before. The
board answers to both, so you are not left guessing which key is where.

---

## The short version

1. **Flash from the Mac** with [`flash_esp.sh`](../flash_esp.sh) (the macOS/Linux
   twin of `flash_esp.ps1`).
2. Plug the **USB** port into the Mac. macOS asks **"Allow accessory to
   connect?"** — click **Allow**, and do it while the Mac is **unlocked**.
3. The **Keyboard Setup Assistant** may open, asking you to identify the
   keyboard. Open the dashboard → **Settings → Mac setup** and tap the two
   buttons when it asks. The board is a US keyboard, so the answers are always
   the same.
4. That is it. The board detects it is on a Mac and switches the lock shortcut,
   the gestures, the app switcher and the keyboard to match.

---

## Flashing on a Mac

Everything is done from Terminal. You need
[arduino-cli](https://arduino.github.io/arduino-cli/) and the ESP32 core — see
[FLASHING.md](FLASHING.md) for the one-time setup, which is identical on every
platform.

With the board on its **COM** port:

```bash
./flash_esp.sh -c        # build from source, then flash
./flash_esp.sh           # flash the existing build
./flash_esp.sh -p /dev/cu.wchusbserial1420   # skip auto-detection
```

The script finds the CH343 serial port, the bundled `esptool` and `boot_app0`
itself, writes only the four firmware partitions — so your saved settings
survive — and reads the board back to confirm it is running. Exit codes and
flags match `flash_esp.ps1`; run `./flash_esp.sh -h` for the list.

> **The serial port has a different name on a Mac.** Windows shows the CH343 as
> `COMx`; macOS shows it as `/dev/cu.wchusbserial*` (with the
> [WCH driver](https://www.wch-ic.com/downloads/CH343SER_MAC_ZIP.html)) or
> `/dev/cu.usbserial*`. If nothing appears, it is almost always the driver or a
> charge-only cable — the same two causes as everywhere else.

---

## Step 1 — "Allow accessory to connect?"

Apple-silicon Macs ask permission the first time **any** USB device is plugged
in. You will see **Allow accessory to connect?** the first time the board's USB
port is connected. Click **Allow**.

This is macOS, not the board, and it cannot be skipped — nor should it be. Two
things are worth knowing:

- **Approve it while the Mac is unlocked.** The prompt only appears for someone
  logged in, so if the board is plugged into a **locked** Mac for the very first
  time, macOS quietly blocks it until someone unlocks the Mac and approves it.
  **This means the unlock feature cannot work on the first-ever plug-in of a
  locked Mac** — the board has to be approved once, on that Mac, while it is
  open. After that it is remembered and the board works on the lock screen like
  any other keyboard.
- If you ever change your mind, the setting lives in **System Settings → Privacy
  & Security → Allow accessories to connect**.

---

## Step 2 — the Keyboard Setup Assistant

The first time a keyboard macOS does not recognise is connected, the **Keyboard
Setup Assistant** opens. It asks you to press:

1. **the key immediately to the right of the left Shift**, then
2. **the key immediately to the left of the right Shift.**

It is identifying the *board*, not your MacBook's own keyboard — which is the
part that confuses everyone. If you answer by looking at your laptop, you may
still get it right by luck, but the on-screen keyboard in the dashboard is laid
out for a phone and does not match a Mac's key positions, so it is no help here.

**You do not have to guess.** The board is a **US ANSI** keyboard, so the two
keys are always **Z** and **/**. Open the dashboard → **Settings → Mac setup**:

- Tap **"1 · Key by left Shift"** when the assistant asks for the first key.
- Tap **"2 · Key by right Shift"** when it asks for the second.

Each button sends exactly the keystroke the assistant is waiting for. If the
assistant does not appear, you do not need it — macOS already knows the layout.

> If it picks the wrong layout, you can rerun it later from **System Settings →
> Keyboard → Keyboard type…**, or just answer with the two buttons again.

---

## What changes once it knows it is a Mac

The board cannot see your screen, but the computer tells it something while it
reads the USB descriptors on the way to setting up the keyboard, without being
asked. Detection reads those enumeration signals — it never types anything to
find out.

**What it reads:** macOS asks for each descriptor string twice — two bytes first
to learn how long it is, then the whole string — so the same request arrives back
to back, for string after string. Windows does that for the odd string but not as
a habit, so the board judges the proportion, not the count.

> An earlier build instead assumed macOS does not send the HID `SET_IDLE`
> request. Measured
> on a real Mac, it does, so every Mac was reported as Windows. `SET_IDLE` is
> still counted as evidence a host is there, but it no longer decides anything.

> Earlier versions tapped **Num Lock** and timed the host's LED reply. That is
> gone. It changed state on your computer, it did not always undo the toggle, and
> a slow host was misread as a Mac. Nothing is typed now, so there is nothing to
> undo, and detection re-runs whenever you move the board to another computer.

**If it says "Not identified"**, the computer did not say enough to be
recognised — Windows in particular caches a device's descriptor strings and may
stay quiet on a replug. The dashboard asks you to pick rather than guessing, and
**Settings → Computer** prints the raw signals it saw underneath.

With a Mac selected, it switches automatically:

| Thing | On a PC | On a Mac |
|---|---|---|
| **Lock** | `Win + L` | `Control + Command + Q` |
| **App switcher** | holds **Alt**, Tab to move | holds **Command**, Tab to move |
| **Mission Control / Spaces** | `Win + Tab`, `Ctrl + Win + ←/→` | `Ctrl + ↑`, `Ctrl + ←/→` |
| **Copy / paste / undo, close & new tab, back/forward** | Ctrl / Alt based | Command based |
| **Zoom** | Ctrl + wheel | `Command + / Command -` |
| **Keyboard modifier caps** | Ctrl · Alt · Win | Ctrl · Opt · Cmd |
| **Shortcuts list** | Windows column | macOS column |

You can see and change what it thinks it is under **Settings → Computer**:

- **Auto** lets detection decide (the default).
- **macOS / Windows / Linux** pin it by hand, saved on the board. Use this if it
  is wrong — in particular, detection **cannot tell Linux from Windows**, because
  both enumerate the same way, so a Linux desktop is detected as "Windows" until
  you set it here.

> A pinned OS stays pinned when you move the board to a different computer. That
> is deliberate — your choice wins — but it is also how a board set up on a Mac
> ends up holding Command on a PC and opening the Start menu. When the pin and the
> attached computer disagree, **Settings → Computer** says so and offers Auto.

---

## Locking and unlocking a Mac

- **Lock** sends `Control + Command + Q`, the system lock on macOS Ventura and
  later. (`Win + L` does nothing on a Mac.)
- **Unlock** types the account password and presses Return. On a Mac, leave
  **Ctrl + Alt + Del off** — that combination is Windows-only. From the dashboard
  it is off by default; over serial or the API, send `"ctrl_alt_del": false`.
- The board tracks lock state **optimistically**. HID is one-way, so it cannot
  read whether the Mac is actually locked; it knows only what it last sent. If
  the two drift apart, correct it with `set_lock_state`.

---

## Limits on a Mac, honestly

- **US layout only.** The board types US-ANSI ASCII. If your Mac is set to a
  non-US keyboard layout, symbols and passwords will come out wrong. This is the
  project's biggest open problem and it is not Mac-specific.
- **Detection is a default, not a guarantee.** It is passive and reliable in
  practice, but if anything looks wrong, pin the OS under **Settings → Computer**
  — a manual choice always wins.
- **Give it a moment after connecting.** Detection settles about three seconds
  after the host enumerates the board. Until it does, **Settings → Computer**
  shows "Detecting…", and a lock fired in that window falls back to the PC
  shortcut (`Win+L`), which does nothing on a Mac. In normal use it has long since
  settled by the time you open the dashboard; if you ever see it, just lock again,
  or pin macOS so there is no wait at all.
- **A silent host reads as "Not identified".** If a computer enumerates the board
  without sending anything recognisable, the board says so rather than guessing.
  Pick an OS under **Settings → Computer** and it will stop asking.
- **Haptics.** iOS Safari has never implemented the web vibration API, so the
  dashboard's haptic ticks are silent on an iPhone regardless of the computer.

---

See also: [FLASHING.md](FLASHING.md), [HARDWARE.md](HARDWARE.md),
[COMMANDS.md](COMMANDS.md), and [SECURITY.md](../SECURITY.md).
