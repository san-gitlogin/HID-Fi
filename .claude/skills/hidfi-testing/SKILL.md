---
name: hidfi-testing
description: Run and interpret HID-Fi's test suite (tests/run.py) safely - which tier to run after which change, what each tier does to the user's computer, how to read skips and golden-file failures. Use whenever HID-Fi code, the dashboard (web_ui.h), the firmware (hid_fi.ino), the tests or the docs changed, before saying a change is done, or when asked to test, verify or check for regressions.
---

# Testing HID-Fi

The full guide is `docs/TESTING.md`. This is the procedure.

## 1. Always: the no-board tiers

```bash
python tests/run.py
```

static, unit, ui, build, hostsim. Needs no board and touches nothing. Run it
before claiming any change is done. If something is missing, `python tests/run.py
doctor` says what.

`hostsim` needs Linux; elsewhere it is skipped. Where you are decides how:

| Machine | Run |
|---|---|
| Linux, or a cloud sandbox | `tests/setup_linux.sh` then `python3 tests/run.py` |
| Windows | the same, inside WSL (a clone made inside WSL) |
| macOS | `tests/docker/run.sh`, or `tests/docker/vm.sh up` first if the user set up `HIDFI_VM_HOME` |

Never install Docker, WSL, colima or a VM without asking, and keep everything
under the folder the user chose (`HIDFI_VM_HOME`). Steps: AGENTS.md, "Setting
up and running tests, as an agent".

## 2. Pick the hardware tiers from what changed

| Changed | Also run | Needs |
|---|---|---|
| Only docs or tests | nothing more | |
| `web_ui.h` only | nothing more (ui tier covers it) - but a flash is needed for users to see it | |
| `hid_fi.ino` (any) | `--board` | board on COM |
| typing, keys, mouse, HID, host detection | `--e2e` (or `--all`) | board on COM **and** USB, the user present |
| timing, loops, WiFi, allocation | `--perf --board` | board on COM |

**Ask the user before `--board`, `--e2e`, `--perf` or `--all`.** `--board` moves
the mouse (net zero) and changes the volume (down, then up). `--e2e` opens a
browser page the user must click Start on, then types and moves the mouse into
it - they must not touch the computer during the run. On Linux, `--e2e` needs
`sudo -E` (or the `input` group) so the board's devices can be grabbed.

Before running any test script on someone's desktop, read it end to end. A
"safe" flag in this repo once still pressed the mouse button.

## 3. Read the result honestly

- The summary table lists passed / failed / error / skipped per tier, then every
  skip reason. **Report the skips.** "All tests pass" with board tiers skipped
  is a statement about the no-board tiers only - say so.
- `test_vault.py refused` on a board in real use (PIN set, entries stored) is
  correct protection, reported as a skip. Never work around it.
- `board not left as found` means a setting was not restored -
  `tests/.reports/board_restore.json` names it. Fix the test, then restore the
  board.

## 4. Golden-file failures

`tests/golden/` defines "unchanged" (typing traces byte for byte, what every
dashboard control sends, size, warnings, layout and performance baselines).

- If the change was **not** meant to alter that behaviour: it is a regression.
  Fix the code.
- If it **was** meant to: rerun with `--update-golden`, read `git diff
  tests/golden/`, and state in the summary exactly what changed and why.
- Never update a golden file to make a failure you do not understand go away.

## 5. Adding coverage

- New test goes in the tier folder for what it needs; mark it
  (`pytestmark = pytest.mark.<tier>`).
- Use `tests/support/` (board session with snapshot/restore, fake board,
  focus-guarded e2e page, corpus with independent expectations, facts read from
  the firmware source).
- Anything that sends input to the host goes in `tests/e2e/` through the `drive`
  fixture, never in `tests/board/`.
- A new guarantee in the typing code gets a planted bug in
  `tests/unit/firmware/mutations.json` that it must catch.
- Anything a person does on the dashboard is a BDD scenario
  (`tests/ui/bdd/features/`). The ui tier's last lines are the **coverage
  contract**; if it fails, follow `.claude/skills/hidfi-feature` section 4.
- Running one feature file does not judge the contract - run `--tier ui` before
  handing back.

## 6. Hand back

Paste `tests/.reports/summary.md` (or its numbers) into the reply, name the OS
the hardware tiers ran on, and list what was not run and why.
