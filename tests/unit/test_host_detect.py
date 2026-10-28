"""Mac or PC from string-descriptor reads (hid_fi/host_detect.h), compiled
from the firmware's own header and run against real enumeration sequences.

The rule has been wrong twice: v3.7 called every Mac a PC (SET_IDLE), and the
ratio rule called macOS 15.6 a PC on every boot (2,2,1,1,3,3,4,4,0 is 4 repeats
of 9 - one short of half - although every string came in pairs).
"""
import pathlib
import shutil
import subprocess
import tempfile

import pytest

pytestmark = pytest.mark.unit

HEADER = pathlib.Path(__file__).resolve().parents[2] / "hid_fi" / "host_detect.h"
CXX = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")

HARNESS = r"""
#include <cstdio>
#include <cstdlib>
#include "host_detect.h"
int main(int argc, char** argv) {
    uint32_t reqs = 0, rereads = 0; uint16_t seen = 0, paired = 0; uint8_t last = 0xFF;
    for (int i = 1; i < argc; i++) {
        uint8_t idx = (uint8_t)atoi(argv[i]);
        reqs++; if (idx == last) rereads++;
        hdNote(idx, last, &seen, &paired);
        last = idx;
    }
    printf("%s\n", hdLooksMac(rereads, reqs, seen, paired) ? "mac" : "other");
    return 0;
}
"""

CASES = [
    # (what, sequence the host asked for, expected)
    ("macOS 15.6, measured 2026-10-21, 5 of 5 boots", "2,2,1,1,3,3,4,4,0", "mac"),
    ("the Mac example in docs/COMMANDS.md", "0,0,1,1,2,2,3,3,4", "mac"),
    ("a Mac reading the language table twice", "0,0,2,2,1,1,3,3,4,4", "mac"),
    ("Windows 11 shape: 11 requests, 2 repeats", "0,1,2,3,3,4,0,1,2,2,3", "other"),
    ("Windows reading straight through", "0,1,2,3,0", "other"),
    ("a host fetching the language table over and over", "0,0,0,0,1,2,3", "other"),
    ("two strings in pairs is not enough", "1,1,2,2,3,4,0", "other"),
    ("nothing at all", "", "other"),
    # Pinned decisions - each one caught a planted bug the cases above missed.
    ("half the strings in pairs reads as Windows' odd re-read", "1,1,2,2,3,3,4,5,6", "other"),
    ("two pairs is what Windows did - not a Mac", "1,1,2,2,0", "other"),
    ("the language table in a pair does not count as a string", "0,0,1,1,2,2,3", "other"),
]


@pytest.fixture(scope="module")
def judge():
    if not CXX:
        pytest.skip("no C++ compiler on this machine")
    d = tempfile.mkdtemp()
    src, exe = pathlib.Path(d, "t.cpp"), pathlib.Path(d, "t")
    src.write_text(HARNESS, encoding="utf-8")
    subprocess.run([CXX, "-std=c++11", "-I", str(HEADER.parent), str(src), "-o", str(exe)], check=True)
    yield lambda seq: subprocess.run([str(exe)] + [x for x in seq.split(",") if x], capture_output=True,
                                     text=True, check=True).stdout.strip()
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.parametrize("what,seq,want", CASES, ids=[c[0] for c in CASES])
def test_verdict(judge, what, seq, want):
    assert judge(seq) == want, f"{what}: {seq}"


# Planted bugs: each must flip at least one verdict above, or the cases have a
# blind spot (the same discipline as tests/unit/firmware/mutations.json).
MUTATIONS = [
    ("the per-string rule removed", "return p >= 3 && p * 4 >= n * 3;", "return false;"),
    ("three quarters loosened to half", "p * 4 >= n * 3", "p * 2 >= n"),
    ("two pairs counted as enough", "return p >= 3 &&", "return p >= 2 &&"),
    ("the language table counted as a string", "if (index == 0 || index >= 16) return;", "if (index >= 16) return;"),
    ("repeats not required to be back to back", "if (index == lastIndex) *paired", "if (*seen & (1u << index)) *paired"),
]


@pytest.mark.parametrize("name,find,replace", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_planted_bugs_are_caught(name, find, replace):
    if not CXX:
        pytest.skip("no C++ compiler on this machine")
    src = HEADER.read_text(encoding="utf-8")
    assert src.count(find) == 1, f"{name}: the text to mutate is not in host_detect.h exactly once"
    d = tempfile.mkdtemp()
    try:
        pathlib.Path(d, "host_detect.h").write_text(src.replace(find, replace), encoding="utf-8")
        c, exe = pathlib.Path(d, "t.cpp"), pathlib.Path(d, "t")
        c.write_text(HARNESS, encoding="utf-8")
        subprocess.run([CXX, "-std=c++11", "-I", d, str(c), "-o", str(exe)], check=True)
        got = [subprocess.run([str(exe)] + [x for x in seq.split(",") if x], capture_output=True, text=True).stdout.strip()
               for _, seq, _ in CASES]
        assert got != [want for _, _, want in CASES], f"planted bug {name!r} survived every case"
    finally:
        shutil.rmtree(d, ignore_errors=True)
