"""Files are read and written as UTF-8, said out loud. Windows' default is the
ANSI code page: README.md's arrows made tools/support_date.py fail on the
Windows CI runner ('charmap' codec). Every read_text/write_text in tools/ and
tests/ names its encoding."""
import re

import pytest

from support import paths

pytestmark = pytest.mark.static
BAD = re.compile(r"\.read_text\(\s*\)|\.write_text\((?![^()\n]*encoding)[^()\n]*\)")


def test_text_files_are_read_and_written_as_utf8():
    root = paths.TESTS.parent
    hits = []
    for p in list((root / "tools").rglob("*.py")) + list((root / "tests").rglob("*.py")):
        if any(x in p.parts for x in (".build", ".reports", "node_modules")):
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if BAD.search(line):
                hits.append(f"{p.relative_to(root)}:{i}: {line.strip()[:90]}")
    assert not hits, "name the encoding (encoding=\"utf-8\"):\n  " + "\n  ".join(hits[:20])
