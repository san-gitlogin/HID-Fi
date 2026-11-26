"""One Chromium per test process for the whole ui tier.

Playwright's sync API allows one running instance per process; under
pytest-xdist the BDD scenarios, the responsive matrix and the older dashboard
tests share a worker, so they share this browser. Each test opens its own
context, which is what isolates them.
"""
import pytest


@pytest.fixture(scope="session")
def pw():
    """The one Playwright of this process (the Companion window tests launch
    WebKit from it too)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(pw):
    # HIDFI_UI_BROWSER=webkit: the same tier in Safari's engine (an iPhone's behaviour
    # that Chromium does not have - "Tap for sound" froze the picture, 2026-11-26).
    import os
    b = pw.webkit.launch() if os.environ.get("HIDFI_UI_BROWSER") == "webkit" else pw.chromium.launch()
    yield b
    b.close()
