# ESP32-S3 USB-HID Remote, Explained

This is a technical explanation of the project in simple language. The short
version is:

> The ESP32-S3 is a small translator. Your phone sends it instructions over
> WiFi, and it repeats those instructions to the PC as if it were a real USB
> keyboard, mouse, media remote, or gamepad.

The PC does not need an application or a driver. It only sees standard USB HID
(Human Interface Device) reports.

## 1. The five-year-old picture

Imagine a little robot with three doors and one hand:

```text
phone dashboard -- WiFi ---------+ \
serial script -- COM USB ---------+  ESP32-S3 -- native USB HID --> PC
HTTP client ----------------------+ /
```

- The **phone** is a remote control with buttons and a trackpad.
- The **ESP32-S3** is the robot that understands the remote control messages.
- The **native USB port** is the robot's hand. It presses keys and moves the
  mouse for the computer.
- The **PC** thinks an ordinary keyboard and mouse are connected.

There are two USB-C ports because they have different jobs:

- **COM** is the serial doorway used for flashing, debugging, and commands.
- **USB** is the ESP32-S3's real USB device doorway used to control the PC.

A common mistake is to flash through COM and then expect the COM connection to
make the PC see a keyboard. It will not. The PC must also be connected to the
native USB port.

## 2. What happens when the board starts

The main firmware is [usb_hid_unlock.ino](usb_hid_unlock/usb_hid_unlock.ino).
Its `setup()` function does roughly this:

1. Start the CH343 serial connection at 115200 baud.
2. Read saved settings from ESP32 NVS storage.
3. Create exactly one mouse object, either relative or absolute.
4. Start the keyboard, mouse, media, system-control, and optional gamepad HID
   devices.
5. Start WiFi, the HTTP server on port 80, and the WebSocket server on port 81.
6. Announce a JSON `boot` event over serial.

The PC needs time to enumerate the USB devices. Enumeration is the USB
conversation where the PC asks, "What are you?" The firmware answers with HID
descriptors that describe its keyboard, mouse, and other controls.

After startup, `loop()` repeatedly:

- reads complete JSON lines from serial;
- services the WebSocket before slower work, so pointer input stays responsive;
- services HTTP requests;
- checks the boot button, background cursor wiggle, and LED blinking;
- performs deferred actions such as releasing a media key or rebooting.

The loop has only a tiny final delay. Sleeping for a long time here would add
cursor latency.

## 3. The one-command pipeline

All three input paths eventually use the same command dispatcher:

```text
serial JSON line       \
HTTP POST JSON           > processCommand() -> command handler -> USB HID report
WebSocket JSON text    /
```

For example, this command:

```json
{"cmd":"press","keys":"CTRL+ALT+DELETE"}
```

is parsed by ArduinoJson. The dispatcher selects the `press` handler. The
handler maps each key name to a HID key code, calls `Keyboard.press()`, and
releases the keys. The PC receives the same kind of key reports it would
receive from a physical keyboard.

The same idea works for other commands:

```json
{"cmd":"mouse_move","dx":40,"dy":-10}
{"cmd":"media","key":"volume_up"}
{"cmd":"gamepad","button":0,"pressed":true}
```

The command reference is in [docs/COMMANDS.md](docs/COMMANDS.md). The important
architecture rule is that the transport should not implement its own separate
version of keyboard or mouse behavior. It should feed the common command path.

## 4. Why USB HID is useful

HID is a standard language for input devices. A keyboard report says which keys
are currently down. A mouse report contains movement, wheel, pan, and button
bits. A consumer-control report represents things such as volume or play/pause.

Because the ESP32 speaks this standard directly over its native USB peripheral:

- the PC does not need an installed agent;
- the PC does not need administrator permission;
- the device can work before a user logs in, including at a lock screen;
- the PC's existing keyboard and mouse drivers handle the reports.

The firmware uses the Arduino ESP32 USB classes:

- `USBHIDKeyboard`
- `USBHIDRelativeMouse` or `USBHIDAbsoluteMouse`
- `USBHIDConsumerControl`
- `USBHIDSystemControl`
- optional `USBHIDGamepad`

### Relative versus absolute mouse mode

A relative mouse says, "move 20 pixels right." An absolute mouse says, "put the
pointer at coordinate 16384, 16384 in a 0..32767 space."

The project stores the selected mode and creates only the matching mouse object.
That is required by the ESP32 USB HID implementation: the mouse base registers
its descriptor in the constructor, and only the first mouse instance can
register. Changing mode therefore saves the setting and reboots so USB can
enumerate a different device layout.

Large relative movements are split into multiple HID reports. The HID relative
axis is a signed byte, so one report cannot represent more than 127 in one
direction. A request for `dx=400` becomes several smaller reports rather than
being clipped to 127.

## 5. Why the browser uses WebSocket binary frames

A normal HTTP request is excellent for occasional commands such as changing a
setting. It is inefficient for a trackpad because the firmware's HTTP server
does not keep the connection alive. Repeated HTTP pointer commands would keep
paying connection and parsing overhead.

The dashboard therefore opens one persistent WebSocket connection to port 81.
It uses two formats:

- **JSON text frames** for normal commands, replies, authentication, and status.
- **Small binary frames** for high-frequency pointer, gamepad, and media input.

The move frame is seven bytes:

```text
byte 0       opcode 0x01
bytes 1..2   signed 16-bit dx, little-endian
bytes 3..4   signed 16-bit dy, little-endian
byte 5       signed 8-bit wheel
byte 6       signed 8-bit horizontal pan
```

That means the hot path does not need HTTP headers, JSON parsing, a reply, or a
serial echo. On the firmware side, `onWsEvent()` reads the opcode and calls
`hidMouseMove()` directly.

The browser also protects responsiveness in two ways:

1. It accumulates tiny touch movements instead of sending every raw browser
   event.
2. It flushes at most once per animation frame with `requestAnimationFrame()`.
3. If `ws.bufferedAmount` is too large, it drops a pointer sample. A slightly
   inaccurate sample is harmless; an old queued sample makes the cursor feel
   permanently behind the finger.

This is a useful real-time rule: for pointer movement, freshness matters more
than delivering every sample.

## 6. How the trackpad becomes gestures

The dashboard is one HTML/CSS/JavaScript document stored in
[web_ui.h](usb_hid_unlock/web_ui.h) as a C raw string in flash. The phone runs
that JavaScript after the ESP32 serves it.

The browser receives pointer events and keeps a small state machine:

- one finger: move, tap, double tap, or drag;
- two fingers: scroll, horizontal pan, right click, or pinch zoom;
- three fingers: desktop switching, task view, or a three-finger drag.

For a one-finger move, the browser:

1. calculates the new delta;
2. applies sensitivity and optional velocity-based acceleration;
3. adds the result to an accumulator;
4. lets the animation-frame flush send the next binary move frame.

Momentum uses another animation-frame loop. It gradually reduces velocity and
adds more movement until it becomes small enough to stop.

The firmware does not need to understand finger geometry. It receives either a
mouse movement, a button action, or a named higher-level gesture such as
`desktop_left`.

## 7. WiFi, authentication, and storage

The ESP32 starts an access point so a phone can connect directly to it. It can
also join another WiFi network as a station. The dashboard normally connects to
the access point address `192.168.4.1`.

Wireless commands can be protected by a four-digit access PIN:

- WebSocket clients authenticate once and become authorized for that connection.
- HTTP commands can include the token in the request body.
- Serial commands are deliberately not PIN-gated because serial requires a
  physical cable and is the recovery path.
- All PIN checks go through `pinCheck()`, which owns failed-attempt counting and
  lockout timing.

The project stores settings in ESP32 NVS, which is persistent key-value storage.
The namespaces separate concerns:

| Namespace | Examples |
| --- | --- |
| `hid_cfg` | pointer mode, gamepad flag, PIN, UI layout |
| `wifi_cfg` | WiFi and access-point credentials |
| `macros` | macro slots |
| `pcprof` | saved PC names and passwords |

Passwords are intentionally write-and-use data. The firmware can use a saved
PC password to unlock, but it does not expose the password in `pc_list` or
provide a command to read it back.

The flashing script updates firmware partitions without erasing NVS. This is
why settings survive a normal firmware upgrade. An erase operation is different:
it clears the stored settings too.

## 8. What the board knows, and what it only believes

USB HID is mostly one-way: the board sends input to the PC. It does not receive
the PC's actual desktop state back.

Therefore the firmware cannot truly know:

- whether the PC is locked;
- the current volume percentage;
- whether the PC accepted a key;
- which window is focused.

The project handles this honestly:

- volume is an endless encoder: send volume up or down ticks instead of
  pretending to know a 0-100 value;
- mute is shown as the board's current intent;
- lock state is tracked optimistically and can be corrected with
  `set_lock_state`;
- wake reports whether the USB bus was suspended and whether the host accepted
  the remote-wakeup request.

This distinction is important. A local state variable is not the same thing as
observing the host.

## 9. Safety behavior

Some actions can leave the PC feeling broken if a connection disappears while
something is held down. The firmware and dashboard defend against that:

- a fresh dashboard connection sends `key_release_all`;
- when the last WebSocket client disconnects, the firmware releases all keys and
  mouse buttons;
- the dashboard releases held controls when the page is hidden;
- idle timeouts prevent an abandoned app-switcher Alt key or drag button from
  remaining pressed forever.

This is especially important because the app switcher intentionally holds Alt
across multiple messages, and a mouse drag intentionally holds the left button.

## 10. How the tests prove it

The tests are serial integration probes. They open the CH343 COM port, send the
same JSON commands a client would send, parse the JSON replies, and check the
result.

[tests/test_v34_features.py](tests/test_v34_features.py) checks that:

- the firmware identifies as v3.4 and HID is ready;
- large movement is accepted and can be reversed;
- horizontal pan works;
- volume down and volume up form a reversible pair;
- invalid media keys and macro slots are rejected;
- absolute mouse and gamepad commands agree with the saved configuration;
- held keys can be released;
- the old ping and lock-state contracts still work;
- serial command round-trip latency is measured.

[tests/test_lock_state.py](tests/test_lock_state.py) exercises the full lock
state sequence: unknown, lock, idempotent second lock, unlock, idempotent second
unlock, and explicit state correction.

These tests are intentionally careful about side effects. The v3.4 test uses
large movement and then moves back, changes volume down and then up, and avoids
clicking or typing into the currently focused PC window.

## 11. A complete example

Suppose the phone wants to lock the PC:

```text
1. User taps Lock in the browser.
2. JavaScript sends JSON: {"cmd":"lock"}.
3. The WebSocket server receives the text frame.
4. processCommand() checks authentication.
5. The lock handler maps the action to GUI+L.
6. USBHIDKeyboard sends the key-down and key-up reports.
7. The PC locks as if a person pressed the physical shortcut.
8. The firmware returns a JSON reply such as {"reply":"locked"}.
```

For a fast trackpad movement, the path is shorter:

```text
1. Browser receives a touch movement.
2. Browser accumulates it until the next animation frame.
3. Browser sends a seven-byte binary MOVE frame.
4. onWsEvent() decodes the fixed fields.
5. hidMouseMove() splits large deltas if needed.
6. The USB mouse sends HID reports to the PC.
```

## 12. Where to read next

- [README.md](README.md): setup, hardware ports, and quick start.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): design constraints and reasons.
- [docs/COMMANDS.md](docs/COMMANDS.md): JSON commands and binary opcodes.
- [docs/HARDWARE.md](docs/HARDWARE.md): board wiring and port details.
- [docs/FLASHING.md](docs/FLASHING.md): build and flash process.
- [SECURITY.md](SECURITY.md): threat model and security boundaries.
- [usb_hid_unlock.ino](usb_hid_unlock/usb_hid_unlock.ino): firmware, dispatcher,
  HID output, WiFi, storage, and WebSocket handling.
- [web_ui.h](usb_hid_unlock/web_ui.h): dashboard UI and browser input logic.

A good way to study the code is to follow one command end to end: start with
`txMove()` or `send()` in `web_ui.h`, find the matching WebSocket or JSON branch
in `onWsEvent()` or `processCommand()`, and finish at `hidMouseMove()`,
`Keyboard.press()`, or the corresponding HID class call.
