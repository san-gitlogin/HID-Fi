# Architecture

How the firmware and dashboard fit together, and why a few odd-looking decisions
are the way they are.

---

## The shape of it

```
                         ┌──────────────────────────┐
  phone / laptop         │        ESP32-S3          │        the PC
                         │                          │
  dashboard  ──WiFi──────┤ HTTP :80   ─┐            │
                         │ WS   :81   ─┼─▶ dispatch ─┼──USB HID──▶ keyboard
  a script   ──USB───────┤ serial     ─┘            │             mouse
             (COM port)  │                          │             media
                         │            NVS           │             gamepad
                         └──────────────────────────┘
```

Three transports, one command dispatcher, one output. Every transport speaks the
same JSON and gets exactly one JSON object back.

---

## Why the WebSocket carries binary

This is the single most important performance decision in the project.

The ESP32 Arduino core's `WebServer` has **no HTTP keep-alive**. Every POST costs a
full TCP handshake — connect, send, respond, close. At 60 pointer events a second
that is 60 handshakes a second, and the cursor visibly trails the finger.

Pointer, joystick and knob traffic therefore goes out as **binary WebSocket
frames** on a persistent connection: a one-byte opcode and a fixed payload, with
no JSON parsing on either end and no reply expected.

Measured effect: serial round trip fell from 22.4 ms to about 15 ms, and the
cursor stopped falling behind.

Rules that keep it that way, and that new code must respect:

- Nothing on an input hot path allocates, logs, or awaits a reply.
- The browser checks `ws.bufferedAmount` before sending and **drops the frame** if
  the socket is backing up. Dropping a pointer sample is invisible; queueing one is
  lag that never recovers.
- Pointer deltas are accumulated and flushed **once per animation frame**, so a
  120 Hz touchscreen cannot outrun the socket.

---

## Files

| File | What it holds |
|---|---|
| `usb_hid_unlock/usb_hid_unlock.ino` | Everything: transports, dispatch, HID, NVS, WiFi |
| `usb_hid_unlock/web_ui.h` | The entire dashboard as one PROGMEM raw string |
| `flash_esp.ps1` | Build, flash, verify |

The dashboard is HTML, CSS, JavaScript and an SVG sprite inside a single C raw
string literal delimited `R"rawliteral( ... )rawliteral"`. It is compiled into the
firmware and served from flash, so **any UI change requires a reflash**.

That is why it is worth extracting the string and checking it in a browser before
flashing. A JavaScript syntax error produces a blank white page whose only
diagnosis is to reflash. See [CONTRIBUTING.md](../CONTRIBUTING.md).

---

## Two hardware constraints that shape the code

### Only one mouse object may ever exist

`USBHIDMouseBase` registers its HID descriptor in its **constructor**, behind a
class-wide `static bool`. Only the first instance ever registers.

Relative and absolute mice therefore cannot coexist. The mouse is heap allocated
at boot from a flag in NVS, and switching modes **reboots the board** so USB
re-enumerates. This is not a design preference; constructing both simply does not
work.

### The board cannot read the PC back

HID is one way. The board can send input and can never observe the result.

Everything the dashboard says about host state — mute, volume, whether the PC is
locked — is what we *believe* we set. This is why:

- The volume knob is an **endless encoder**, not a 0–100 dial. An encoder only
  sends up and down ticks, so it cannot drift out of step with reality. A dial
  showing "60%" would be a claim the board cannot support.
- Mute is a latched **intent**, shown clearly enough that a desync is obvious.
- Lock state is tracked optimistically and can be corrected with `set_lock_state`.

New features should stay honest about this rather than inventing state.

---

## Storage

Settings live in ESP32 NVS across three namespaces:

| Namespace | Contents |
|---|---|
| `hid_cfg` | Pointer mode, gamepad enabled, access PIN, verbose flag, power mode, AP auto-off, dashboard layout |
| `wifi_cfg` | Home network credentials, static address settings, access point name and password |
| `macros` | Eight macro slots |
| `pcprof` | Saved PC names and passwords — write and use only, never read back |

**`flash_esp.ps1` writes four partitions and deliberately leaves NVS alone**, so
settings survive a firmware upgrade. Only `-Erase` clears them.

This is also why the script does not flash `merged.bin`: that file is a full 16 MB
image and writing it at `0x0` destroys NVS along with everything else.

Because the dashboard layout is stored on the board rather than in the browser,
every phone that opens it sees the same knobs, quick actions and macros.

---

## Authentication

The access PIN gates the two wireless transports. Serial is never gated.

Every path that tests a PIN funnels through one function, `pinCheck()`, which owns
the failure counter and the lockout deadline. That matters: the HTTP API accepts a
PIN inline in the request body, and an earlier version tested it without counting
the attempt — so the lockout could be bypassed by POSTing guesses instead of using
`auth`. If you add a transport, route it through the same function.

A request carrying no token at all is not counted, so an ordinary unauthenticated
page load cannot lock out the legitimate user.

### The exposure gate

Authentication answers *who*. This answers *from where*, and it is a separate
question that is easy to conflate.

Access-point clients have to be in radio range. Clients arriving over a joined
home network do not — every device on that subnet can reach the board. So when
the board is on a network with **no PIN set**, commands from outside the softAP
subnet are refused with `lan_locked`. Read-only commands and `set_auth` stay open,
so the user can fix it from where they are.

The subnet test is a straight comparison of the first three octets against
`WiFi.softAPIP()`. That is not a security boundary on its own — a determined
attacker on the LAN could spoof a source address — but it is not being used as
one. It decides *how loudly to insist on a PIN*, and the PIN is the actual
control.

---

## Nothing in `loop()` may block

This is the single most important rule in the firmware, and it has been broken
twice.

`loop()` services the WebSocket, the web server, the join state machine and the
pointer stream. Anything that blocks there freezes the dashboard, and because the
socket heartbeat also stops, a long enough block drops the connection outright —
usually at the exact moment the user is staring at the screen waiting for feedback.

Two functions used to block for seconds at a time:

| Was | Now |
|---|---|
| `connectWifi()` spun for up to 15 s waiting to associate | `staBeginJoin()` starts it, `updateStaJoin()` advances it from `loop()` |
| `WiFi.scanNetworks()` blocked 2–4 s | `WiFi.scanNetworks(true)` plus polling on `/api/scan` |

Both now return immediately and report progress through status fields the
dashboard polls. `handleApConfigure()` follows the same principle from the other
direction: it defers the AP restart by 300 ms so its own reply can reach the
client before the network disappears underneath it.

If you add anything that waits, make it a state machine.

---

## Power

Almost all of the board's draw is the radio: roughly 100–120 mA awake against
30–40 mA with modem sleep. CPU frequency scaling is deliberately not offered —
it saves far less and changes the clock the UART derives from, which would risk
the serial link for a few milliamps.

The tension cannot be designed away. Modem sleep parks the radio between beacons,
which is exactly the jitter the binary WebSocket work existed to remove. So the
policy avoids making the user choose once and live with it:

- `PWR_PERFORMANCE` — always awake.
- `PWR_BALANCED` *(default)* — awake whenever a WebSocket client is connected,
  asleep after 45 s with none. An open dashboard means intent to use it, so this
  costs nothing anyone can feel.
- `PWR_SAVER` — always asleep, and it shows.

`noteActivity()` is called from the binary hot path. It is one store and one
already-true comparison in the common case, which is why it can afford to be
there.

The larger saving is `ap_auto_off`: an access point beacons whether or not anyone
is listening, so shutting it down once the board is reachable over the home
network saves more than radio sleep does — and removes a way in. It is opt-in
because it removes a way in, and it only ever triggers when the board is provably
reachable another way. **Holding BOOT for two seconds always restores it**, which
is the guaranteed recovery path when nothing else works.

---

## Memory

The board runs for weeks at a time, so the failure mode that matters is slow
exhaustion, not a single bad allocation. Two habits keep it honest:

- **Bound everything that arrives from outside.** WebSocket text frames are capped
  at `WS_TEXT_MAX`, serial lines at `SERIAL_LINE_MAX`. A frame is rejected before
  it is copied — copying it to discover it was too big is the exhaustion.
- **Track the floor, not the level.** `heap_floor` in status is the lowest free
  heap since boot. Current free heap recovers, so it hides a leak that already
  happened.

The text-frame path used to `malloc`, `memcpy`, construct a `String` and `free` —
two allocations per command. It now uses the `String(buf, len)` constructor for
one. `WiFi.scanDelete()` is called on every completed scan, which also makes the
next request start a fresh one.

---

## Safety nets

Two failure modes would leave the user's PC unusable, so both are handled
explicitly:

- **A stuck modifier.** The app switcher holds Alt down across many requests. If
  the phone locks or the tab dies, that Alt would stay down forever. The firmware
  releases all keys and mouse buttons when the **last** WebSocket client
  disconnects, and the dashboard releases on page hide and after an idle timeout.
- **A stuck mouse button.** The grab gesture holds the left button. Same net.

The dashboard also sends `key_release_all` on every fresh connection, so a session
that died mid-chord cannot poison the next one.
