"""Waiting for the old-browser layer (docs/LEGACY_DEVICES.md) to finish, instead of
guessing a delay - tests/ui/legacy uses it."""

SETTLE_JS = """async ([quiet, limit]) => {
  const frame = () => new Promise(r => requestAnimationFrame(() => r()));
  const L = window.__hidfiLegacy, end = performance.now() + limit;
  let last = -1;
  // Done means nothing queued AND the pass count unchanged for a while. The count
  // alone was not enough: on a busy CI runner the next frame - with the pass
  // already queued for it - came later than the quiet window, and four checks
  // measured a page the layer had not reached yet.
  while (performance.now() < end) {
    const runs = L ? (L.runs || 0) : 0, queued = !!(L && L.busy && L.busy());
    if (!queued && runs === last) break;
    last = queued ? -1 : runs;
    await new Promise(r => setTimeout(r, quiet));
  }
  await frame(); await frame();
}"""


def settle(page, quiet_ms=250, limit_s=10):
    """Until the old-browser layer (if it runs) has stopped working, then two frames."""
    page.evaluate(SETTLE_JS, [quiet_ms, limit_s * 1000])
