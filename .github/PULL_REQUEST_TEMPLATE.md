## What this changes

<!-- One or two lines. -->

## Why

<!-- What was wrong, or what this makes possible. -->

## How it was tested

- Board tested on: 
- Host OS: 

```
python tests\test_v34_features.py
```

```
paste the result here
```

<!-- If you changed the dashboard, please add a screenshot at phone width
     (360-390px). Most of this UI is used one-handed on a phone. -->

<!-- If you changed anything about timing, feel or latency, say what you
     measured rather than how it felt. -->

## Checklist

- [ ] I read [AGENTS.md](../AGENTS.md) / [CONTRIBUTING.md](../CONTRIBUTING.md)
- [ ] Firmware builds and flashes
- [ ] `tests/test_v34_features.py` passes
- [ ] Dashboard JavaScript passes `node --check` (if I touched `web_ui.h`)
- [ ] No emoji added to the dashboard
- [ ] No new way to read a saved PC password back out
- [ ] The shape of existing serial commands is unchanged
- [ ] Docs updated in this same change
