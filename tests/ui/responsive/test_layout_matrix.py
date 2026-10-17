"""Every screen, every state: the dashboard drawn on the whole responsive matrix
(support/responsive.py) and measured by support/responsive_probe.js.

Problems that already exist are recorded per OS in
tests/golden/ui_responsive_known.<os>.json (a ratchet, like the older 67-size
sweep): a new problem fails here, with the viewport and state that show it; a
fixed one fails the session until it is taken off the list, so it cannot come
back unnoticed. The full result is written to tests/.reports/responsive.md.
"""
import json
import platform

import pytest

from support import paths, responsive as R, responsive_run as RR

pytestmark = pytest.mark.ui

MATRIX = R.matrix()
CHUNK = 8
CHUNKS = [MATRIX[i:i + CHUNK] for i in range(0, len(MATRIX), CHUNK)]
KNOWN = paths.GOLDEN / f"ui_responsive_known.{platform.system().lower()}.json"


def _known():
    return json.loads(KNOWN.read_text()) if KNOWN.exists() else None


@pytest.mark.parametrize("chunk", CHUNKS, ids=[f"{i:02d}-{c[0].name.replace(' ', '_')}-{c[0].w}x{c[0].h}"
                                                for i, c in enumerate(CHUNKS)])
def test_every_state_on_every_screen(browser, chunk):
    found, errors = {}, []
    for vp in chunk:
        ctx, page, sim, errs = RR.open_page(browser, vp)
        try:
            res = RR.walk(page)
        finally:
            ctx.close()
        found[vp.id] = sorted({b for r in res.values() for b in r["bad"]})
        R.save_part(vp, res)
        errors += [f"{vp.id}: {e}" for e in errs]
    assert not errors, "JavaScript errors while drawing:\n  " + "\n  ".join(errors)
    known = _known()
    if known is None:
        pytest.skip(f"no baseline yet - the session writes {KNOWN.name} under --update-golden")
    new = [f"{v} :: {p}" for v, ps in found.items() for p in ps if p not in known.get(v, [])]
    assert not new, f"{len(new)} NEW layout problem(s):\n  " + "\n  ".join(new[:60])
