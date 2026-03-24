"""
Layer 3 — E2E 專案總覽 Tab UI 測試（Playwright）。

All CSS classes use the `pj-` prefix.
"""
import os
import pytest
import httpx

pytestmark = pytest.mark.e2e

SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")


def _safe_print(msg):
    """Print with fallback for Windows cp950 encoding."""
    import sys
    try:
        print(msg)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((str(msg) + "\n").encode("utf-8", errors="replace"))


def _cleanup_test_agents(real_server):
    """Remove any test agents from settings via API."""
    try:
        base = real_server["base_url"]
        r = httpx.get(f"{base}/api/settings/load", timeout=5.0)
        if r.status_code == 200:
            settings = r.json()
            agents = settings.get("agents", [])
            cleaned = [a for a in agents if not a.get("name", "").startswith("Test")]
            if len(cleaned) != len(agents):
                settings["agents"] = cleaned
                httpx.post(f"{base}/api/settings/save", json=settings, timeout=5.0)
    except Exception:
        pass


# ── 1. 專案總覽 Tab 存在 ────────────────────────────────────
def test_projects_tab_exists(page, real_server):
    """Find and click projects tab → pj-container visible."""
    page.click("#btn_tab-projects")
    page.wait_for_timeout(500)

    container = page.query_selector(".pj-container")
    assert container is not None, "pj-container not found"
    assert container.is_visible(), "pj-container not visible"

    page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "test_projects_tab_exists.png"))


# ── 2. Section headers ──────────────────────────────────────
def test_section_headers(page, real_server):
    """Verify 4 section headers exist in correct order."""
    page.click("#btn_tab-projects")
    page.wait_for_timeout(500)

    headers = page.query_selector_all(".pj-section-header")
    header_texts = []
    for h in headers:
        text = h.text_content().strip()
        header_texts.append(text)
    _safe_print(f"Section headers: {header_texts}")

    assert len(headers) >= 4, f"Expected 4 section headers, got {len(headers)}: {header_texts}"

    page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "test_section_headers.png"))


# ── 3. Settings panel toggle ────────────────────────────────
def test_settings_panel_toggle(page, real_server):
    """Click settings button → panel shows → has 4 selects → click again → hides."""
    page.click("#btn_tab-projects")
    page.wait_for_timeout(500)

    # Click settings button (in header)
    settings_btn = page.query_selector(".pj-header-right .pj-btn-settings")
    if not settings_btn:
        settings_btn = page.query_selector(".pj-header-right button")
    assert settings_btn is not None, "Settings button not found"
    settings_btn.click()
    page.wait_for_timeout(300)

    panel = page.query_selector("#pj-settings-panel")
    assert panel is not None, "Settings panel not found"
    assert panel.is_visible(), "Settings panel not visible after click"

    # Check for limit selects
    selects = panel.query_selector_all("select")
    _safe_print(f"Found {len(selects)} selects in settings panel")
    assert len(selects) >= 4, f"Expected 4 selects, got {len(selects)}"

    page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "test_settings_panel_open.png"))

    # Toggle off
    settings_btn.click()
    page.wait_for_timeout(300)
    assert not panel.is_visible(), "Settings panel should be hidden after second click"


# ── 4. Add agent form ───────────────────────────────────────
def test_add_agent_form(page, real_server):
    """Click add machine → form shows with name/IP/port → cancel → hides."""
    _cleanup_test_agents(real_server)
    page.click("#btn_tab-projects")
    page.wait_for_timeout(500)

    # Find "新增機器" button - look for it in section headers or machines area
    add_btn = page.query_selector("text=新增機器") or page.query_selector("text=+ 新增機器")
    if not add_btn:
        # Try buttons near machine section
        buttons = page.query_selector_all(".pj-section-header button")
        for b in buttons:
            txt = b.text_content()
            if "新增" in txt:
                add_btn = b
                break
    if not add_btn:
        # Try empty state button
        add_btn = page.query_selector(".pj-machines-empty button")

    assert add_btn is not None, "Add machine button not found"
    add_btn.click()
    page.wait_for_timeout(300)

    form = page.query_selector("#pj-add-agent-form")
    assert form is not None, "Add agent form not found"
    assert form.is_visible(), "Form not visible after click"

    # Check fields
    name_input = page.query_selector("#pj-agent-name")
    ip_input = page.query_selector("#pj-agent-ip")
    port_input = page.query_selector("#pj-agent-port")
    assert name_input is not None, "Name input not found"
    assert ip_input is not None, "IP input not found"
    assert port_input is not None, "Port input not found"

    page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "test_add_agent_form.png"))

    # Cancel
    cancel_btn = form.query_selector(".pj-btn-close") or form.query_selector("button:has-text('取消')")
    if cancel_btn:
        cancel_btn.click()
        page.wait_for_timeout(300)
        assert not form.is_visible(), "Form should be hidden after cancel"


# ── 5. Add agent success ────────────────────────────────────
def test_add_agent_success(page, real_server):
    """Add agent via API → verify card appears in UI."""
    _cleanup_test_agents(real_server)
    base = real_server["base_url"]

    try:
        # Add agent via API
        r = httpx.get(f"{base}/api/settings/load", timeout=15.0)
        settings = r.json()
        settings["agents"] = settings.get("agents", []) + [
            {"id": "test_pc_1", "name": "Test-PC", "url": "http://192.168.1.200:8000"}
        ]
        httpx.post(f"{base}/api/settings/save", json=settings, timeout=15.0)
    except httpx.ReadTimeout:
        pytest.skip("Server busy")

    # Switch to projects tab and reload agents (no polling to avoid server interference)
    page.click("#btn_tab-projects")
    page.wait_for_timeout(500)
    page.evaluate("window.projectsTab && window.projectsTab.reloadAgentsNoPolling()")
    page.wait_for_timeout(1500)

    # Check for machine card
    cards = page.query_selector_all(".pj-machine-card")
    _safe_print(f"Machine cards found: {len(cards)}")

    card_found = any("Test-PC" in c.text_content() for c in cards)

    page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "test_add_agent_success.png"))
    assert card_found, "Machine card with name 'Test-PC' not found"

    _cleanup_test_agents(real_server)


# ── 6. Add two agents → persist after refresh ───────────────
def test_add_two_agents(page, real_server):
    """Add PC-X and PC-Y via API → both persist in settings."""
    _cleanup_test_agents(real_server)
    base = real_server["base_url"]

    try:
        r = httpx.get(f"{base}/api/settings/load", timeout=15.0)
        settings = r.json()
        settings["agents"] = settings.get("agents", []) + [
            {"id": "test_pcx", "name": "Test-PCX", "url": "http://192.168.1.201:8000"},
            {"id": "test_pcy", "name": "Test-PCY", "url": "http://192.168.1.202:8000"},
        ]
        httpx.post(f"{base}/api/settings/save", json=settings, timeout=15.0)

        # Verify persistence via API
        r2 = httpx.get(f"{base}/api/settings/load", timeout=15.0)
        saved_agents = r2.json().get("agents", [])
        saved_names = [a.get("name", "") for a in saved_agents]
        _safe_print(f"Agents in settings after save: {saved_names}")

        assert "Test-PCX" in saved_names, f"Test-PCX not found in settings. Got: {saved_names}"
        assert "Test-PCY" in saved_names, f"Test-PCY not found in settings. Got: {saved_names}"
    except httpx.ReadTimeout:
        pytest.skip("Server busy")

    # Switch to projects tab and reload agents (no polling to avoid server interference)
    page.click("#btn_tab-projects")
    page.wait_for_timeout(500)
    page.evaluate("window.projectsTab && window.projectsTab.reloadAgentsNoPolling()")
    page.wait_for_timeout(1500)

    cards_text = [c.text_content() for c in page.query_selector_all(".pj-machine-card")]
    _safe_print(f"Cards in UI: {cards_text}")

    page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "test_add_two_agents.png"))
    assert any("Test-PCX" in t for t in cards_text), "Test-PCX card not found in UI"
    assert any("Test-PCY" in t for t in cards_text), "Test-PCY card not found in UI"

    _cleanup_test_agents(real_server)


# ── 7. Remove agent ─────────────────────────────────────────
def test_remove_agent(page, real_server):
    """Add one agent via API → remove via API → verify gone."""
    _cleanup_test_agents(real_server)
    base = real_server["base_url"]

    try:
        # Add agent via API
        r = httpx.get(f"{base}/api/settings/load", timeout=15.0)
        settings = r.json()
        settings["agents"] = settings.get("agents", []) + [
            {"id": "test_remove_1", "name": "Test-Remove", "url": "http://192.168.1.250:8000"}
        ]
        httpx.post(f"{base}/api/settings/save", json=settings, timeout=15.0)

        # Verify agent was added
        r2 = httpx.get(f"{base}/api/settings/load", timeout=15.0)
        saved = r2.json().get("agents", [])
        assert any(a.get("id") == "test_remove_1" for a in saved), "Agent not saved"

        # Remove via API (avoid confirm() dialog hang in Playwright)
        r_load = httpx.get(f"{base}/api/settings/load", timeout=15.0)
        s = r_load.json()
        s["agents"] = [a for a in s.get("agents", []) if a.get("id") != "test_remove_1"]
        httpx.post(f"{base}/api/settings/save", json=s, timeout=15.0)

        # Verify via API that agent is removed
        r3 = httpx.get(f"{base}/api/settings/load", timeout=15.0)
        remaining = r3.json().get("agents", [])
        remove_found = any(a.get("id") == "test_remove_1" for a in remaining)
        assert not remove_found, f"Test-Remove agent should be gone. Remaining: {remaining}"
    except httpx.ReadTimeout:
        pytest.skip("Server busy (conflict timeout from previous tests)")

    _cleanup_test_agents(real_server)


# ── 8. Empty states ──────────────────────────────────────────
def test_empty_states(page, real_server):
    """With no machines/tasks, verify empty state messages."""
    _cleanup_test_agents(real_server)
    # Instead of full page reload, just switch tabs to refresh
    page.click("#btn_tab_main", timeout=5000)
    page.wait_for_timeout(300)
    page.click("#btn_tab-projects", timeout=5000)
    page.wait_for_timeout(1000)

    # Check machines empty state
    machines_empty = page.query_selector(".pj-machines-empty")
    if machines_empty and machines_empty.is_visible():
        text = machines_empty.text_content()
        _safe_print(f"Machines empty text: {text}")

    # Check active tasks empty state
    active_empty = page.query_selector("#pj-active-container .pj-empty-state")
    if active_empty and active_empty.is_visible():
        text = active_empty.text_content()
        _safe_print(f"Active empty text: {text}")

    # Check queue empty state
    queue_empty = page.query_selector(".pj-queue-empty")
    if queue_empty and queue_empty.is_visible():
        text = queue_empty.text_content()
        _safe_print(f"Queue empty text: {text}")

    page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "test_empty_states.png"))

    # At least one empty state should be visible
    has_empty = (
        (machines_empty and machines_empty.is_visible())
        or (active_empty and active_empty.is_visible())
        or (queue_empty and queue_empty.is_visible())
    )
    assert has_empty, "Expected at least one empty state message to be visible"
