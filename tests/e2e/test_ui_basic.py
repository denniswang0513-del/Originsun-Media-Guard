"""
Layer 3 — E2E 基礎 UI 測試（Playwright）。
"""
import pytest

pytestmark = pytest.mark.e2e


def _safe_print(msg):
    """Print with fallback encoding to avoid cp950 errors on Windows."""
    import sys
    try:
        print(msg)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((str(msg) + "\n").encode("utf-8", errors="replace"))


# ── 1. 頁面標題 ─────────────────────────────────────────────
def test_page_title(page):
    """Page title should contain 'originsun'."""
    page.screenshot(path="tests/e2e/screenshots/test_page_title.png")
    title = page.title()
    assert "originsun" in title.lower(), f"Unexpected title: {title}"


# ── 2. 所有頁籤按鈕可見 ─────────────────────────────────────
def test_all_tabs_visible(page):
    """All 8 tab buttons should be visible."""
    # Print all button text for debugging
    buttons = page.query_selector_all("button")
    tab_texts = [b.text_content().strip() for b in buttons if b.is_visible()]
    _safe_print(f"Visible buttons: {tab_texts}")

    expected_tabs = [
        ("btn_tab-projects", "專案總覽"),
        ("btn_tab_main", "備份"),
        ("btn_tab_verify", "比對"),
        ("btn_tab_transcode", "Proxy"),
        ("btn_tab_concat", "串帶"),
        ("btn_tab_report", "報表"),
        ("btn_tab_transcribe", "逐字稿"),
        ("btn_tab_tts", "語音"),
    ]

    for btn_id, label_hint in expected_tabs:
        btn = page.query_selector(f"#{btn_id}")
        assert btn is not None, f"Tab button #{btn_id} ({label_hint}) not found"

    page.screenshot(path="tests/e2e/screenshots/test_all_tabs_visible.png")


# ── 3. 切換頁籤 ─────────────────────────────────────────────
def test_tab_switching(page):
    """Click verify tab → verify section becomes visible."""
    page.click("#btn_tab_verify")
    page.wait_for_timeout(500)

    # Print section visibility
    sections = ["tab-projects", "tab_main", "tab_verify", "tab_transcode",
                "tab_concat", "tab_report", "tab_transcribe", "tab_tts"]
    for sid in sections:
        el = page.query_selector(f"#{sid}")
        if el:
            visible = el.is_visible()
            print(f"  #{sid}: visible={visible}")

    verify_section = page.query_selector("#tab_verify")
    assert verify_section is not None, "Verify section not found"
    assert verify_section.is_visible(), "Verify section not visible after click"

    page.screenshot(path="tests/e2e/screenshots/test_tab_switching.png")


# ── 4. 備份頁籤欄位 ─────────────────────────────────────────
def test_backup_tab_fields(page):
    """Backup tab should have project name input and start button."""
    page.click("#btn_tab_main")
    page.wait_for_timeout(500)

    # Print all input ids
    inputs = page.query_selector_all("input")
    for inp in inputs:
        inp_id = inp.get_attribute("id") or ""
        placeholder = inp.get_attribute("placeholder") or ""
        if inp.is_visible():
            print(f"  input id={inp_id} placeholder={placeholder}")

    # Project name field
    proj_input = page.query_selector("#proj_name")
    assert proj_input is not None, "Project name input #proj_name not found"

    # Start button
    start_btn = page.query_selector('[onclick="submitJob()"]')
    assert start_btn is not None, "Start button (submitJob) not found"

    page.screenshot(path="tests/e2e/screenshots/test_backup_tab_fields.png")


# ── 5. Socket 連線狀態 ──────────────────────────────────────
def test_socket_connected(page):
    """Socket status badge should be visible."""
    # Print connection-related elements
    status = page.query_selector("#status-badge")
    if status:
        _safe_print(f"  status-badge text: {status.text_content()}")
        _safe_print(f"  status-badge visible: {status.is_visible()}")

    page.wait_for_selector("#status-badge", timeout=8000)
    badge = page.query_selector("#status-badge")
    assert badge is not None, "Status badge not found"
    assert badge.is_visible(), "Status badge not visible"

    page.screenshot(path="tests/e2e/screenshots/test_socket_connected.png")
