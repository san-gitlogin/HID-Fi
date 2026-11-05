# Factory reset: the board as new, from the Companion

Status: **being built.** ▢ is unproven until the test next to it passes on a real
board; ✔ is proven, with where.

## What it does

Everything a person set up is erased; the board's identity stays:

| Kept | Erased |
|---|---|
| the board's name (`ESP32-HID-<MAC>`) and the WiFi password it made for itself (`ap_genpw`) - it becomes the board's password again | the access PIN and its lockout, every setting (`hid_cfg`) |
| the update journal (`ota`: slot, step, from/to, why) - a reset can never break a rollback | auto-update choices and the update source (`ota`: auto, autosec, src) |
| the firmware itself | home WiFi networks, a custom board WiFi name or password (`wifi_cfg`) |
| | stored passwords (`vault`), macros (`macros`), saved PCs (`pcprof`), paired computers (`compan`) |

Then the board restarts as it did the first time: its own access point, no PIN, no
home network. Refused while an update is being verified or installed (the same rule
as recovery mode: a restart then would cost the update).

## Who may ask, and what proves it

Designed from the person's side (AGENTS.md, "UX for everyone"): no tool, no ritual,
and the one button only as the last resort.

| Situation | Proof | What the person does |
|---|---|---|
| Their own computer - paired with the board - and the board plugged into it | this computer's pairing key, **and** the erase arrives over this computer's USB cable | Companion: Factory reset..., tick "I understand", Start. |
| Another computer, and they know the access PIN | the PIN, on the phone's dashboard | That Companion asks; the dashboard shows "<computer> wants to factory reset this board" - Approve. The dashboard's Settings also has Factory reset, behind the PIN. |
| PIN forgotten and no paired computer | holding the board: the pinhole held 8 s opens recovery mode | Recovery mode's panel (or `--recover`): Factory reset. |

**Why the paired path restarts first.** A signed request over the network cannot
prove which computer the board is plugged into. So the request only makes the board
restart with its USB link open for 60 s, holding a one-use token; the erase must come
**down that cable**, signed with the same pairing key over the token. A paired laptop
elsewhere on the network cannot send anything down a cable it is not on. That link
answers only `status`, `factory_reset` (with the proof) and `recovery_exit` - never
the WiFi password or a PIN reset, which stay behind the pinhole.

**The confirmation** is a plain warning, a checkbox and a 10-second countdown with a
large Cancel - not typing a word (harder for some people, no safer). Every step
shows its progress, and the result says what happened, including "nothing was
changed".

## Protocol

Firmware commands (docs/COMMANDS.md):
- `{"cmd":"factory_reset","confirm":true}` - over USB in recovery mode (pinhole) or
  on the COM port; over WiFi only behind the access PIN gate (a board without a PIN:
  only from its own access point, whose password is the proof). Paired-reset link:
  `{"cmd":"factory_reset","proof":"<hex HMAC>"}`. Reply `{"status":"ok",
  "reply":"factory_reset","restart":true}` or an error (`verifying`, `busy`,
  `confirm_needed`, `bad_proof`, `not_allowed`).
- `{"cmd":"factory_reset_decline","request":"<hex>"}` - PIN-gated, from the dashboard.

`POST /api/companion`:
- `{op:"reset", id, chal, mac}` - HMAC-SHA256 with the pairing key over
  `reset|<id>|<chal>`. Reply `{"status":"ok","token":"<hex>"}`; the board restarts
  with the paired-reset link. On the link: `proof` = HMAC(key,
  `factory_reset|<id>|<token>`), one use.
- `{op:"reset_request", name}` - unsigned, from a computer that is not paired. One
  request at a time, forgotten after 120 s, shown on the dashboard. Reply
  `{"status":"ok","request":"<hex>"}`.
- `{op:"reset_request_status", request}` - `pending`, `declined`, `approved` or
  `unknown` (expired, or the board already restarted).

`status` gains `fresh` (no PIN, no home network, no pairings, nothing stored) and,
while a request waits, `reset_request: {name, left_s}`.

## Tests

- ▢ unit/sim: the wipe keeps exactly the kept keys; every proof rule; refusals.
- ▢ dashboard BDD: Settings reset (warning, checkbox, countdown, cancel), approve and
  decline a request.
- ▢ Companion: the three flows against a model board; window states; both engines.
- ▢ hardware (09F7C8, NVS saved first and written back after): each path erases and
  keeps what it should; a paired-reset link refuses everything else; a request from a
  computer that is not paired waits for the PIN.
