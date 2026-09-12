# -*- coding: utf-8 -*-
"""士源帳本（私帳手機版 /m/ledger.html）—— docs/MY_LEDGER_MOBILE_PLAN.md。

這裡釘的是**前後端契約**（/options／/home 的鍵名）與規矩：只有 finance_mine 能進、
寫入一律走既有端點（收支必走 /cash-entries，已收是增量制）、office-api 要掛、
office-api 不因此長出排程。手機版 CRM 的教訓：兩個代理各寫一半時契約要用測試釘。
"""
import re
from datetime import date, datetime, timezone

from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src

from routers.api_ledger_mobile import (HOUSEHOLD_TOP, PERIODS, _in_period,
                                       _period_range, taxonomy_options)

_SRC = repo_src("routers/api_ledger_mobile.py")

#: 前端照這份鍵名寫（views/ledger-*.js）；改鍵名兩邊一起改
OPTIONS_KEYS = {"me", "taxonomy", "household_top", "sources", "cost_fields",
                "income_items", "projects", "accounts"}
HOME_KEYS = {"period", "range", "projects", "cash", "recent", "to_collect"}


# ── 純規則 ─────────────────────────────────────────────────────────

def test_period_range():
    assert _period_range("month", date(2026, 2, 10)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert _period_range("month", date(2026, 12, 31)) == (date(2026, 12, 1), date(2026, 12, 31))
    assert _period_range("year", date(2026, 9, 12)) == (date(2026, 1, 1), date(2026, 12, 31))
    assert _period_range("all") == (None, None)
    assert PERIODS == ("month", "year", "all")


def test_in_period_treats_missing_close_date_as_only_in_all():
    lo, hi = date(2026, 1, 1), date(2026, 12, 31)
    assert _in_period(date(2026, 5, 5), lo, hi)
    assert _in_period(datetime(2026, 5, 5, tzinfo=timezone.utc), lo, hi)
    assert not _in_period(date(2025, 12, 31), lo, hi)
    assert not _in_period(None, lo, hi), "沒結案日的案不算進本月／本年"
    assert _in_period(None, None, None), "all 才把它算進去"


def test_taxonomy_options_drop_top_level_and_join_the_path():
    flat = [{"id": "a", "name": "家用", "depth": 1, "path": ["家用"]},
            {"id": "b", "name": "變動支出", "depth": 2, "path": ["家用", "變動支出"]},
            {"id": "c", "name": "外食", "depth": 3, "path": ["家用", "變動支出", "外食"]},
            {"id": "d", "name": "專案", "depth": 2, "path": ["公司", "專案"]}]
    out = taxonomy_options(flat)
    assert [o["id"] for o in out] == ["b", "c", "d"], "頂層節點不進 picker（掛到頂層等於沒分類）"
    assert out[1] == {"id": "c", "label": "家用／變動支出／外食", "top": "家用", "depth": 3}
    assert out[2]["top"] == "公司"
    assert HOUSEHOLD_TOP == "家用"


# ── 契約與規矩（掃原始碼）────────────────────────────────────────────

def test_both_endpoints_require_the_private_ledger_in_full():
    for fn in ("async def ledger_mobile_options(", "async def ledger_mobile_home("):
        body = code_only(func_body(_SRC, fn))
        assert 'require_entity(request, "mine", level="full")' in body, fn


def test_options_and_home_return_the_pinned_keys():
    opt = code_only(func_body(_SRC, "async def ledger_mobile_options("))
    for k in OPTIONS_KEYS:
        assert f'"{k}":' in opt, k
    home = code_only(func_body(_SRC, "async def ledger_mobile_home("))
    for k in HOME_KEYS:
        assert f'"{k}":' in home, k
    # 案子那半沿用 /project-ledger 的數字，不另寫一套
    assert 'await project_ledger(request, entity="mine")' in home
    # 專案 picker 不帶金額
    assert "contract_amount" not in opt and "amount_received" not in opt
    # 帳戶帶 active／is_default：停用的歷史帳戶編輯時要認得（不然存個摘要就洗成不指定）、新增預選預設帳戶
    assert '"active":' in opt and '"is_default":' in opt


def test_mounted_on_master_and_office():
    assert "'api_ledger_mobile'" in repo_src("main.py")
    office = repo_src("main_office.py")
    for m in ('"api_finance"', '"api_finance_projects"', '"api_finance_assets"', '"api_ledger_mobile"'):
        assert m in office, m


def test_module_pulls_in_no_scheduler_or_notifier():
    """office-api 的模組圖鐵則（test_office_surface 也會抓，這裡在檔案層再釘一次）。"""
    code = code_only(_SRC)
    for bad in ("core.scheduler", "import notifier", "from notifier", "socket_mgr", "croniter"):
        assert bad not in code, bad


# ── 前端（/m/ledger.html）──────────────────────────────────────────

def test_mobile_ledger_page_uses_the_shared_shell_and_gates_on_finance_mine():
    js = js_code_only(repo_src("frontend/m/ledger.js"))
    assert "from './shell.js'" in js, "手機頁只准 import 同一個殼"
    assert "finance_mine" in js
    html = repo_src("frontend/m/ledger.html")
    assert "manifest-ledger.webmanifest" in html
    manifest = repo_src("frontend/m/manifest-ledger.webmanifest")
    assert "士源帳本" in manifest and "/m/ledger.html" in manifest


def test_mobile_ledger_writes_go_through_existing_endpoints_only():
    """收支必走 /cash-entries（已收增量制）；沒有「標已收」；專案改動只送有動的鍵。"""
    src = "\n".join(js_code_only(repo_src(f"frontend/m/views/ledger-{v}.js"))
                    for v in ("cash", "projects", "receivable", "household", "overview", "assets"))
    assert "/api/v1/crm/cash-entries" in src
    assert "amount_received" not in src, "手機上沒有直接寫已收的路"
    assert "/api/v1/finance/project-ledger" in src
    assert "/api/v1/finance/m/options" in js_code_only(repo_src("frontend/m/ledger.js"))
    # 沒有發票、沒有請款
    assert "/invoices" not in src and "/payments" not in src


def test_cash_form_edit_never_clears_what_this_page_cannot_draw():
    """改一筆只送有動的鍵，而「這台畫不出來」不算動：分類節點停用、專案不在清單、帳戶不在選單
    三個都要守 —— 漏一個就是存個摘要順手把那欄洗掉，而且沒有任何錯誤。新增那條路分類必填。"""
    from tests.unit._srcscan import js_func_body
    js = js_code_only(repo_src("frontend/m/views/ledger-cash.js"))
    sheet = js_func_body(js, "export function openEntrySheet(e, { mode = 'cash', onDone } = {}) {")
    for k in ("taxonomy_node_id", "project_id", "bank_account_id"):
        assert f"{k}:" in sheet, k
    assert "delete diff[k]" in sheet
    form = js_func_body(js, "export function entryFormHtml(pfx, { mode = 'cash', entry = null } = {}) {")
    assert "a.active !== false" in form and "a.is_default" in form, "帳戶選單同桌機：只列 active＋目前那個，新增預選預設"
    assert "if (!body.taxonomy_node_id)" in js
    # 字彙先到手再接 hashchange：字彙還在飛時 render 出來的表單是空選單，而且不會再重建
    main = js_code_only(repo_src("frontend/m/ledger.js"))
    assert main.index("/api/v1/finance/m/options") < main.index("addEventListener('hashchange'")


def test_mobile_ledger_has_six_tabs_with_assets_separate():
    html = repo_src("frontend/m/ledger.html")
    tabs = re.findall(r'data-tab="([a-z]+)"', html)
    assert tabs == ["cash", "projects", "receivable", "household", "overview", "assets"], tabs


# ── L2（2026-09-13）：推送到母帳／家用按月／資產成長線 ──────────────────

def test_l2_project_sheet_pushes_to_parent_through_the_existing_link_endpoints():
    """三選一沿用 project_links 那三條路；換帳本前一定 confirm（不讓人按了才吃 409）。"""
    js = js_code_only(repo_src("frontend/m/views/ledger-projects.js"))
    for ep in ("/parent-create", "/parent-link", "/move-ledger", "/ledger-move-check"):
        assert ep in js, ep
    assert js.index("ledger-move-check") < js.index("confirm(") < js.index("/move-ledger")
    assert "projects-mine-links" in js, "母帳候選與同名建議來自對應表那支端點"
    assert "parent_links" in js, "已連結時不再出現推送鈕"


def test_l2_household_is_viewed_by_month():
    js = js_code_only(repo_src("frontend/m/views/ledger-household.js"))
    assert "date_from" in js and "date_to" in js
    assert "toISOString" not in js, "日期一律本地 YYYY-MM-DD"
    assert "shiftMonth" in js and "monthRange" in js


def test_l2_assets_growth_line_is_plain_inline_svg():
    js = js_code_only(repo_src("frontend/m/views/ledger-assets.js"))
    assert "<svg" in js
    for lib in ("chart", "echarts", "d3"):
        assert lib not in js.lower().replace("lg-chart", "").replace("growthcharthtml", "").replace("mountchart", "").replace("chart_max", "").replace("as-chart", "").replace("_chartrows", ""), lib
    assert "快照不足兩筆" in js


# ── 特徵測試（/polish 安全網）：前端純函式真的用 node 跑一次 ─────────────────

def _node(script: str) -> str:
    """跑一段 JS。🔴 輸出只准 ASCII：Windows 上 node 對 pipe 吐的是主控台碼頁，中文會變亂碼。"""
    import subprocess
    from tests.unit._srcscan import _REPO
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=_REPO)
    assert r.returncode == 0, r.stderr
    return (r.stdout or "").strip()


def test_asset_buckets_leaf_in_node():
    """資產「現在估計」＝自動桶＋上次快照裡沒被系統桶取代的手填桶；桌機與手機共用同一份。"""
    js = js_code_only(repo_src("frontend/js/shared/asset-buckets.js")).replace("export ", "")
    out = _node(js + """
const [gone1, , , , , gone2] = [...SUPERSEDED];          // 被系統桶取代的兩個桶名（不在輸出裡印中文）
const auto = {bank: 100, stocks: 50};
const last = {buckets: {[gone1]: 999, bank: 1, prepaid: 7, [gone2]: 5}};
console.log(JSON.stringify([manualBuckets(auto, last), estimatedTotal(auto, last), estimatedTotal(auto, null), estimatedTotal({}, null), SUPERSEDED.size]));""")
    # 被取代的不帶；bank 已是自動桶不帶；prepaid 留手填
    assert out == '[{"prepaid":7},157,150,0,7]', out
    # 桌機 assets.js 也走這份，不再有自己的 SUPERSEDED 名單
    desk = js_code_only(repo_src("frontend/tabs/finance/subviews/assets.js"))
    assert "asset-buckets.js" in desk and "const SUPERSEDED" not in desk


def test_household_month_helpers_in_node():
    """月份切換與月頭月尾：跨年、閏年、月底不用 toISOString（台北早上 8 點前會變昨天）。"""
    js = js_code_only(repo_src("frontend/m/views/ledger-household.js"))
    from tests.unit._srcscan import js_func_body
    fns = (js_func_body(js, "export function shiftMonth(ym, n) {")
           + js_func_body(js, "export function monthRange(ym) {")).replace("export ", "")
    out = _node(fns + """
console.log(JSON.stringify([shiftMonth('2026-01', -1), shiftMonth('2026-12', 1), shiftMonth('2026-09', -13),
  monthRange('2024-02'), monthRange('2026-09'), monthRange('2026-12')]));""")
    assert out == ('["2025-12","2027-01","2025-08",'
                   '{"from":"2024-02-01","to":"2024-02-29"},'
                   '{"from":"2026-09-01","to":"2026-09-30"},'
                   '{"from":"2026-12-01","to":"2026-12-31"}]'), out
    assert "toISOString" not in js
