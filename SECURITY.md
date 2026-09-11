# Security

This project lets anything on a WiFi network type on a computer. That deserves a
straight description of what it does and does not protect.

---

## The short version

Out of the box, **this is not secure**. Two settings make it reasonable, and both
take about thirty seconds:

1. **Change the access point password.** Settings → Access point.
2. **Set a 4-digit access PIN.** Settings → Access PIN.

Do both before you use the board for anything that matters.

---

## Why the defaults are not safe

Every board flashed with this firmware advertises an access point with the **same
password**, `hid12345`, and that password is written in the README. Anyone who has
seen this project and is within WiFi range can join and start typing on your
computer.

Until you set a PIN, the dashboard is **completely unauthenticated**. Joining the
network is the only barrier.

This is a deliberate tradeoff for first-run usability — you have to be able to get
in before you can lock it down — but it is only defensible if you actually lock it
down.

---

## The access PIN

Four digits, gating every wireless request. That is only 10,000 combinations,
which a script clears in minutes at socket speed. So the board throttles:

| Consecutive wrong PINs | Lockout before the next attempt |
|---|---|
| 1 | none |
| 2 | 5 seconds |
| 3 | 15 seconds |
| 4 | 60 seconds |
| 5 and beyond | 5 minutes each |

Walking the whole keyspace under that schedule takes roughly a month.

**Every path that tests a PIN goes through one check.** The HTTP API accepts a PIN
inline in the request body, and an earlier version of this firmware tested it
without counting the attempt — which meant the lockout could be bypassed entirely
by POSTing guesses. Both paths now share the same counter. If you add another
transport, route it through `pinCheck()` too.

A request carrying **no** token is not counted as a failure, so an ordinary
unauthenticated page load cannot lock out the legitimate user.

### USB serial is never gated

Serial commands bypass the PIN and are never locked out. This is intentional:

- Physical access to the board is already total control.
- It has to remain possible to recover after locking yourself out.

If you cannot afford that, do not leave the board plugged into a machine other
people can reach.

---

## Saved PC passwords

The dashboard can store a PC password on the board so you do not retype it.
Understand the tradeoff first:

- **ESP32 flash is not encrypted.** Anyone who physically takes the board can dump
  it and read the password. Treat a saved password as one written on a note taped
  to the device.
- A saved password turns the board into a **one-request unlock** for that machine,
  needing no knowledge at all — just radio range.

Because of that second point, **saving a password is refused unless an access PIN
is set**. That check is the control that makes the feature defensible.

Design constraints, which contributors should not relax:

- **No command reads a password back.** Not over WiFi, not over serial. `pc_list`
  returns names only.
- `unlock` takes a **slot number**; the board looks the password up itself. The
  secret crosses the network exactly once, when you save it.
- Passwords are never logged. The serial log records slot, name and length.

If you would rather not store one, don't — typing it into the password field still
works and keeps nothing.

---

## Joining a home network changes the threat model

This is the part people miss, so the firmware enforces it rather than only warning
about it.

On the board's **own access point**, an attacker has to be physically within WiFi
range of you. That is a real constraint — it means someone in the building.

The moment the board **joins a home or office network**, that constraint is gone.
Every device on that network can reach the dashboard: the smart TV, the guest's
laptop, the IoT plug running firmware from 2019, anything already compromised. The
radius of "who can type on your computer" jumps from one building to one subnet,
and nothing about the dashboard looks any different.

**So when the board is on a network and no PIN is set, wireless control from
outside the access point subnet is refused.** The reply is:

```json
{"status":"error","reply":"lan_locked"}
```

Deliberate exceptions, so the user is not locked out of fixing it:

| Still allowed | Why |
|---|---|
| `status`, `ping`, `wifi_status`, `ui_get` | Read-only. The dashboard must be able to load and explain the problem. |
| `auth`, `set_auth` | Otherwise the PIN could not be set from where the user is standing. |

Access-point clients are unaffected. USB serial is never gated.

This is not a substitute for a PIN — it *is* the demand for one. It cannot help
you if you set `1234`, and it does nothing about traffic interception, because the
link is still plain HTTP.

### If you must be on a network

- Set a PIN first, from the access point, before joining.
- Prefer a guest VLAN or an IoT network that cannot reach your main devices.
- Give it a static address so you can find it in the router's client list and
  firewall it if you want to.
- Remember that anyone on that network can also read your unlock password off the
  wire as you type it, because there is no TLS.

---

## What this does not protect against

Being honest about the boundaries:

- **Physical access.** Someone holding the board can read everything on it and
  send anything they like over serial.
- **A malicious host.** The PC the board is plugged into can see it is a HID
  device. It cannot read your saved passwords over USB, but it controls power.
- **WiFi cracking.** The access point is WPA2 with whatever password you set. A
  weak one is weak.
- **Traffic interception.** The dashboard is plain HTTP and unencrypted
  WebSocket. Anyone already on the same network can read the traffic, including a
  password as you type it into the unlock field. TLS on an ESP32 for this workload
  is not practical, so treat the network itself as the security boundary.
- **Rubber-ducky style abuse.** This is a device whose entire purpose is injecting
  keystrokes. Anyone with access to the dashboard can do anything you could do at
  the keyboard.
- **A weak PIN.** Four digits with a lockout is a speed bump against a stranger,
  not a defence against someone who watched you enter it.

---

## Recommendations

- Change the access point name and password. A distinctive name also stops you
  connecting to someone else's board by accident.
- Set a PIN, and do not use `1234`.
- Prefer the board's own access point. If you do join a network, use a guest or
  IoT VLAN rather than the one your computers are on.
- Turn on **ap_auto_off** if you are permanently on a network — it removes the
  access point as a way in when nobody is using it, and BOOT brings it back.
- Unplug it when you are not using it. It is a keyboard that anyone in range
  might be able to type on.

---

## Reporting a vulnerability

Please do not open a public issue for something exploitable.

Email `san.phplogin@gmail.com` with what you found and how to reproduce it. I will
confirm within a week and credit you in the fix unless you would rather I did not.
