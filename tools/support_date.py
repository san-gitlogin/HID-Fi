"""The support statement's date (docs/OTA.md, decisions: support period).

    python tools/support_date.py            # print the date the newest release commits to
    python tools/support_date.py --write    # put it into README.md and SECURITY.md

The date is the newest release in tools/release_history.json + 12 months, the
same rule tools/ota_release.py writes into each manifest. Run --write when a
release is added to the history, before tagging.
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("README.md", "SECURITY.md")
MARK = re.compile(r"<!--support-until-->(\d{4}-\d{2}-\d{2})<!--/support-until-->")


def committed():
    sys.path.insert(0, str(ROOT / "tools"))
    import ota_release as R
    hist = json.loads((ROOT / "tools" / "release_history.json").read_text())["releases"]
    newest = max(hist, key=R.vtuple)
    d = date.fromisoformat(hist[newest]["date"])
    m = d.month - 1 + 12
    return date(d.year + m // 12, m % 12 + 1, min(d.day, 28))


def stated():
    return {f: MARK.findall((ROOT / f).read_text()) for f in FILES}


def main(argv):
    want = committed().isoformat()
    if argv == ["--write"]:
        for f in FILES:
            p = ROOT / f
            p.write_text(MARK.sub(f"<!--support-until-->{want}<!--/support-until-->", p.read_text()))
    print(want)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
