---
name: hidfi-feature
description: Add or change a HID-Fi feature end to end - firmware command, dashboard control, the simulated board, the BDD scenarios and the coverage contract - so nothing ships untested. Use whenever adding or changing a command in hid_fi.ino, a control, sheet, tab or behaviour in web_ui.h, or when a test run reports the coverage contract failed.
---

# Adding a feature to HID-Fi

Read `AGENTS.md` ("Adding a feature" and "Definition of done") first. This is
the order that keeps the tests honest. The run enforces most of it - when it
fails, the message says which step was skipped.

## 1. Find the truth

Read the firmware handler and the dashboard code you are touching, end to end.
Behaviour that looks like a bug here has often been documented hardware
behaviour (`AGENTS.md`, "Known hardware behaviour").

## 2. Firmware: a new or changed command

- Handler + prototype + `strcmp` branch in `processCommand` (`hid_fi.ino`).
- `docs/COMMANDS.md`: the command, every field, every reply, every error.
- `tests/support/board_sim.py`: a branch in `_dispatch` that replies **exactly**
  as the firmware does - same `reply`, same keys, same error messages. The
  dashboard scenarios run against this model, not the board.
- `tests/board/test_sim_contract.py`: add the command (read-only list, or the
  stateful round trip if it changes something - and put it back). This is what
  proves the model, whenever a board is attached.

Static rules fail the run if the command is not documented, not modelled, or
never sent by a test.

## 3. Dashboard: a new control, sheet or behaviour

Write the scenario **first**, the way a person would describe using it:

```gherkin
# tests/ui/bdd/features/<tab>.feature
Scenario: Pinning the computer by hand, and handing back to detection
  Given the board is plugged into a Windows computer
  And the dashboard is open
  When I open the "Settings" tab
  And I tap "macOS"
  Then the board receives {"cmd": "set_host_os", "os": "mac"}
  And "#osName" reads "macOS"
```

- Shared steps live in `tests/ui/bdd/conftest.py` - read the list before
  writing a new one. Tab-specific steps go in `tests/ui/bdd/test_<tab>.py`.
- Touch input is real (Chromium's DevTools input): `ui.touch_drag`,
  `ui.touch_hold`, `ui.tap_point`, `ui.circle`, several fingers via `ui._touch`.
  Synthetic DOM events miss `setPointerCapture` and `pointerrawupdate`.
- Assert what reaches the **board** (`sim.received`, `sim.frames`), and what the
  person sees (text, toasts, dialogs) - not internal variables.
- Every branch a person can hit: the happy path, the confirm answered no, the
  board refusing (`Given the board will refuse the next "<cmd>" with "<why>"`),
  the link dropping, the phone on the LAN, the other OSes.
- What the phone's browser is: `Given the dashboard is open on a <device>`
  (`DEVICES` in `tests/support/ui.py`), `Given this phone's browser cannot
  vibrate` (`BROWSERS` in the conftest).

Run just your feature while writing it:

```bash
python -m pytest tests/ui/bdd/test_<tab>.py -q -n 4 -p no:cacheprovider
```

## 4. The coverage contract

Run the whole tier - the contract is only judged on a full run:

```bash
python tests/run.py --tier ui
```

The last line of pytest's output is the contract:

- `control #x ... no scenario touches it` - write the scenario.
- `function f() never runs` - a scenario is missing, or `f` is dead code. If it
  truly cannot run in Chromium, add it to `tests/golden/ui_functions_unrun.json`
  **with the reason**. Dead code is a finding to report, not something to hide.
- `only N% of the dashboard script ran` - read
  `tests/.reports/ui_js_uncovered.txt`; it lists the exact `web_ui.h` lines.
- `... well above the floor - raise it` - good news: `--update-golden` and commit.

## 5. Every screen

The ui tier also draws the dashboard on 500+ screens (`tests/ui/responsive`), from
a folded phone to an 8K display, upright and turned, toolbars showing and
hidden, notch included - and fails on any **new** layout problem. When it does,
the message names the screen and the state; look at that frame before changing
anything (open it at that size, take a screenshot). Then:

- a new sheet or overlay: add it to `SHEET_STATES` / `OVERLAY_STATES` in
  `tests/support/responsive_run.py` (a static rule insists);
- a new breakpoint: nothing to do - the matrix is read from the CSS;
- you fixed a known layout problem: `--update-golden`, and check the diff of
  `tests/golden/ui_responsive_known.<os>.json` only removes lines;
- it got slower: `tests/.reports/responsive_perf.md` has the numbers.

## 6. A bug you found but are not fixing now

Tag the scenario that shows it: `@known_issue:<key>`, add `<key>` with a one-line
cause to `KNOWN_ISSUES` in `tests/ui/bdd/conftest.py`, and a line under
`docs/TESTING.md#known-issues`. It runs as a strict xfail - it fails the run the
day the bug is fixed, so the tag cannot outlive the bug. Tell the user about it.
Never weaken or delete a scenario to make a run green.

## 7. Done

`python tests/run.py` prints PASSED, then the hardware tiers - with the user's
OK - for any firmware change (`.claude/skills/hidfi-testing`). Update
`CHANGELOG.md`, and the docs the change touched, in the same commit.
