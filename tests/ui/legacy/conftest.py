"""Shared by the old-browser tests: pages that are always closed, and waiting for the
layer to finish instead of guessing a delay.

On CI (2026-11-06) a loaded runner measured before the layer had finished, the
tests failed, and every failure left its page open - dozens of whole pages, until
the runner ran out of memory and was shut down, twice."""
import pytest

from support.legacy_wait import settle  # noqa: F401  (the tests import it from here too)


@pytest.fixture
def pages(browser):
    """new_context() that is closed after the test however it ends."""
    made = []

    def new(**kw):
        ctx = browser.new_context(**kw)
        made.append(ctx)
        return ctx
    yield new
    for ctx in made:
        try:
            ctx.close()
        except Exception:
            pass
