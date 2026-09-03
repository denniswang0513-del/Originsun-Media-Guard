# -*- coding: utf-8 -*-
"""前端請求預算（owner 2026-09-03：「存取都有點慢」）。

量出來的三個肇因與對應規則，各釘一條；改壞任何一條，這裡先紅：
  1. 分頁點到才載 —— 開頁只載落地那一頁（原本 30+ 分頁全載、~80 支 API）。
  2. 靜態檔走 ETag（no-cache 而非 no-store）；API 維持 no-store。
  3. 背景輪詢：無限期的狀態輪詢一律走 startVisiblePolling（分頁在背景／SPA 分頁被藏起來就不打）、
     本機代理活著與否問 health（status 會回整段 log 緩衝）、版本比對 60 秒一次；
     第一次載入的分頁 tab-changed 帶 fresh，「切回來要重抓」的鉤子不重抓。

🔴 app.js 的函式全部縮排在 8 格底下，_srcscan.js_func_body（以第 0 欄判下一個宣告）切不準，
這裡用顯式切片。
"""
from tests.unit._srcscan import js_code_only, repo_src


def _slice(src, start, end):
    i = src.index(start)
    return src[i:src.index(end, i)]


def test_tabs_load_on_first_switch_not_at_boot():
    app = js_code_only(repo_src("frontend/app.js"))
    boot = _slice(app, "async function loadTabs(", "initSelectAutoUpgrade();")
    assert "TAB_LOADERS" not in boot, "開頁不該把 TAB_LOADERS 全跑一遍——只載落地那一頁"
    assert "await switchTab(firstTab)" in boot
    assert "_ensureTabsLoaded" not in app                      # 登入後補載走同一支 _ensureTabLoaded
    loader = _slice(app, "const _loadTab = ", "window._ensureTabLoaded = _loadTab")
    assert "_loading.has(sectionId)" in loader                  # 同時兩處要同一頁：只載一次、init 一次
    assert "(!embed && !_authed(ld.key))" in loader             # embed：嵌別人的模組可跳過分頁權限
    assert "tabLoadError(status)" in loader                     # 載不到要說原因，不能停在「載入中…」
    assert "renderStandaloneHostPanels();" in loader            # 機隊勾選面板在分頁長出來時補
    sw = _slice(app, "async function switchTab(", "window._lastJob = null")
    assert "const fresh = _loadedTabs.has(tabId) ? false : await _loadTab(tabId)" in sw
    assert "detail: { tab: tabId, fresh }" in sw
    # 「切回來要重抓」的三個鉤子：剛載入的分頁 init 抓過了，fresh 就跳過
    assert "if (!e.detail.fresh) refreshList(" in js_code_only(repo_src("frontend/tabs/proposals/proposals.js"))
    assert "if (!e.detail.fresh) await loadProjects()" in js_code_only(repo_src("frontend/tabs/crm/crm-projects.js"))
    assert "if (e.detail.fresh) return;" in js_code_only(repo_src("frontend/tabs/finance/finance.js"))
    # 專案頁的報價子頁不自己灌 html —— 走同一支載入器（embed）才會記在 _loadedTabs
    assert "window._ensureTabLoaded('tab_crm_quotes', { embed: true })" in js_code_only(repo_src("frontend/tabs/crm/crm-projects-quotes.js"))
    # 開機段原本替備份頁設日期、綁轉檔勾選——那些 DOM 開機時已不存在，要住在備份頁自己的 init
    bk = js_code_only(repo_src("frontend/tabs/backup/backup.js"))
    init = _slice(bk, "export function initBackupTab(", "window.addSourceRow = addSourceRow")
    assert "getElementById('proj_name')" in init and "getElementById('chk_transcode')" in init
    assert "_ensureTabLoaded?.('tab_report')" in init            # 「最新備份報表」清單由 report.js 填


def test_static_files_revalidate_with_etag_api_stays_no_store():
    src = repo_src("main.py")
    mw = src[src.index("class NoCacheMiddleware"):src.index("class GzipJsonMiddleware")]
    assert 'b"cache-control", b"no-cache"' in mw and 'b"no-store' in mw
    assert 'path.startswith(("/api/", "/socket.io", "/download", "/healthz", "/e/"))' in mw


def test_background_polling_is_light():
    vc = js_code_only(repo_src("frontend/js/update/version-check.js"))
    poll = _slice(vc, "export async function pollLocalAgent(", "export function checkForceInstallModal(")
    assert "/api/v1/health" in poll and "/api/v1/status" not in poll
    ver = _slice(vc, "export async function checkAgentVersion(", "window.pollLocalAgent = pollLocalAgent")
    assert "_versionCheckedAt < 60000) return" in ver
    # 四個無限期的狀態輪詢都走同一支「看得見才打」
    assert "startVisiblePolling(pollLocalAgent, 3000)" in js_code_only(repo_src("frontend/app.js"))
    assert "startVisiblePolling(_checkHostHealth, 30000)" in js_code_only(repo_src("frontend/js/shared/utils.js"))
    web = js_code_only(repo_src("frontend/tabs/website/website.js"))
    assert web.count("startVisiblePolling(") == 2 and "sectionId: 'tab_website'" in web
    assert "document.hidden" not in web and "visibilityState" not in web   # 不各自再寫一份判定
