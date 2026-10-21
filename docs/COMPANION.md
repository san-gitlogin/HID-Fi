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
3. Its window opens and walks you through three steps. It lives in the menu bar
   (no Dock icon) and starts at login.

**What macOS will ask, and what to choose.** These come from macOS, not from
HID-Fi; each is asked once unless noted.

| When | macOS says | Choose |
|---|---|---|
| First start | "…would like to find and connect to devices on your local network" | **Allow**. It is how the app reaches the board and your phone. |
| First start | "Background Items Added" (a notification) | Nothing. It is the start-at-login entry; the menu bar icon can turn it off. |
| You click *Allow Screen Recording* | "…would like to record this computer's screen" | **Open System Settings**, then switch on **HID-Fi Companion** under Screen & System Audio Recording. |
| Right after switching it on | "…may not be able to record the contents of your screen until it is quit" | **Quit & Reopen**. The app comes back by itself, still paired. |
| The first time a screen is shown (macOS 15 and later) | "…is requesting to bypass the system private window picker and directly access your screen and audio" | **Allow**. macOS asks this again about once a month. The app never records audio; the wording is macOS's. |
| Opening a download that Apple has not notarized | "Apple could not verify…" | System Settings, Privacy & Security, **Open Anyway**. Release builds are notarized; this is for builds of your own. |

An app you build yourself is signed only for your Mac, so macOS forgets the
Screen Recording switch whenever you rebuild it: switch it on again.

### Windows 10 and 11 (64-bit)

In **PowerShell** (Start, type *PowerShell*):

```powershell
irm https://raw.githubusercontent.com/san-gitlogin/HID-Fi/main/companion/package/install-windows.ps1 | iex
```

Windows asks for administrator approval **once**, to let viewers on your home
network connect (a firewall rule for Private networks only). The icon appears by
the clock; Windows may hide it under the **^** arrow - drag it out to keep it visible.
If you skip the approval, Windows Firewall asks the first time a viewer connects:
tick **Private networks** and choose **Allow access**.

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
| "… is not reachable" | Start HID-Fi Companion on that computer: macOS from Applications (it lives in the menu bar), Windows from the Start menu (its icon sits by the clock), Linux from the applications menu |
| The app says "HID-Fi is plugged in, but this computer cannot reach it" | Is the board on the same WiFi as the computer? Dashboard, Settings, Home network. On macOS, if you chose Don't Allow when it asked to find devices on your network: System Settings, Privacy & Security, Local Network, switch on HID-Fi Companion (the app's "Open network settings…" button goes there). On Windows, set the WiFi to Private |
| Linux: no icon and no notifications at all | The desktop has no tray and no notification service (GNOME without the AppIndicator extension, some minimal desktops). The app still runs and still shows up on the dashboard's Screen tab - pair from there |
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
