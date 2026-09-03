# -*- coding: utf-8 -*-
"""前端請求預算（owner 2026-09-03：「存取都有點慢」）。

量出來的三個肇因與對應規則，各釘一條；改壞任何一條，這裡先紅：
  1. 分頁點到才載 —— 開頁只載落地那一頁（原本 30+ 分頁全載、~80 支 API）；分頁 DOM 開機時不存在，
     預設值都住在各分頁自己的 init。
  2. 有驗證器（ETag）的靜態檔走 no-cache／304；JSON 沒驗證器一律 no-store。
  3. 背景輪詢：無限期的狀態輪詢一律走 startVisiblePolling（分頁在背景／SPA 分頁被藏起來就不打）、
     本機代理活著與否問 health（status 會回整段 log 緩衝）、版本比對 60 秒一次；
     第一次載入的分頁 tab-changed 帶 fresh，「切回來要重抓」的鉤子不重抓。
"""
import pytest

from tests.unit._srcscan import between, js_code_only, js_func_body, repo_src


def test_tabs_load_on_first_switch_not_at_boot():
    app = js_code_only(repo_src("frontend/app.js"))
    boot = js_func_body(app, "async function loadTabs(")
    assert "TAB_LOADERS" not in boot, "開頁不該把 TAB_LOADERS 全跑一遍——只載落地那一頁"
    assert "await switchTab(firstTab)" in boot
    assert "_ensureTabsLoaded" not in app                      # 登入後補載走同一支 _ensureTabLoaded
    loader = between(app, "const _loadTab = ", "window._ensureTabLoaded = _loadTab")
    assert "_loading.has(sectionId)" in loader                  # 同時兩處要同一頁：只載一次、init 一次
    assert "(!embed && !_authed(ld.key))" in loader             # embed：嵌別人的模組可跳過分頁權限
    assert "tabLoadError(status)" in loader                     # 載不到要說原因，不能停在「載入中…」
    assert "_refreshHostPanels();" in loader                    # 機隊勾選面板在分頁長出來時補
    sw = js_func_body(app, "async function switchTab(")
    assert "const fresh = await _loadTab(tabId)" in sw          # 載不載由載入器決定，switchTab 不自己猜
    assert "detail: { tab: tabId, fresh }" in sw
    assert "tab_report" not in sw                               # 殼層不認得個別分頁的欄位（報表頁自己接 tab-changed）
    # 「切回來要重抓」的鉤子：剛載入的分頁 init 抓過了，fresh 就跳過
    assert "if (!e.detail.fresh) refreshList(" in js_code_only(repo_src("frontend/tabs/proposals/proposals.js"))
    assert "if (!e.detail.fresh) await loadProjects()" in js_code_only(repo_src("frontend/tabs/crm/crm-projects.js"))
    assert "if (e.detail.fresh) return;" in js_code_only(repo_src("frontend/tabs/finance/finance.js"))
    # 專案頁的報價子頁不自己灌 html —— 走同一支載入器（embed）才會記在 _loadedTabs
    assert "window._ensureTabLoaded('tab_crm_quotes', { embed: true })" in js_code_only(repo_src("frontend/tabs/crm/crm-projects-quotes.js"))


def test_tab_defaults_live_in_their_own_init():
    """開機段原本替備份頁設日期、報表頁設檔名——那些 DOM 開機時已不存在，要住在各分頁的 init。"""
    bk = js_func_body(js_code_only(repo_src("frontend/tabs/backup/backup.js")), "export function initBackupTab(")
    assert "setTodayName()" in bk and "loadReportHistory()" in bk
    rp = js_func_body(js_code_only(repo_src("frontend/tabs/report/report.js")), "export function initReportTab(")
    assert "todayStamp('_Report')" in rp and "loadReportHistory()" in rp and "tab-changed" in rp
    # 報表歷史清單兩頁共用 → 住 js/shared，備份頁不必為了它把整個報表分頁載進來
    shared = js_code_only(repo_src("frontend/js/shared/report-history.js"))
    assert "export async function loadReportHistory(" in shared and "window.loadReportHistory = loadReportHistory" in shared
    assert "_ensureTabLoaded" not in js_code_only(repo_src("frontend/tabs/backup/backup.js"))


async def test_static_files_revalidate_with_etag_api_stays_no_store(async_client):
    r = await async_client.get("/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache" and r.headers.get("etag")
    r304 = await async_client.get("/app.js", headers={"If-None-Match": r.headers["etag"]})
    assert r304.status_code == 304
    for path in ("/api/v1/version", "/healthz"):
        rr = await async_client.get(path)
        assert rr.headers["cache-control"].startswith("no-store"), path
    assert '_NO_STORE_FILES = ("/download", "/e/")' in repo_src("main.py")   # 例外要留著理由


def test_background_polling_is_light():
    vc = js_code_only(repo_src("frontend/js/update/version-check.js"))
    poll = js_func_body(vc, "export async function pollLocalAgent(")
    assert "/api/v1/health" in poll and "/api/v1/status" not in poll
    assert "_versionCheckedAt < 60000) return" in js_func_body(vc, "export async function checkAgentVersion(")
    # 四個無限期的狀態輪詢都走同一支「看得見才打」（它自己打第一發，呼叫端不必再手動打）
    utils = js_code_only(repo_src("frontend/js/shared/utils.js"))
    assert "fn();" in js_func_body(utils, "export function startVisiblePolling(")
    assert "startVisiblePolling(pollLocalAgent, 3000)" in js_code_only(repo_src("frontend/app.js"))
    assert "startVisiblePolling(_checkHostHealth, 30000)" in utils
    web = js_code_only(repo_src("frontend/tabs/website/website.js"))
    assert web.count("startVisiblePolling(") == 2 and "sectionId: 'tab_website'" in web
    assert "document.hidden" not in web and "visibilityState" not in web   # 不各自再寫一份判定


@pytest.mark.parametrize("rel", ["frontend/app.js", "frontend/js/app/remote-dispatch.js"])
def test_no_dead_appendlog_guards(rel):
    """utils.js 開機就 import，window.appendLog 一定在——`typeof appendLog === 'function'` 守衛只會把訊息吞掉。"""
    assert "typeof appendLog" not in js_code_only(repo_src(rel))


def test_dispatch_state_is_one_ctx():
    rd = js_code_only(repo_src("frontend/js/app/remote-dispatch.js"))
    assert "window._dispatchCtx = _dispatch" in rd and "_dispatchDestRoot" not in rd
    assert "const proxyRoot = _dispatch.proxyRoot;" in js_func_body(rd, "async function mergeHostOutputs(")
    assert "getElementById('tc_dest')" not in rd               # 合併不再事後讀分頁欄位
