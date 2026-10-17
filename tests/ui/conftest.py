"""One Chromium per test process for the whole ui tier.

Playwright's sync API allows one running instance per process; under
pytest-xdist the BDD scenarios, the responsive matrix and the older dashboard
tests share a worker, so they share this browser. Each test opens its own
context, which is what isolates them.
"""
import pytest


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()
