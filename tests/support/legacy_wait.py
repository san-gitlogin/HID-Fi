"""Waiting for the old-browser layer (docs/LEGACY_DEVICES.md) to finish, instead of
guessing a delay - tests/ui/legacy uses it."""

SETTLE_JS = """async ([quiet, limit]) => {
  const frame = () => new Promise(r => requestAnimationFrame(() => r()));
  const L = window.__hidfiLegacy, end = performance.now() + limit;
  let last = -1;
  while (performance.now() < end) {          // the layer's pass count, unchanged for a while
    const runs = L ? (L.runs || 0) : 0;
    if (runs === last) break;
    last = runs;
    await new Promise(r => setTimeout(r, quiet));
  }
  await frame(); await frame();
}"""


def settle(page, quiet_ms=250, limit_s=10):
    """Until the old-browser layer (if it runs) has stopped working, then two frames."""
    page.evaluate(SETTLE_JS, [quiet_ms, limit_s * 1000])
