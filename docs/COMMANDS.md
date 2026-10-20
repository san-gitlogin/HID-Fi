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
| `set_auth` | `token`, `old`, `ap_password` | Sets the PIN; empty string disables it. Over WiFi, the **first** PIN also needs `ap_password`, the board's own WiFi password |
| `ap_password` | — | **USB only.** The board's WiFi name and password: `{"ssid", "password", "open", "generated"}`. Over WiFi it answers `{"status":"error","reply":"usb_only"}` |

HTTP requests may instead carry `token` inline in the body of any command.

**The first PIN over WiFi** (no PIN set yet, `token` not empty) is refused with
`{"status":"error","reply":"ap_password_required","message":...}` unless
`ap_password` matches the board's WiFi password. If the board's WiFi is open, the
first PIN can only be set by a client on that WiFi. Over serial nothing is needed.
Changing an existing PIN works as before, from a signed-in connection.

Wrong PINs are throttled **per device** (by IP address): one free retry, then 5s,
15s, 60s, then 5 minutes per attempt. A locked-out reply carries `retry_in` in
seconds. 20 wrong PINs from the home network within 10 minutes lock the home
network out for 10 minutes; clients of the board's own access point and the USB
cable are never part of that. A WebSocket that has not authenticated within 20
seconds of connecting (while a PIN is set) is closed. See
[SECURITY.md](../SECURITY.md).

---

## Keyboard

| Command | Parameters | Notes |
|---|---|---|
| `type` | `text`, `enter` | Types a string; `enter` presses Return afterwards. The reply's `skipped` counts characters that could not be typed (see below) |
| `press` | `keys` | A combo such as `CTRL+SHIFT+ESC` |
| `key_down` | `key` | Holds a key down |
| `key_up` | `key` | Releases it |
| `key_release_all` | — | Releases everything |
| `test_hid` | — | Types `HID_OK` to prove the USB side is alive |
| `set_unicode` | `os` (`mac`/`linux`), `on` | Says that computer is set up for Unicode entry, so emoji and accents are typed for real. Off by default; saved on the board |
| `unicode_test` | `os` (`mac`/`linux`) | Types `é😀` through that OS's Unicode entry whatever the setting, so a person can see whether it works. Refused (`status: error`) for any other OS, and on a computer the board has detected as a different one |

### Text that is not plain English

A USB keyboard sends key positions, never characters, so `type` decodes the
text as UTF-8 and does one of three things with each character:

| Character | What is typed |
|---|---|
| ASCII | The key, exactly as before |
| Phone punctuation - curly quotes, dashes, `…`, non-breaking spaces | The plain ASCII it stands for (`’` → `'`, `—` → `-`, `…` → `...`) |
| Anything else (emoji, accents, other scripts) | Through the host's Unicode entry when `set_unicode` is on for that OS: Option + hex on macOS with *Unicode Hex Input* selected, Ctrl+Shift+U + hex + Space on Linux (IBus/GTK). Otherwise it is skipped, and counted in `skipped` |

Bytes that are not well-formed UTF-8 are skipped and counted - never typed as
keys. The board never uses the macOS sequence on a computer it detected as a
PC (Option is Alt there), nor the Linux one on a detected Mac, even if the OS
is pinned by hand.

```json
{"cmd":"type","text":"what’s 😀"}
{"status":"ok","reply":"typed","length":12,"skipped":1,"enter":false}
```

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
| Keypad | `KP0`–`KP9` `KPDOT` `KPPLUS` `KPMINUS` `KPSTAR` `KPSLASH` `KPENTER` |

> The keypad names send the **keypad** HID usages, which are different keys from
> the number row. Applications that tell them apart — spreadsheets, CAD, games
> bound to keypad keys — see the difference. A single character like `"5"` always
> means the top-row digit.

> The board types **US-layout ASCII**. On other keyboard layouts symbols and
> passwords will come out wrong. This is the most valuable open problem in the
> project.

---

## Actions - for agents and scripts

Say what to do; the board presses the keys for the computer it is plugged into.
One call, one word, the same on macOS, Windows and Linux (GNOME).

| Command | Parameters | Reply |
|---|---|---|
| `actions` | `os` *(optional)*, `keys: true` *(optional)* | `a`: the action names that work on this computer, space separated (about 730 characters). With `keys`, an object name → combo. `also`: the step verbs. |
| `do` | `a` - an action name; `os` *(optional, to override detection)* | `done` with the `keys` it pressed and the `os`; or `unknown_action`, or `no_equivalent` when that OS has none |
| `open` | `app` - the name as you would type it in the OS's search | `opened`, `via`: `start` (Windows), `spotlight` (macOS) or `overview` (GNOME) |
| `open` | `run` - an exact command or URL | `opened`, `via: run` - Win+R on Windows, Alt+F2 on GNOME (a URL goes through `xdg-open`). `no_equivalent` on macOS, which has no run box |
| `steps` | `s` - an array of 1 to 32 steps | `steps_done` with `done`; or `steps_stopped` with `at` (the step it could not do) and `message` |

Steps, one per array item (an array, so text may contain any character):

| Step | Does |
|---|---|
| `do NAME` | an action, as `do` |
| `open APP` | as `open` with `app` |
| `run LINE` | as `open` with `run` |
| `type TEXT` | types the rest of the line |
| `key COMBO` | presses a combo, as `press` |
| `wait MS` | waits, up to 5000 per step and 10 s per call |
| `click left` / `right` / `middle` | clicks |

```json
{"cmd":"do","a":"screenshot_area"}
{"cmd":"open","app":"Calculator"}
{"cmd":"steps","s":["open Notepad","wait 1500","type Hello from HID-Fi","do save"]}
```

Every action is also a row of the dashboard's shortcut table, with the same keys
(`tests/static` checks it), and is taken from the vendor's own shortcut page.
Opening an app types into a launcher, so it depends on the launcher finding the
name: use the name the OS shows. The whole request must fit in about 900 bytes.
These commands need the access PIN like any other over WiFi.
See [AGENT_CONTROL.md](AGENT_CONTROL.md) for driving the board from an AI agent.

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
| `pc_save` | `name`, `password`, `slot`, `os` | **Refused unless an access PIN is set** |
| `pc_list` | — | Returns **names and OS only**, never passwords |
| `pc_delete` | `slot` | |

There is deliberately **no command that reads a password back**. `unlock` takes a
slot number and the board does the lookup itself.

> A **macro is not a secret store.** `macro_get` returns every step verbatim so
> the dashboard can edit them, and it is ungated over serial — a `type` step
> holding your password is readable by anyone with the cable. Use a PC slot or
> the password vault instead. See [SECURITY.md](../SECURITY.md).

Each slot carries the OS of **that** computer, which is not necessarily the one
attached now — a Mac login window is woken with a jiggle and a Shift, a PC with
Esc, and Esc on a Mac collapses the password field. `unlock` uses the slot's OS
when one is stored and falls back to detection when it is not.

- `os` is `mac` \| `windows` \| `linux` \| `auto`. Omit it on a new slot and the
  board stores whatever it is currently plugged into; omit it when editing and
  the stored value is kept. `auto` means "follow the attached computer".
- Passing an occupied `slot` **edits** it. `password` may then be omitted, which
  keeps the stored one — that is what lets the dashboard rename a PC or change
  its OS without being able to show the password back.

### Password vault

Secrets the board types for you. Twelve slots, each holding a purpose, a kind
(`password` or `pin`) and the secret itself — any length up to 128 characters, so
a PIN is not assumed to be four digits or even digits.

| Command | Parameters | Notes |
|---|---|---|
| `vault_list` | — | Purpose and kind only. Never the secret, and not its length |
| `vault_save` | `label`, `type`, `secret`, `slot` | Creates or edits. Refused unless an access PIN is set |
| `vault_get` | `slot`, **`pin`** | The one command that returns a secret |
| `vault_type` | `slot`, **`pin`**, `enter` | Types it on the host |
| `vault_delete` | `slot` | |
| `vault_pin` | `pin` **or** `access`, `new_pin` | Sets, changes or resets the vault's own PIN |
| `vault_wipe` | — | Destroys everything. **Not** gated |

**The two commands in bold need the PIN in that request, on every transport
including USB serial.** They are the only ways a secret leaves the board, and
they are the single exception to serial being ungated: serial is open so the
board can be recovered, not so it can be read. There is no session — typing a
stored secret asks again every time, because the keystrokes *are* the secret and
the board cannot see which window they land in.

Saving, renaming and deleting are **not** gated by the vault PIN. None of them
discloses anything, and asking for a PIN to store a password nobody can read
back buys nothing. Tampering is the accepted price; disclosure is not.

- The gate is the **vault PIN** when one is set, and the **access PIN**
  otherwise. Wrong guesses share the same escalating lockout as the access PIN,
  on their own counter, and it applies on serial too.
- `vault_save` on an occupied slot edits it; omit `secret` to keep the stored
  one. The dashboard is never given a secret to put back in the field. A
  `type:"pin"` entry is refused if the secret is not all digits.
- `vault_pin` takes **either** `pin` (the current vault PIN — changes it and
  keeps the entries) **or** `access` (the access PIN — a *reset*, which **erases
  the vault**). Without that second rule the access PIN would silently be a way
  to read everything and the separate PIN would be decorative. `new_pin` is four
  digits, or empty to go back to using the access PIN.
- `vault_wipe` is deliberately ungated. Destroying a secret reveals nothing, and
  it is the way back from a forgotten vault PIN.
- **`set_auth` erases the vault** unless you prove you knew the old PIN by
  passing `old`, or a separate vault PIN is in force. That is what stops ungated
  serial `set_auth` from being a way in. The dashboard passes `old` for you, so
  an ordinary PIN change keeps everything.

```json
{"cmd":"vault_get","slot":0,"pin":"1234"}
{"status":"ok","reply":"vault_secret","slot":0,"label":"Netflix","type":"pin","secret":"…"}
```

Denials come back as `vault_denied` (wrong or missing PIN), `vault_locked` (no
access PIN set, or too many wrong guesses — with `retry_in`).

### Host OS

The board works out whether it is plugged into a Mac or a PC by **watching how the
host enumerates it**, without typing anything.

> **Known limitation:** the current signal does not separate macOS from Windows,
> so a Mac reports as `windows` and must be pinned with `set_host_os`. The
> `usb_*` counters below are the raw evidence, reported so the discriminator can
> be chosen from measurements.

Detection re-runs every time the USB bus drops and comes back, so moving the
board from a Mac to a PC is picked up without a reboot.

| Command | Parameters | Notes |
|---|---|---|
| `set_host_os` | `os` — `mac` \| `windows` \| `linux` \| `auto` | Pins the OS (persisted) or, with `auto`, clears the override and re-arms detection |

`status` reports the current answer:

```json
{ "host_os": "mac", "host_os_source": "auto", "host_os_detected": "mac",
  "detect_phase": "settled", "usb_str_reqs": 9, "usb_str_rereads": 4,
  "usb_str_seq": "0,0,1,1,2,2,3,3,4,0,0,0,0,0,0,0",
  "usb_set_idle": 1, "usb_ctrl_reqs": 1, "usb_led_reports": 0 }
```

`host_os_source` is one of:

| Value | Meaning |
|---|---|
| `auto` | Decided from how the host enumerated the board |
| `manual` | Pinned with `set_host_os` |
| `pending` | A host is connected but the decision window has not closed yet |
| `undetermined` | The host enumerated but sent nothing recognisable — pick an OS by hand |

`host_os_detected` is what detection concluded **regardless of any manual pin**,
so the dashboard can warn you that a pin left over from another computer no
longer matches the one attached. The `usb_*` counters are the raw evidence behind
the verdict, which is what makes a detection argument settleable instead of
guesswork. `usb_str_rereads` is the discriminator: macOS reads each descriptor
string twice, two bytes then the whole thing, so the same index arrives back to
back, and at least half of its requests are repeats. Windows does it
occasionally — measured on Windows 11, 11 requests of which 2 were repeats — so
the verdict is the *ratio*, not the count. `usb_str_seq` is the raw sequence of
indices the host asked for, oldest first, so a wrong verdict can be diagnosed
from the phone.

Detection **cannot tell Linux from Windows** — they enumerate identically — so a
Linux host reads as `windows` until you pin it. A `host_os` event is also pushed
to WebSocket clients the moment it settles, so the dashboard reskins without
waiting for its next poll.

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

### Saved networks

The board remembers up to five networks, most recently used first, and joins
the strongest one in range - like a phone.

| Command | Parameters | Reply |
|---|---|---|
| `wifi_configure` | `ssid`, `password`, optional fixed IP | adds the network (or updates it) at the front and joins it. With five saved, the oldest is forgotten |
| `wifi_networks` | - | `networks`: `ssid`, `proven` (has worked), `static`, `current` (connected now), `refused` (wrong password - enter it again). Never a password. `max`: 5 |
| `wifi_forget` | `ssid` | `wifi_forgotten` with `left`. Forgetting the connected one leaves it, and the board looks for another |
| `wifi_disconnect` | - | forgets every saved network |

`status` adds `sta_known` (how many are saved), `sta_away` (none of them in
range) and `sta_scans`.

| Situation | What the board does |
|---|---|
| Not on a network | scans (about 2 s), then joins the strongest saved network it can see |
| None of them in range | scans every 30 s for ten minutes, then every two minutes, so its own WiFi stays smooth; every fourth round tries the most recent proven network unseen, for hidden networks |
| A new network's password is refused | not tried again until it is re-entered; the others still are |
| A new network fails for another reason | rests for five minutes; a network that has worked is preferred meanwhile |
| Upgrading from v3.10 | its one saved network becomes the first entry, still proven. The v3.10 settings keep mirroring the first entry, so a downgrade still finds it |

### Staying on the network

Once a network has worked (the board remembers that), the board never gives up
on it. Whenever it is saved and the board is not on it, the board rejoins: one
second after a drop, then backing off to every 30 seconds, for as long as it
takes. The core library's own auto-reconnect is switched off so only one thing
drives the radio.

| What happens | What the board does |
|---|---|
| the router drops the board | rejoins within seconds; `sta_drops` counts it |
| the router is off for a while | keeps its own WiFi up, `sta_phase` is `reconnecting`, rejoins within 30 s of the router coming back |
| the board starts before the router | keeps trying from boot - it no longer gives up after the first attempt |
| the router gives it a new address | `sta_prev_ip` and `sta_ip_changes` say so, an `sta_online` event announces it, mDNS moves to the new address |
| the access point was switched off to save power | it comes back 20 s after the home network goes, so the board is always reachable |
| the saved password stops working | after three refusals it asks every 5 minutes instead, and says so in `sta_error` |
| a new network's password is wrong | stops and says so: nothing to keep trying |

`status` carries the record:

| Field | Meaning |
|---|---|
| `sta_last_ip` | the last address held, kept while offline and across restarts |
| `sta_prev_ip` | the address before that, once it changed |
| `sta_ip_changes` / `sta_drops` | since the board started |
| `sta_down_s` | seconds without the home network, while it is away |
| `sta_retry_in_s` | seconds to the next attempt |
| `sta_reason` | why the router let the board go, e.g. `BEACON_TIMEOUT` (router gone) or `AUTH_FAIL` |

Pushed to every open dashboard: `{"event":"sta_online","ip":...,"changed":true,"prev_ip":...,"mdns":...}`
when the board is back (or at a new address), and `{"event":"ap_restored","why":"home network lost"}`.

**Finding the board after its address changed.** It always answers at
`http://esp-hid-XXXXXX.local/` (shown in Settings - bookmark that), it advertises
`_hidfi._tcp` so `python3 tools/hidfi.py find` lists it, its own WiFi is at
`http://192.168.4.1/`, and over USB `status` has `wifi_ip`. To keep the same
address, reserve it on your router for the MAC in Settings (`mac`). The board
cannot ask for its old address back after a restart: that option is compiled out
of the ESP32 core it is built on.

**Internet on the board's own WiFi.** A phone on the board's WiFi has no internet.
`{"cmd":"internet_share","on":true}` shares the home connection with it (NAPT),
and hands out the home network's DNS server so names resolve. It is off by default
and stored. Turning it on over WiFi needs an access PIN (`pin_required`
otherwise), because anything on the board's WiFi can then reach your home
network. It is active only while the board is on its home network
(`ap_share_active` in `status`; `waiting_for` in the reply says what it waits
for), stops during an outage, and resumes by itself. Phones already on the
board's WiFi must rejoin to receive the DNS server. The better habit is usually
to stay on the home WiFi and open the board by its address.

**Rehearsing it.** `net_fault` (USB only - refused over WiFi with `usb_only`)
makes the board behave as if the router misbehaved, without touching the router.
`tests/board/test_home_network.py` uses it.

| `kind` | Staged |
|---|---|
| `drop` | the router lets the board go |
| `vanish` with `s` (5-600) | the network is gone for `s` seconds; with `ssid`, only that saved network (another one can stay in range); with `at_boot: true` the board restarts as if the router were off when it started |
| `ap_off` | the access point switched off, as auto-off does |
| `ip_was` with `ip` | the board believes it last held `ip`, so the next join is a change |
| `clear` | ends a staged fault now |

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

`{"cmd":"echo","data":"..."}` answers `{"reply":"echo","data":"...","length":N}` with
the same text back - a round-trip check for a transport.

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
  "ap_open": false,
  "ap_pass_generated": true,
  "ap_pass_default": false,
  "pc_state": "unknown",
  "host_os": "mac",
  "host_os_source": "auto",
  "unicode_mac": false,
  "unicode_linux": false
}
```

`usb_suspended: true` means the host is asleep and reading nothing.

`ap_pass_generated` is true while the access point still uses the password the
board made for itself (v3.10); `ap_pass_default` is true if it is the old shared
`hid12345`. Neither ever carries the password - only `ap_password` over USB does.

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

`dashboards` lists every open dashboard socket, from either network - the number
the dashboard's header shows counts these:

| Field | Meaning |
|---|---|
| `ip` | the browser's address |
| `network` | `board` (its own WiFi) or `home` (the home network) |
| `signed_in` | signed in with the access PIN; always `false` while no PIN is set |
| `you` | this is the socket that asked |

A tab left open anywhere counts, and reconnects by itself within seconds of the
board restarting - so restarting the board does not clear the count; closing the
tab does. A socket whose device vanished is dropped by the heartbeat within about
40 seconds.

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

The binary frames below (keys, pointer, buttons, media, gamepad) from such a client
are dropped silently - they have no reply to carry an error. Before v3.9 they were
not, so the gate could be walked around by anything that spoke the binary
protocol. Only `0x04` ping is still answered.

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
