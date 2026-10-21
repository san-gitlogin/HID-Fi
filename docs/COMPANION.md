# HID-Fi Companion: see the computer's screen on your phone

HID-Fi types and moves the mouse on a computer; it cannot see that computer's
screen, because USB keyboards and mice only send. **HID-Fi Companion** is a small
app for the computer the board is plugged into. It shows that screen live on the
dashboard's **Screen** tab.

- The picture goes from the computer to your phone directly, encrypted. The
  board only introduces them, so its WiFi keeps carrying just the mouse and keyboard.
- It costs nothing while nobody watches: no capture, no encoding.
- It shows only the computer the board is plugged into, and stops the moment the
  board is unplugged.

---

## Before you start

1. **The board joins your home WiFi** (dashboard, Settings, Home network).
2. **The board has an access PIN** (Settings, Access PIN). Screens are never
   shared without one.
3. **Your phone is on the same WiFi as the computer.** On the board's own WiFi
   instead? Turn on Settings, Internet sharing, or join the home WiFi.

## Install

### macOS (12.3 or later)

1. Download `HID-Fi-Companion-…-macos.dmg` from the
   [latest release](https://github.com/san-gitlogin/HID-Fi/releases/latest).
2. Open it and drag **HID-Fi Companion** to **Applications**. Open it from there.
3. macOS asks to allow **Screen Recording** the first time a screen is shown.
   Choose **Open System Settings**, switch on HID-Fi Companion, and reopen the
   app if macOS asks you to.

It lives in the menu bar (no Dock icon) and starts at login.

### Windows 10 and 11 (64-bit)

In **PowerShell** (Start, type *PowerShell*):

```powershell
irm https://raw.githubusercontent.com/san-gitlogin/HID-Fi/main/companion/package/install-windows.ps1 | iex
```

Windows asks for administrator approval **once**, to let viewers on your home
network connect (a firewall rule for Private networks only). The icon appears by
the clock; Windows may hide it under the **^** arrow - drag it out to keep it visible.

Your WiFi must be set to **Private**: Settings, Network & internet, your WiFi,
Network profile type, Private.

### Linux (x86-64 and ARM64)

In a terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/san-gitlogin/HID-Fi/main/companion/package/install-linux.sh | sh
```

No `sudo`. It installs for your user, downloads the H.264 encoder from Cisco
(OpenH264, checked against its known checksum), and starts the app.

- **X11 (Xorg) sessions** work today. On a **Wayland** session, choose an
  "on Xorg" session at the login screen for now.
- **GNOME** shows tray icons only with the *AppIndicator and KStatusNotifierItem
  Support* extension (Ubuntu and Fedora include it). KDE, XFCE, Cinnamon and LXQt
  show them as they are. The pairing code also arrives as a notification.

## Pair (once)

1. On the dashboard, open **Screen**. The computer appears: *"… wants to share its screen"*.
2. Tap **Pair**. The computer shows a **six-digit code** (in a window on macOS and
   Windows, as a notification and in the tray menu on Linux).
3. Type the code on the phone and tap **Pair**.

The code proves the phone and the computer are talking to each other and not to
someone in between. Three wrong codes end the attempt; start again from the app.

## Watch

- **Screen tab**: the picture above the trackpad (phone upright) or beside it
  (wider screens). Pinch, double tap or the buttons to zoom.
- **Float** (window icon): keep the picture on top while you use other tabs. Drag
  it by its title, resize it from the corner, minimise or close it.
- **On its own** (arrow-out icon): a separate screen that only watches - a
  tablet, a TV's browser, a second monitor - while your phone controls. Or open
  `http://<board address>/#watch`. On a desktop: **F** full screen, **+** / **-**
  zoom, **0** fit.
- Up to **four screens** can watch one computer at once. A fifth waits and joins
  by itself when one closes.

## When something is not right

| What you see | What to do |
|---|---|
| "Set an access PIN to share screens" | Dashboard, Settings, Access PIN |
| "… is not reachable" | Start HID-Fi Companion on that computer |
| "This phone cannot reach …" (phone on the board's WiFi) | Join the computer's WiFi, or turn on Settings, Internet sharing |
| "Cannot reach … A firewall may be blocking" | Windows: run the installer again and approve the prompt; set the WiFi to Private. Linux: allow TCP 47800-47819 and UDP 47820-47859 (e.g. `sudo ufw allow 47800:47819/tcp` and `sudo ufw allow 47820:47859/udp`) |
| "HID-Fi is not plugged into …" | The board's **USB** port (not COM) goes into that computer |
| "Stopped for your safety" | The computer answered with a certificate the board did not vouch for. Remove it on the Screen tab and pair again |
| "Update the board's firmware to use screen sharing" (in the app) | Flash the latest firmware ([FLASHING.md](FLASHING.md)) |
| macOS: black picture | System Settings, Privacy & Security, Screen Recording: switch HID-Fi Companion on |
| Linux: "this is a Wayland desktop" | Log in with an Xorg session |
| Linux: no tray icon on GNOME | Install the AppIndicator extension; the code still comes as a notification |

The lock screen, the login window and the BIOS are not shown yet: the app runs
inside your user session. For those, an HDMI capture device is the route today.

## Remove

- macOS: Quit from the menu bar, delete the app from Applications.
- Windows: `.\install-windows.ps1 -Uninstall` (or run the install line with `-Uninstall`).
- Linux: `sh install-linux.sh --uninstall`.

Then remove the computer on the dashboard's Screen tab.

## Build it yourself

Rust 1.89 or newer: `cd companion && cargo build --release`. Details, tests and
the design: [companion/README.md](../companion/README.md),
[companion/DESIGN.md](../companion/DESIGN.md).
