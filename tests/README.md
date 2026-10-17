# tests/

```bash
python tests/run.py            # everything that needs no board
python tests/run.py --all      # everything this machine can run (board on COM + USB)
python tests/run.py doctor     # what is installed vs pinned
```

The full guide - tiers, per-OS instructions, safety rules, golden files,
performance budgets, upgrading dependencies - is **[docs/TESTING.md](../docs/TESTING.md)**.

| Path | What |
|---|---|
| `run.py` | The one command: picks tiers, runs pytest, prints the summary |
| `conftest.py`, `pytest.ini` | Fixtures, tier markers and the safety rules |
| `deps.json` | Versions HID-Fi is verified against |
| `support/` | Shared pieces: board session, dashboard + fake board, e2e test page, corpus, firmware facts, Linux key derivation, perf budgets |
| `static/` `unit/` `ui/` `compile/` `hostsim/` `board/` `e2e/` `perf/` | One folder per tier (`compile/` is the build tier - pytest skips folders named `build`) |
| `golden/` | What "unchanged" means - see the guide before editing |
| `ui/bdd/` | The dashboard, one feature file per tab, against `support/board_sim.py`; ends with the coverage contract (`support/ui_contract.py`) |
| `hooks/` | git pre-commit / pre-push (`run.py install-hooks`) and the Claude Code Stop hook |
| `test_*.py` (this folder) | The original standalone suites. Each still runs on its own (`python tests/test_v34_features.py`); the tiers run them for you |
