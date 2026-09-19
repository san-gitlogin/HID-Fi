# Command reference

Every interface speaks the same JSON. One command in, exactly one JSON object out.

| Transport | How |
|---|---|
| **Serial** | 115200 baud on the **COM** port, one JSON object per line |
| **HTTP** | `POST http://192.168.4.1/api/command` with a JSON body |
| **WebSocket** | port `81`, JSON text frames, plus binary frames for hot paths |

If an access PIN is set, HTTP and WebSocket requests must authenticate.
**Serial never requires a PIN and is never locked out** — that is the deliberate
way back in.

```bash
# serial
echo {"cmd":"status"} > COM13

# http
curl -X POST http://192.168.4.1/api/command \
     -H "Content-Type: application/json" \
     -d '{"cmd":"status"}'
```

---

## Authentication

| Command | Parameters | Notes |
|---|---|---|
| `auth` | `token` | Authenticates this WebSocket client |
| `set_auth` | `token` | Sets the PIN; empty string disables it |

HTTP requests may instead carry `token` inline in the body of any command.

Wrong PINs are throttled: one free retry, then 5s, 15s, 60s, then 5 minutes per
attempt. A locked-out reply carries `retry_in` in seconds. See
[SECURITY.md](../SECURITY.md).

---

## Keyboard

| Command | Parameters | Notes |
|---|---|---|
| `type` | `text`, `enter` | Types a string; `enter` presses Return afterwards |
| `press` | `keys` | A combo such as `CTRL+SHIFT+ESC` |
| `key_down` | `key` | Holds a key down |
| `key_up` | `key` | Releases it |
| `key_release_all` | — | Releases everything |
| `test_hid` | — | Types `HID_OK` to prove the USB side is alive |

### Key names

`press` splits on `+`, upper-cases each token, and looks it up. A single character
that is not a known name is pressed literally, so `CTRL+,` and `GUI+.` work. A
literal `+` cannot appear as a token — that is why `PLUS` exists as a name.

**Anything not in this list silently does nothing**, because `press` does not
report unknown keys. `key_down` does validate and will return an error.

| Group | Names |
|---|---|
| Modifiers | `CTRL` `ALT` `SHIFT` `GUI` (`WIN` `CMD` `COMMAND` `SUPER` `META` `OPTION`) |
| Right-hand | `RCTRL` `RSHIFT` `RALT` (`ALTGR`) `RGUI` |
| Editing | `ENTER` `ESC` `TAB` `SPACE` `BACKSPACE` `DELETE` `INSERT` `HOME` `END` `PAGEUP` `PAGEDOWN` `CAPSLOCK` |
| Arrows | `UP` `DOWN` `LEFT` `RIGHT` |
| Function | `F1`–`F12` |
| System | `PRINTSCREEN` (`PRTSC`) `MENU` (`APPS`) `PAUSE` `SCROLLLOCK` `NUMLOCK` |
| Symbols | `PLUS` `MINUS` `EQUALS` |

> The board types **US-layout ASCII**. On other keyboard layouts symbols and
> passwords will come out wrong. This is the most valuable open problem in the
> project.

---

## Mouse

| Command | Parameters |
|---|---|
| `mouse_move` | `dx`, `dy`, `wheel`, `pan` |
| `mouse_click` | `button` (`left`/`right`/`middle`), `count` |
| `mouse_press` | `button` |
| `mouse_release` | `button` |
| `mouse_scroll` | `amount` |
| `mouse_abs` | `x`, `y` — absolute pointer mode only |
| `pointer_mode` | `mode` (`relative`/`absolute`), `reboot` |

Switching pointer mode **reboots the board**, because only one mouse object can
ever exist — see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Media, system and gestures

| Command | Parameters |
|---|---|
| `media` | `key` or `usage` — `volume_up`, `mute`, `play_pause`, `next`, `brightness_up`, … |
| `system` | `action` — `sleep`, `wake`, `power_off` |
| `gesture` | `name` — `switch_app`, `task_view`, `desktop_left`, `back`, `zoom_in`, … or `keys` |
| `presenter` | `action` — `next`, `prev`, `start`, `end`, `black`, `white` |

### Waking a sleeping host

A sleeping PC **suspends the USB bus and reads nothing** — no keystroke, click or
System Wake usage reaches it. The firmware calls TinyUSB's `tud_remote_wakeup()`
before input whenever it sees the bus suspended.

That only works if the host armed the device as a wake source, so
`{"cmd":"system","action":"wake"}` reports both:

```json
{ "reply": "system_sent", "action": "wake",
  "was_suspended": true, "remote_wakeup": false }
```

`remote_wakeup: false` while `was_suspended: true` means the host refused. Enable
*Allow this device to wake the computer* on the HID keyboard in Device Manager,
and USB wake in the BIOS.

Note also that if the host cuts VBUS while asleep, the board loses power entirely.
Power the COM port separately if you need the dashboard to stay up.

---

## Session

| Command | Parameters | Notes |
|---|---|---|
| `unlock` | `password` **or** `profile`, `ctrl_alt_del`, `force` | Types the password and presses Enter |
| `lock` | `force` | `Win+L` on a PC, `Ctrl+Cmd+Q` on a Mac (see `set_host_os`) |
| `set_lock_state` | `state` | Tells the board what it should believe |
| `wiggle` | `mode`, `amplitude`, `interval_ms` | Net-zero cursor jiggle |
| `wiggle_stop` | — | |

`unlock` with `ctrl_alt_del: false` works on macOS — Ctrl+Alt+Del is Windows only.

### Saved PC passwords

| Command | Parameters | Notes |
|---|---|---|
| `pc_save` | `name`, `password`, `slot` | **Refused unless an access PIN is set** |
| `pc_list` | — | Returns **names only**, never passwords |
| `pc_delete` | `slot` | |

There is deliberately **no command that reads a password back**. `unlock` takes a
slot number and the board does the lookup itself.

### Host OS

The board works out whether it is plugged into a Mac or a PC by toggling Num Lock
and watching for the host's LED reply — Windows and Linux have a Num Lock and echo
it, macOS does not. That result steers the lock shortcut, the gesture combos, the
app-switcher modifier and the dashboard keyboard.

| Command | Parameters | Notes |
|---|---|---|
| `set_host_os` | `os` — `mac` \| `windows` \| `linux` \| `auto` | Pins the OS (persisted) or, with `auto`, clears the override and re-runs the probe |

`status` reports the current answer:

```json
{ "host_os": "mac", "host_os_source": "auto" }
```

`host_os_source` is `auto` (from the Num Lock probe), `manual` (pinned with
`set_host_os`) or `pending` (not yet decided). The probe **cannot tell Linux from
Windows** — both have a Num Lock — so a Linux host reads as `windows` until you
pin it. A `host_os` event is also pushed to WebSocket clients the moment the probe
settles, so the dashboard reskins without waiting for its next poll.

---

## Configuration

| Command | Parameters | Notes |
|---|---|---|
| `ap_configure` | `ssid`, `password`, `open` | Restarts the access point 300 ms after replying |
| `wifi_configure` | `ssid`, `password`, `ip`, `gateway`, `subnet`, `dns` | Returns immediately, joins in the background |
| `wifi_status` / `wifi_disconnect` | — | |
| `power_mode` | `mode`, `ap_auto_off` | `performance` \| `balanced` \| `saver` |
| `gamepad_enable` | `on`, `reboot` | Adds or removes the controller device |
| `gamepad` | `lx`, `ly`, `rx`, `ry`, `hat`, `buttons` | |
| `ui_save` / `ui_get` | `data` | Dashboard layout, stored on the board |
| `macro_save` / `macro_get` / `macro_list` / `macro_run` / `macro_delete` | `slot`, `name`, `steps` | 8 slots |
| `set_verbose` | `on` | Echo every source to serial |
| `storage_info` | — | What is kept in the board's permanent memory |
| `clients` | — | Everything currently joined to the access point |
| `led_set` / `led_blink` / `led_status` | `color` | **Does nothing unless the board's RGB pads are bridged** |

`ap_configure` treats the password three ways, and the difference matters:

| You send | What happens |
|---|---|
| `"password":"something"` | That becomes the new password (8+ characters) |
| `"open":true` | The password is **removed** and the network becomes open |
| neither | The current password is **kept** |

An omitted password used to silently open the network. It no longer does — taking
security off is now something you have to ask for explicitly.

The restart is deferred slightly so the reply reaches the client before the
network it arrived on disappears.

### SSID rules

An SSID is arbitrary bytes on the wire, so **emoji and accented characters work**.
The limit that matters is **32 bytes, not 32 characters** — one emoji uses four of
them, so `HomeWiFi` (8 bytes) and four emoji (16 bytes) are very different
lengths from the radio's point of view. The dashboard shows a live byte count.

Rejected with a clear message:

| Condition | Why |
|---|---|
| over 32 bytes | 802.11 caps the field |
| invalid UTF-8 | would render as garbage on every device that sees it, and would corrupt this board's JSON |
| control characters | no device can display or retype them |
| password 1–7 characters | WPA2 minimum is 8 |
| password over 63 bytes | WPA2 maximum |

### Joining a network

`wifi_configure` does **not** wait for the join. It saves the credentials, starts
the attempt and replies at once:

```json
{"status":"ok","reply":"wifi_connecting","phase":"connecting",
 "ip_mode":"static","timeout_ms":15000,"ap_still_active":true}
```

Poll `wifi_status` to follow it. `sta_phase` moves through `connecting` to either
`online` or `failed`, and `sta_error` carries a plain-language reason. The access
point stays up throughout, so a failed join can never lock you out.

Static addressing is optional. `ip` and `gateway` are both required if either is
given; `subnet` defaults to `255.255.255.0` and `dns` to the gateway.

Nothing on the ESP32 can detect that a static address is already taken — a static
client never asks anyone's permission, so there is no server to refuse it. Two
things are checked instead:

- after association, the address actually held is compared with the one asked for,
  which catches a router that overrides it
- on timeout the board retries once on DHCP, which catches a duplicate or
  out-of-subnet address

`sta_ip_mode` reports which happened: `static`, `dhcp` or `dhcp_fallback`.

### Power

```json
{"cmd":"power_mode","mode":"balanced"}
{"cmd":"power_mode","ap_auto_off":true}
```

| Mode | Radio | Effect |
|---|---|---|
| `performance` | always awake | Lowest latency, ~100-120 mA |
| `balanced` *(default)* | awake while a client is connected | No felt difference, sleeps when idle |
| `saver` | always asleep | ~30-40 mA, visible pointer stutter |

`ap_auto_off` stops the access point once the board is on a home network and
nobody has used the AP for ten minutes. This is where the real saving is, because
an access point beacons whether or not anyone is listening — and it removes a way
in. **Holding BOOT for two seconds always brings it back.**

CPU frequency scaling is deliberately not offered. It saves far less than the
radio does and it changes the clock the UART is derived from, which would put the
serial link at risk for a few milliamps.

---

## Scanning

`GET /api/scan` starts an asynchronous scan and returns straight away:

```json
{"status":"ok","reply":"scan","scanning":true}
```

Keep asking until `scanning` is `false`, then read `networks`. Calling it again
after results are returned starts a fresh scan. If a join is in flight the reply
carries `"busy":true` rather than disturbing the association.

---

## Status

`{"cmd":"status"}` returns everything:

```json
{
  "firmware": "hid_fi_v3.6",
  "uptime_sec": 412,
  "free_heap": 200932,
  "heap_floor": 188104,
  "heap_low": false,
  "hid_ready": true,
  "usb_mounted": true,
  "usb_suspended": false,
  "power_mode": "balanced",
  "radio_awake": true,
  "ap_auto_off": false,
  "ap_running": true,
  "lan_exposed": false,
  "sta_phase": "online",
  "sta_ip_mode": "static",
  "sta_error": "",
  "wifi_quality": "good",
  "ap_ssid": "ESP32-HID-XXXXXX",
  "ap_ip": "192.168.4.1",
  "ap_clients": 1,
  "channel": 6,
  "mac": "DC:B4:D9:09:F7:C8",
  "ap_mac": "DC:B4:D9:09:F7:C9",
  "ws_clients": 1,
  "pointer_mode": "relative",
  "gamepad": false,
  "auth_set": true,
  "pc_state": "unknown",
  "host_os": "mac",
  "host_os_source": "auto"
}
```

`usb_suspended: true` means the host is asleep and reading nothing.

`channel` is the radio channel. There is only one radio, so the access point and
the home network are always on the same one — joining a router drags the access
point onto that router's channel, which is why a phone connected to the AP can
briefly drop during a join.

`heap_floor` is the lowest free heap since boot. Current free heap recovers, so it
hides a leak that already happened; the floor does not.

`lan_exposed: true` means the board is on a home network with no PIN set — see
the exposure gate below.

The two MACs are different hardware addresses on the same radio. `mac` is the
station side — the one your router's DHCP table and any MAC filter will show.
`ap_mac` is the access point side, the one a phone sees when it joins the board.

---

## Storage

`{"cmd":"storage_info"}` reports what survives a power cycle:

```json
{
  "status": "ok", "reply": "storage_info",
  "nvs": {"used_entries": 196, "free_entries": 434,
          "total_entries": 630, "percent_used": 31, "namespaces": 4},
  "hid_cfg":  {"pointer_mode": "relative", "gamepad": false, "pin_set": true,
               "power_mode": "balanced", "ap_auto_off": false, "ui_bytes": 412},
  "wifi_cfg": {"sta_ssid": "HomeWiFi", "sta_pass_set": false,
               "ap_ssid": "HID-fi", "ap_pass_set": true, "static_ip": ""}
}
```

**Passwords and PINs are reported as booleans only.** `pass_set: true` tells you a
secret exists; nothing on this board will hand you the secret itself. Storage is
NVS, in four namespaces: `hid_cfg`, `wifi_cfg`, `macros`, `pcprof`.

## Connected clients

`{"cmd":"clients"}` lists what is joined to the access point:

```json
{
  "status": "ok", "reply": "clients", "count": 1, "attached": 1,
  "clients": [{"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.4.2",
               "rssi": -47, "quality": "excellent", "dashboard": true}],
  "note": "Device names are not shown..."
}
```

The MAC and signal come from the WiFi driver. The IP is looked up in the ARP
table, so it stays empty for a station that has associated but not yet spoken IP.
`dashboard: true` means that address currently holds a WebSocket to the dashboard.

**Device names are not available, and that is not a bug we can fix.** A WiFi client
is never obliged to tell an access point its name, and modern phones deliberately
present a randomised MAC as well — so the address you see may not match the one
printed on the device.

---

## The exposure gate

On the board's own access point, an attacker has to be within radio range. On a
home network, every device on that network can reach the board, including anything
already compromised and anything a guest brought in.

So when the board is joined to a network and **no PIN is set**, commands arriving
from outside the AP subnet are refused:

```json
{"status":"error","reply":"lan_locked",
 "message":"Set an access PIN before using this board over your home network..."}
```

`status`, `ping`, `wifi_status`, `ui_get`, `auth` and `set_auth` stay available, so
the PIN can actually be set from where the user is standing. Access-point clients
are unaffected, and USB serial is never gated.

---

## WebSocket binary opcodes

Little-endian, client to board. These carry the pointer, joystick, knob and
keyboard streams with no JSON parsing and no reply.

| Op | Payload | Meaning |
|---|---|---|
| `0x01` | `i16 dx, i16 dy, i8 wheel, i8 pan` | Relative move and scroll |
| `0x02` | `u8 mask, u8 action, u8 count` | Buttons — action 0 release, 1 press, 2 click |
| `0x03` | `u16 x, u16 y` | Absolute move, absolute mode only |
| `0x04` | `u32 id` | Ping, answered with `0x81` and the same id |
| `0x05` | `i8 lx, i8 ly, i8 rx, i8 ry, u8 hat, u32 buttons` | Gamepad state |
| `0x06` | `u16 usage` | Consumer control, e.g. volume |
| `0x07` | `u8 down, key name bytes` | Key down (1) or up (0) |
| `0x81` | `u32 id` | Pong, board to client |

`0x07` carries the **key name**, not a resolved HID code — `"a"`, `"ENTER"`,
`"CTRL"`. That keeps `mapKeyName()` as the single authority and means the
dashboard never has to hold its own copy of the keymap, which would drift. Names
are capped at 22 bytes; anything longer falls back to the JSON `key_down` /
`key_up` commands, as does any client whose socket is down or unauthenticated.
