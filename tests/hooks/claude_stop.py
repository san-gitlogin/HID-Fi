"""Claude Code Stop hook (.claude/settings.json): an agent cannot finish a turn
that leaves HID-Fi's checks failing.

- Anything under hid_fi/, tests/ or docs/ changed: the static tier runs (seconds) -
  every command documented, modelled and tested; the dashboard well formed.
- The dashboard or its scenarios changed since the last good run: the ui tier
  runs too (about 5 minutes), which includes the coverage contract and the
  500-screen responsive matrix.

Failing, it exits 2: Claude Code shows the agent why and the agent keeps working.
It never runs a tier that touches the board or the computer.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAMP = ROOT / "tests" / ".reports" / "ui_ok.sha"


def changed():
    r = subprocess.run(["git", "status", "--porcelain", "--", "hid_fi", "tests", "docs"],
                       capture_output=True, text=True, cwd=ROOT)
    return [l[3:] for l in r.stdout.splitlines() if l.strip() and ".reports" not in l and ".build" not in l]


def ui_hash():
    h = hashlib.sha256()
    for p in [ROOT / "hid_fi" / "web_ui.h", *sorted((ROOT / "tests" / "ui").rglob("*")),
              *sorted((ROOT / "tests" / "support").glob("*.py")), *sorted((ROOT / "tests" / "golden").glob("ui_*.json"))]:
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def run(tier):
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "run.py"), "--tier", tier],
                       capture_output=True, text=True, cwd=ROOT)
    return r.returncode, (r.stdout + r.stderr)


def main():
    try:
        event = json.load(sys.stdin)
    except Exception:
        event = {}
    if event.get("stop_hook_active"):       # already blocked once this turn: do not loop
        return 0
    if not changed():
        return 0
    code, out = run("static")
    if code:
        sys.stderr.write("HID-Fi static checks fail - fix before finishing:\n" + out[-4000:])
        return 2
    h = ui_hash()
    if not (STAMP.exists() and STAMP.read_text().strip() == h):
        code, out = run("ui")
        if code:
            tail = [l for l in out.splitlines() if "contract" in l or l.startswith(("  control", "  function", "FAILED", "  only"))]
            sys.stderr.write("HID-Fi dashboard tier fails - fix before finishing "
                             "(see .claude/skills/hidfi-feature):\n" + "\n".join(tail[-40:]) + "\n")
            return 2
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
