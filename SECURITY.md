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

### USB serial is never gated — with one exception

Serial commands bypass the PIN and are never locked out. This is intentional:

- Physical access to the board is already total control.
- It has to remain possible to recover after locking yourself out.

If you cannot afford that, do not leave the board plugged into a machine other
people can reach.

**The exception is any command that would reveal a stored secret.** Those are PIN
gated on *every* transport, serial included. Serial is ungated so you can
*recover* the board, not so you can read what is on it.

That exception is not enough on its own, because `set_auth` over serial can
overwrite the PIN without knowing the old one — so anyone with the cable could
set a PIN of their choosing and then use it. The rule that closes it:

> **Changing or clearing the access PIN erases every secret that can be read
> back.** You cannot reach the secrets by resetting the PIN, because resetting it
> destroys them.

A normal PIN change made by someone who knows the current PIN is offered the
choice; a reset by someone who does not know it is a wipe, with no prompt to
bypass. Saved PC passwords are unaffected by this rule because nothing can read
them back at all — see below.

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
  returns the name and which OS that computer is, and nothing else.
- `unlock` takes a **slot number**; the board looks the password up itself. The
  secret crosses the network exactly once, when you save it.
- Editing a saved PC never reveals its password. Renaming it or changing its OS
  sends no password at all, and the stored one is kept.
- Passwords are never logged. The serial log records slot, name and length.

If you would rather not store one, don't — typing it into the password field still
works and keeps nothing.

---

## Do not put a password in a macro

A macro is a list of steps the board replays, and `macro_get` returns those steps
verbatim so the dashboard can edit them. It is **not** a secret store:

- `macro_get` is a normal command, so over USB serial it is **ungated**.
- A `type` step containing your password is therefore readable by anyone with the
  cable, with one request.

This is intended behaviour for macros — you cannot edit a step you cannot see —
but it means the saved-PC slots and the password vault are the only places a
secret belongs.

---

## What never returns a secret

Audited against the firmware, current as of v3.8:

| Command | What it gives back |
|---|---|
| `pc_list` | Slot, name and OS. Never the password. |
| `vault_list` | Slot, purpose and kind. Never the secret, and not its length. |
| `vault_get` | **The secret** — the one reveal, and it needs the PIN in the request on every transport. |
| `macro_list` | Slot and name only. |
| `macro_get` | **The macro body in full** — see the warning above. |
| `storage_info` | Booleans only: `pin_set`, `sta_pass_set`, `ap_pass_set`. |
| `status`, `/api/status` | `auth_set` as a boolean. No PIN, no passwords. |
| `wifi_status` | The SSID. Never the WiFi password. |

The board's **own access point password** is the exception, and deliberately so:
it is printed on the serial console at boot, because you need a way to recover it
when you have forgotten what you set. It protects the radio, not your data.

If you add a command, add it to this table or prove it belongs in it.

**This is a test, not a promise.** `tests/test_secrets.py` plants a marker
password in a PC slot and in a macro, then fails if any listing, status or
inventory reply contains it. `tests/test_vault.py` does the same for the vault
and additionally proves the gate: that nothing can be read, typed, edited or
deleted without the PIN, and that resetting the access PIN blind destroys what
it guarded. Both run over serial, the transport with no PIN and no lockout,
because that is the worst case.

---

## The password vault

Stores arbitrary secrets — a Netflix PIN, a work password, a door code — and has
the board type them on demand, so they are never typed on a keyboard that might
be watched or logged. Three fields per entry: a purpose, a kind (PIN or
password), and the secret itself, any length. A PIN is not assumed to be four
digits, or digits at all.

The rules it holds to, each enforced in firmware and covered by
`tests/test_vault.py`:

1. **Nothing is stored until an access PIN is set.** Without a PIN, a stored
   secret is a secret anyone in radio range can have typed out.
2. **Getting a secret out needs the PIN in the request itself** — reading it
   back or typing it — on **every transport, USB serial included**. That is the
   one exception to serial being ungated.
3. **There is no session.** Nothing stays unlocked. Typing a stored secret asks
   for the PIN every single time, because the keystrokes *are* the secret and
   the board cannot see which window they land in.
4. **Putting one in does not need the PIN.** Saving, renaming and deleting
   disclose nothing, so they do not ask. A PIN prompt in front of an act that
   cannot leak anything is a toll, not a control — and one people learn to type
   without reading. Tampering is the accepted price; disclosure is not.
5. **Listing gives purposes and kinds only** — not the secret, and not its
   length. No status or inventory response carries one.
6. **Editing never reveals.** The stored secret is never sent back to the page
   to be put in a field; leaving it empty keeps it.
7. **Deleting is confirmed** and says plainly that the secret will be destroyed.
8. **Nothing is logged.** Not the secret, not its length. The serial log records
   the slot and its purpose.
9. **Wrong guesses are rate limited**, on the vault's own counter, with the same
   escalating backoff as the access PIN — and unlike the access PIN, the lockout
   applies on serial too. An ungated guess loop over a cable would clear four
   digits instantly.
10. **A PIN entry is digits.** An entry saved as a PIN is refused if it is not,
    and the dashboard shows a number pad and strips anything else as you type.

### The vault can have its own PIN

By default the access PIN — the one that unlocks the dashboard — also opens the
vault. You can give the vault a **separate four digit PIN**, so unlocking the
dashboard stops being the same thing as unlocking your passwords. This is the
behaviour a browser has when it asks for your account password before showing a
saved one.

**Changing it needs the current one.** Forgotten it? You can reset it with the
**access PIN** — and that erases the vault. That is not an oversight:

> If the access PIN could replace a forgotten vault PIN *and* keep the entries,
> then the access PIN would quietly be a way to read everything, and the second
> PIN would be decorative.

So the reset exists, it is reachable, and it costs exactly what it guarded.

### Why resetting the access PIN erases the vault

`set_auth` over serial is ungated, so anyone with the cable could set a PIN of
their own — and if the vault answered to it, the gate would be worthless.

> **Changing or clearing the access PIN erases the vault**, unless you prove you
> knew the old PIN (`set_auth` takes `old`) or a separate vault PIN is in force.

The dashboard passes `old` for you, so an ordinary PIN change from Settings
keeps everything. An attacker who does not know it gets an empty vault. Turning
the PIN off erases the vault too — without a PIN there is nothing guarding it —
and the dashboard says so before it happens.

`vault_wipe` is ungated for the same reason in reverse: destroying a secret
discloses nothing, and it is the way back from a forgotten vault PIN.

### What it does not protect against

- **A flash dump reads everything.** NVS is not encrypted. Encrypting the vault
  with a key derived from the PIN would raise the bar, but a four-digit PIN is
  ten thousand guesses offline — minutes of work, not a defence. It would be
  worth doing only alongside a longer passphrase, and real protection needs the
  ESP32's own flash encryption with the key in efuse.
- **The board can still type the secrets.** Anyone who has the PIN can have one
  typed into whatever window is focused, which is as good as reading it.
  Concealing it on screen is not the same as keeping it.
- **There is no TLS.** A reveal or a save over WiFi crosses the network in clear
  text, the same as the unlock password does.
- **The board cannot see where it is typing.** "Type" sends the secret to
  whatever the host has focused. Focus the right box first.

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
  send anything they like over serial. Commands that reveal a stored secret are
  PIN gated even there, and resetting the PIN erases what it protected — but a
  flash dump goes under all of that, because NVS is not encrypted.
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
