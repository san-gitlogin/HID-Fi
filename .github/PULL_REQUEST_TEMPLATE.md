## What this changes

<!-- One or two lines. -->

## Why

<!-- What was wrong, or what this makes possible. -->

## How it was tested

- Board tested on: 
- Host OS: 

```
python tests/run.py            # always
python tests/run.py --all      # if you touched firmware and have a board
```

<!-- Paste tests/.reports/summary.md here. Say which OS the hardware tiers ran on,
     and copy any SKIPPED reasons - a skipped tier is not a passed one. -->

<!-- If you changed the dashboard, please add a screenshot at phone width
     (360-390px). Most of this UI is used one-handed on a phone. -->

<!-- If you changed anything about timing, feel or latency, say what you
     measured rather than how it felt. -->

## Checklist

- [ ] I read [AGENTS.md](../AGENTS.md) / [CONTRIBUTING.md](../CONTRIBUTING.md)
- [ ] Firmware builds and flashes
- [ ] `python tests/run.py` passes (and `--all` on a board, for firmware changes)
- [ ] The dashboard coverage contract is OK - every control touched, every function run (see the last lines of the ui tier)
- [ ] New commands are documented, modelled in `tests/support/board_sim.py` and sent by a test
- [ ] Any bug found and not fixed is a `@known_issue` scenario and a line in `docs/TESTING.md#known-issues`
- [ ] Any change to `tests/golden/` is intended, and explained above
- [ ] No emoji added to the dashboard
- [ ] No new way to read a saved PC password back out
- [ ] The shape of existing serial commands is unchanged
- [ ] Docs updated in this same change
