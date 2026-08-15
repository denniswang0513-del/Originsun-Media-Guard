# -*- coding: utf-8 -*-
"""E2E 專案總覽 Tab（`pj-` 前綴）。

2026-08-15 對齊兩次改版：導覽走分群（goto_tab），機器管理走
`/api/v1/agents`（admin 守衛）—— 舊測試寫的 `settings.json["agents"]`
早就沒人讀了（機器清單住 DB，NAS json 只是 fallback），而
`/api/settings/save` 現在要 admin，匿名 POST 靜默失敗讓那批測試紅了一陣子。

🔴 金絲雀鐵則：dev 的 agents 清單＝**真實生產機隊**。這裡只動名字帶
`_TAG` 的列（開頭掃殘留、finally 必刪），URL 用 TEST-NET（192.0.2.x，
RFC 5737 保證不路由）—— 舊測試用 192.168.1.200，那是哪天真的有機器插上
就會被健康輪詢打到的位址。
"""
import os
from contextlib import contextmanager

import pytest

from .conftest import HTTP, goto_tab

pytestmark = pytest.mark.e2e

SCREENSHOTS_DIR = os.path.join(os.path.dirname(__file__), "screenshots")

_TAG = "E2E測試機"


def _safe_print(msg):
    """Print with fallback for Windows cp950 encoding."""
    import sys
    try:
        print(msg)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((str(msg) + "\n").encode("utf-8", errors="replace"))


def _sweep_test_agents(base, h):
    """清掉上一輪殘留的測試機器 —— 只認 `_TAG` 開頭的名字，掃錯範圍就是把
    真機從機隊名單裡刪掉。"""
    for a in HTTP.get(f"{base}/api/v1/agents").json().get("agents", []):
        if (a.get("name") or "").startswith(_TAG):
            HTTP.delete(f"{base}/api/v1/agents/{a['id']}", headers=h)


@contextmanager
def _agent_case(base, token, names):
    """自建自刪 N 台測試機器。寫的是 dev DB 的 agents 表（也會 best-effort
    同步 NAS json 備份），所以建/收都走正式 API、絕不繞道。"""
    h = {"Authorization": f"Bearer {token}"}
    _sweep_test_agents(base, h)
    made = []
    try:
        for i, n in enumerate(names):
            r = HTTP.post(f"{base}/api/v1/agents", headers=h,
                          json={"name": n, "url": f"http://192.0.2.{10 + i}:8000"})
            assert r.status_code == 200, f"建測試機器失敗 {r.status_code}: {r.text[:200]}"
            made.append(r.json()["agent"])
        yield {"h": h, "agents": made}
    finally:
        for a in made:
            HTTP.delete(f"{base}/api/v1/agents/{a['id']}", headers=h)


def _reload_cards(page, expect_text):
    """讓 projects tab 重抓機器清單（不開輪詢），等到某張卡真的出現。"""
    goto_tab(page, "projects", "tab-projects")
    page.evaluate("window.projectsTab && window.projectsTab.reloadAgentsNoPolling()")
    page.wait_for_function(
        """t => [...document.querySelectorAll('.pj-machine-card')]
                 .some(c => c.textContent.includes(t))""",
        arg=expect_text, timeout=15000)


# ── 1. 專案總覽 Tab 存在 ────────────────────────────────────
def test_projects_tab_exists(page, real_server):
    """Find and click projects tab → pj-container visible."""
    goto_tab(page, "projects", "tab-projects")

    container = page.query_selector(".pj-container")
    assert container is not None, "pj-container not found"
    assert container.is_visible(), "pj-container not visible"

    page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "test_projects_tab_exists.png"))


# ── 2. Section headers ──────────────────────────────────────
def test_section_headers(page, real_server):
    """Verify 4 section headers exist in correct order."""
    goto_tab(page, "projects", "tab-projects")

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
    goto_tab(page, "projects", "tab-projects")

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


# ── 4. Add agent form（admin-only UI）───────────────────────
def test_add_agent_form(browser_context, real_server, e2e_admin_token):
    """「+ 新增機器」是 admin-only（匿名連按鈕都看不到）—— 用登入的獨立分頁
    驗表單開闔。獨立分頁是刻意的：共用的 `page` 是匿名 session，在它身上
    登入會把後面每一支匿名測試的前提弄髒。"""
    p = browser_context.new_page()
    try:
        base = real_server["base_url"]
        p.goto(base + "/", timeout=60000)
        p.evaluate("t => localStorage.setItem('auth_token', t)", e2e_admin_token)
        p.reload(timeout=60000)
        goto_tab(p, "projects", "tab-projects")
        # admin-only 元素由登入流程非同步揭示 —— 等它真的可見
        p.wait_for_selector(".pj-btn-settings.admin-only", state="visible",
                            timeout=15000)
        p.click(".pj-btn-settings.admin-only")

        form = p.locator("#pj-add-agent-form")
        assert form.is_visible(), "按了新增機器，表單沒出現"
        for fid in ("#pj-agent-name", "#pj-agent-ip", "#pj-agent-port"):
            assert p.locator(fid).count() == 1, f"表單缺欄位 {fid}"
        p.screenshot(path=os.path.join(SCREENSHOTS_DIR, "test_add_agent_form.png"))

        p.click(".pj-btn-settings.admin-only")     # 再按一次＝收起
        assert not form.is_visible(), "再按一次應該收起表單"
    finally:
        p.close()


# ── 5. Add agent → card appears in UI ───────────────────────
def test_add_agent_success(page, real_server, e2e_admin_token, dev_db_only):
    """API 建一台 → 專案總覽真的長出那張機器卡（匿名也看得到機器狀態）。"""
    with _agent_case(real_server["base_url"], e2e_admin_token, [f"{_TAG}A"]):
        _reload_cards(page, f"{_TAG}A")
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "test_add_agent_success.png"))


# ── 6. Add two agents ───────────────────────────────────────
def test_add_two_agents(page, real_server, e2e_admin_token, dev_db_only):
    """一次兩台都要出現（id 去重那段邏輯的 UI 面）。"""
    with _agent_case(real_server["base_url"], e2e_admin_token,
                     [f"{_TAG}X", f"{_TAG}Y"]):
        _reload_cards(page, f"{_TAG}X")
        _reload_cards(page, f"{_TAG}Y")
        page.screenshot(path=os.path.join(SCREENSHOTS_DIR, "test_add_two_agents.png"))


# ── 7. Remove agent ─────────────────────────────────────────
def test_remove_agent(real_server, e2e_admin_token, dev_db_only):
    """API 建一台 → API 刪掉 → 清單裡真的沒了（不是只有畫面上消失）。"""
    base = real_server["base_url"]
    with _agent_case(base, e2e_admin_token, [f"{_TAG}R"]) as c:
        aid = c["agents"][0]["id"]
        r = HTTP.delete(f"{base}/api/v1/agents/{aid}", headers=c["h"])
        assert r.status_code == 200, r.text[:200]
        ids = [a["id"] for a in HTTP.get(f"{base}/api/v1/agents").json()["agents"]]
        assert aid not in ids, f"刪了還在清單裡：{aid}"
        c["agents"].clear()          # 已刪乾淨，finally 不必再刪一次


# ── 8. Empty states ──────────────────────────────────────────
def test_empty_states(page, real_server):
    """With no machines/tasks, verify empty state messages."""
    # Instead of full page reload, just switch tabs to refresh
    goto_tab(page, "production", "tab_main")
    goto_tab(page, "projects", "tab-projects")
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
