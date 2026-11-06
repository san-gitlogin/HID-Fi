"""docs/DECISIONS.md: every decision names the tests that hold it, and every one of
them must exist - a decision whose guard was deleted or renamed is a decision
nobody is keeping any more."""
import re

import pytest

from support import paths

pytestmark = pytest.mark.static
ROOT = paths.TESTS.parent
DOC = ROOT / "docs" / "DECISIONS.md"


def guards():
    text = DOC.read_text(encoding="utf-8")
    out = []
    for block in re.split(r"\n## ", text)[1:]:
        title = block.splitlines()[0]
        g = block.split("**Guarded by:**", 1)
        assert len(g) == 2, f"{title}: no 'Guarded by' list"
        refs = re.findall(r"^- `([^`]+)`", g[1], re.M)
        assert refs, f"{title}: names no test"
        out += [(title, r) for r in refs]
    return out


def test_every_decision_names_its_guards():
    assert len(guards()) >= 10


@pytest.mark.parametrize("title, ref", guards(), ids=lambda v: v if "/" in str(v) else str(v)[:40])
def test_every_guard_exists(title, ref):
    if ": " in ref and ref.split(": ", 1)[0].endswith(".feature"):
        path, scenario = ref.split(": ", 1)
        text = (ROOT / path).read_text(encoding="utf-8")
        assert re.search(r"^\s*Scenario(?: Outline)?: " + re.escape(scenario) + r"\s*$", text, re.M), \
            f"{title}: no scenario {scenario!r} in {path}"
        return
    path, name = ref.split("::", 1)
    text = (ROOT / path).read_text(encoding="utf-8")
    pat = r"^\s*def " if path.endswith(".py") else r"^\s*(?:pub\s+)?fn "
    assert re.search(pat + re.escape(name) + r"\b", text, re.M), f"{title}: no test {name} in {path}"
