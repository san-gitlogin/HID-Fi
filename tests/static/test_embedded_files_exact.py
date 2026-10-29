"""Every data file the Companion embeds with include_str!/include_bytes! must be
checked out byte-for-byte on Windows too (.gitattributes `-text`). A CRLF
conversion changed the vendored flasher stub's SHA-256 and failed every cable
update test on the Windows runner."""
import re, subprocess

import pytest

from support import paths

pytestmark = pytest.mark.static
ROOT = paths.TESTS.parent
EMBED = re.compile(r'include_(?:str|bytes)!\("([^"]+)"\)')
BINARY = {".png", ".bin", ".ico", ".icns"}


def _embedded():
    for rs in (ROOT / "companion" / "crates").rglob("*.rs"):
        if "target" in rs.parts:
            continue
        for m in EMBED.finditer(rs.read_text(encoding="utf-8")):
            p = (rs.parent / m.group(1)).resolve()
            if p.suffix not in BINARY and p.suffix != ".rs":
                yield p.relative_to(ROOT).as_posix()


def test_embedded_data_files_are_never_line_converted():
    files = sorted(set(_embedded()))
    assert files, "found no embedded files - the pattern is wrong"
    out = subprocess.run(["git", "check-attr", "text", "--", *files], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    loose = [ln.split(":")[0] for ln in out.splitlines() if not ln.endswith(": unset")]
    assert not loose, f"add these to .gitattributes as -text: {loose}"
