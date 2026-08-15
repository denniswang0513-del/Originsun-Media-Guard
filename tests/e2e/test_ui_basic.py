# -*- coding: utf-8 -*-
"""E2E 基礎 UI：分群導覽（頂部群組 + 左側欄）與匿名可見面。

2026-08-15 對齊 tab 分群改版（原本的 `#btn_tab_*` 平鋪按鈕已不存在，這批
測試紅了一陣子）。匿名（未登入）看得到的是 MEDIA_TABS —— 後期製作是開放
工具（owner 拍板），所以這裡**不登入**，順便守住「匿名可用」這件事本身。
"""
import pytest

from .conftest import goto_tab

pytestmark = pytest.mark.e2e


# ── 1. 頁面標題 ─────────────────────────────────────────────
def test_page_title(page):
    page.screenshot(path="tests/e2e/screenshots/test_page_title.png")
    title = page.title()
    assert "originsun" in title.lower(), f"Unexpected title: {title}"


# ── 2. 匿名導覽面：兩顆群組鈕 + 後期製作左側欄的媒體 tab ────
def test_media_nav_visible_anonymously(page):
    """匿名要看得到專案總覽與後期製作群，且後期側欄有全部媒體 tab。

    正本是 tab-config 的 MEDIA_TABS —— 少一顆＝匿名可用面被誰的 RBAC 改動
    順手關掉了（「按鈕點了沒反應＝先查 401」那類事故的前一站）。
    """
    # 群組列是 renderGroupNav 畫的，可能比 loadTabs 晚一拍 —— 等，不要瞬時斷言
    for gbtn in ("#gbtn_projects", "#gbtn_production"):
        page.wait_for_selector(gbtn, state="visible", timeout=15000)

    page.click("#gbtn_production")
    # 側欄按鈕 id = sbtn_ + section id（app.js renderGroupSidebar）
    for sec in ("tab_main", "tab_verify", "tab_transcode", "tab_concat",
                "tab_report", "tab_transcribe", "tab_tts", "tab_drone_meta"):
        assert page.locator(f"#sbtn_{sec}").is_visible(), \
            f"後期側欄少了 #{sec} —— 匿名可用面縮水了"
    page.screenshot(path="tests/e2e/screenshots/test_all_tabs_visible.png")


# ── 3. 切換頁籤 ─────────────────────────────────────────────
def test_tab_switching(page):
    goto_tab(page, "production", "tab_verify")
    assert page.locator("#tab_verify").is_visible()
    # 只有一個 section 亮著 —— 切換壞掉最常見的形狀是兩個同時顯示
    visible = page.eval_on_selector_all(
        ".tab-content:not(.hidden)", "els => els.map(e => e.id)")
    assert visible == ["tab_verify"], f"同時亮著：{visible}"
    page.screenshot(path="tests/e2e/screenshots/test_tab_switching.png")


# ── 4. 備份頁籤欄位 ─────────────────────────────────────────
def test_backup_tab_fields(page):
    goto_tab(page, "production", "tab_main")
    assert page.locator("#proj_name").count() == 1, "專案名稱欄 #proj_name 不見了"
    assert page.locator('[onclick="submitJob()"]').count() >= 1, \
        "開始按鈕（submitJob）不見了"
    page.screenshot(path="tests/e2e/screenshots/test_backup_tab_fields.png")


# ── 5. 本機代理連線 ─────────────────────────────────────────
def test_local_agent_connected(page):
    """輪詢迴圈真的把「本機已連線」設起來。

    舊 `#status-badge` 已退役（index.html 註明「隱藏舊 badge，保留 ID 供 JS
    相容」）—— 對它斷言可見等於測一個死元素。UI 據以行動的正本是
    `window._localAgentActive`（createShortcut 那類功能的守門就是它）。
    """
    page.wait_for_function("() => window._localAgentActive === true", timeout=15000)
