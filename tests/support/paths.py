"""Where things live. Everything else imports these rather than guessing."""
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
REPO = TESTS.parent
SKETCH = REPO / "hid_fi"
INO = SKETCH / "hid_fi.ino"
WEB_UI = SKETCH / "web_ui.h"
FLASH_SH = REPO / "flash_esp.sh"
CHANGELOG = REPO / "CHANGELOG.md"
DOCS = REPO / "docs"
GOLDEN = TESTS / "golden"
DEPS = TESTS / "deps.json"
CACHE = TESTS / ".build"          # compiled host binaries, build output - gitignored
REPORTS = TESTS / ".reports"      # junit.xml, summary.md, perf results - gitignored
