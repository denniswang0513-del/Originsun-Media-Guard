"""
E2E test fixtures using Playwright + real server.
"""
import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope="session")
def browser_context():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        yield context
        context.close()
        browser.close()


@pytest.fixture(scope="session")
def page(browser_context, real_server):
    p = browser_context.new_page()
    p.on("console", lambda msg: print(f"[BROWSER {msg.type}] {msg.text}"))
    p.goto(real_server["base_url"] + "/", timeout=60000)
    p.wait_for_load_state("domcontentloaded")
    p.wait_for_timeout(3000)  # Allow dynamic tabs to load
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _check_server_alive(real_server):
    """Check test server process is alive before each test."""
    proc = real_server.get("_proc")
    if proc and proc.poll() is not None:
        pytest.skip(f"Test server process died (rc={proc.returncode})")
    yield
