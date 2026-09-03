# -*- coding: utf-8 -*-
"""前端請求預算（owner 2026-09-03：「存取都有點慢」）。

量出來的三個肇因與對應規則，各釘一條；改壞任何一條，這裡先紅：
  1. 分頁點到才載 —— 開頁只載落地那一頁（原本 30+ 分頁全載、~80 支 API）。
  2. 靜態檔走 ETag（no-cache 而非 no-store）；API 維持 no-store。
  3. 背景輪詢：本機代理活著與否問 health（status 會回整段 log 緩衝）、版本比對 60 秒一次、
     分頁在背景時不打；第一次載入的分頁 tab-changed 帶 fresh，鉤子不重抓。
"""
from tests.unit._srcscan import js_code_only, js_func_body, repo_src


def test_tabs_load_on_first_switch_not_at_boot():
    app = js_code_only(repo_src("frontend/app.js"))
    boot = js_func_body(app, "async function loadTabs(")
    assert "TAB_LOADERS" not in boot, "開頁不該把 TAB_LOADERS 全跑一遍——只載落地那一頁"
    assert "await switchTab(" in boot
    sw = js_func_body(app, "async function switchTab(")
    assert "await _loadTab(tabId)" in sw and "fresh" in sw
    assert "detail: { tab: tabId, fresh }" in sw
    assert "window._ensureTabLoaded = _loadTab" in app          # 跨分頁要人家的 DOM 時走這支（報價／e2e）
    # 「切回來要重抓」的兩個鉤子：剛載入的分頁 init 抓過了，fresh 就跳過
    assert "if (!e.detail.fresh) refreshList(" in js_code_only(repo_src("frontend/tabs/proposals/proposals.js"))
    assert "if (!e.detail.fresh) await loadProjects()" in js_code_only(repo_src("frontend/tabs/crm/crm-projects.js"))
    # 專案頁的報價子頁不自己灌 html —— 走同一支載入器才會記在 _loadedTabs
    assert "window._ensureTabLoaded('tab_crm_quotes')" in js_code_only(repo_src("frontend/tabs/crm/crm-projects-quotes.js"))


def test_static_files_revalidate_with_etag_api_stays_no_store():
    src = repo_src("main.py")
    mw = src[src.index("class NoCacheMiddleware"):src.index("class GzipJsonMiddleware")]
    assert 'b"cache-control", b"no-cache"' in mw and 'b"no-store' in mw
    assert 'path.startswith("/api/")' in mw and 'path.startswith("/socket.io")' in mw


def test_background_polling_is_light():
    vc = js_code_only(repo_src("frontend/js/update/version-check.js"))
    poll = js_func_body(vc, "export async function pollLocalAgent(")
    assert "/api/v1/health" in poll and "/api/v1/status" not in poll
    assert "if (document.hidden) return" in poll
    ver = js_func_body(vc, "export async function checkAgentVersion(")
    assert "_versionCheckedAt < 60000) return" in ver
    hosts = js_func_body(js_code_only(repo_src("frontend/js/shared/utils.js")), "async function _checkHostHealth(")
    assert "if (document.hidden) return" in hosts
