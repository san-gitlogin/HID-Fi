# Gemini instructions

The full brief for AI agents in this repository is **[AGENTS.md](AGENTS.md)**.
Read it before making changes: most of it exists because something went wrong
once, and its rules are not optional.

The essentials, in case only this file is loaded:

- Run `python tests/run.py` before saying a change is done. It needs no board.
  Report skipped tiers with their reasons; a skip is not a pass.
- The Linux-only tier: on Linux or in a cloud sandbox, `tests/setup_linux.sh`
  then `python3 tests/run.py`; on Windows the same inside WSL; on macOS
  `tests/docker/run.sh`. Ask before installing anything. Every route:
  docs/TESTING.md, "The Linux tiers, whatever machine you have".
- Ask the user before any test that touches their computer (`--board`, `--e2e`,
  `--perf`) and before flashing or pushing.
- Never print, store or repeat a PIN or a secret. The one exception, and its
  rules, are in AGENTS.md ("Ownership, PINs and recovery").
- A new command or dashboard control ships with its test; the static tier fails
  otherwise.
- Screen sharing lives in `companion/` (Rust) and the dashboard's Screen tab:
  read AGENTS.md "Screen sharing" first. Never write the access PIN into a file;
  real-board tests take it from `HIDFI_PIN`.
- Before calling a change done, list the stories a person lives (a board moved
  to another computer, two boards on one, a phone turned on its side) and make
  each a test; AGENTS.md "Think in scenarios" says where they go.
