# -*- coding: utf-8 -*-
"""堡壘的手機版（士源帳本 /m/ledger.html#fortress）—— docs/FORTRESS_PLAN.md §4。

釘的是規矩，不是行號：#fortress 是隱藏路由（六顆分頁不動、tabbar 亮「總覽」）、資料走桌機那支
`/api/v1/finance/fortress?entity=mine`、只有預留清單能寫且寫完標髒總覽、日期 todayLocal()、
按鈕純文字沒有 emoji、刪除前 confirm。總覽頂卡點了 → #fortress。
"""
import re

from tests.unit._srcscan import between, js_code_only, js_func_body, repo_src

_LEDGER = repo_src("frontend/m/ledger.js")
_FORT = repo_src("frontend/m/views/ledger-fortress.js")
_OV = repo_src("frontend/m/views/ledger-overview.js")
_CSS = repo_src("frontend/m/m.css")
_FORT_CODE = js_code_only(_FORT)

API = "/api/v1/finance/fortress?entity=mine"
#: 表情符號區段（U+1F300–U+1FAFF）與三個常被拿來當圖示的勾叉（規矩：按鈕純文字）
_EMOJI = re.compile("[\U0001F300-\U0001FAFF]|[✓✔✗]")


# ── 路由 ──────────────────────────────────────────────────────────

def test_ledger_routes_fortress_as_hidden_route():
    """TABS 六顆不動；'fortress' 進 ROUTES，currentTab 認得它，VIEWS 對到 ledger-fortress.js。"""
    js = js_code_only(_LEDGER)
    tabs = between(js, "export const TABS = [", "]")
    assert "'fortress'" not in tabs, "堡壘不是第七顆分頁（六顆已滿，docs/FORTRESS_PLAN.md §4）"
    assert "fortress" in between(js, "HIDDEN_ROUTES = {", "}")
    assert "export const ROUTES = [...TABS, ...Object.keys(HIDDEN_ROUTES)]" in js
    cur = js_func_body(js, "function currentTab(")
    assert "ROUTES.includes(h)" in cur and "TABS.includes(h)" not in cur
    assert "fortress: fortressView" in between(js, "const VIEWS = {", "}")
    assert "import * as fortressView from './views/ledger-fortress.js'" in js


def test_ledger_labels_fortress_and_keeps_overview_lit():
    js = js_code_only(_LEDGER)
    assert "HIDDEN_ROUTES[tab]" in between(js, "const tabLabel =", "\n"), "頂欄名要落到「堡壘」"
    assert "fortress: 'overview'" in between(js, "HIDDEN_PARENT = {", "}")
    assert "HIDDEN_PARENT[tab] || tab" in js_func_body(js, "async function render(")


# ── 堡壘頁 ────────────────────────────────────────────────────────

def test_fortress_view_contract():
    assert "export async function render(host, { first })" in _FORT_CODE
    assert f"'{API}'" in _FORT_CODE, "資料走桌機那支，不另開端點"
    assert "shouldLoad('fortress', { first })" in _FORT_CODE, "60 秒快取同其他分頁"
    assert "from '../shell.js'" in _FORT_CODE and "from '../ui.js'" in _FORT_CODE
    imports = re.findall(r"from '([^']+)'", _FORT_CODE)
    assert all(i in ("../shell.js", "../ui.js") or i.startswith("./ledger-") for i in imports), imports


def test_fortress_dates_are_local_and_no_emoji():
    assert "todayLocal()" in _FORT_CODE
    assert "toISOString" not in _FORT_CODE, "日期一律本地 YYYY-MM-DD"
    for name, src in (("ledger-fortress.js", _FORT), ("ledger-overview.js", _OV)):
        hit = _EMOJI.search(src)
        assert not hit, f"{name} 有 emoji／勾叉：{hit.group()!r}（按鈕純文字）"


def test_fortress_writes_only_earmarks_and_mark_overview_stale():
    """能寫的只有預留清單：POST／PUT／DELETE 三條都走 earmarks，寫完整包重畫＋標髒總覽。"""
    assert "const EARMARK_API = '/api/v1/finance/fortress/earmarks'" in _FORT_CODE
    assert "method: 'POST'" in _FORT_CODE and "method: 'PUT'" in _FORT_CODE and "method: 'DELETE'" in _FORT_CODE
    # 寫入的三條路都收斂到 done()：整包重畫 → markStale('overview') → toast
    d = js_func_body(_FORT_CODE, "async function done(")
    assert "markStale('overview')" in d and "await apply(host, r)" in d and "toast(" in d
    for header in ("function openAddSheet(", "function openEditSheet("):
        body = js_func_body(_FORT_CODE, header)
        assert "await done(host, r" in body, f"{header} 寫完要走 done()"
        assert "withBusy(" in body, f"{header} 的按鈕要 withBusy"
    edit = js_func_body(_FORT_CODE, "function openEditSheet(")
    i = edit.index("method: 'DELETE'")
    assert "confirm(" in edit[max(0, i - 400):i], "刪除前要 confirm"
    assert "paid: !e.paid" in edit, "已付／未付一顆鈕切換"
    # 自動項（房貸／信用卡）不可改：只有 manual 的列開抽屜
    r = js_func_body(_FORT_CODE, "export async function render(")
    assert "=== 'manual'" in r


def test_fortress_page_sections_and_money_style():
    d = js_func_body(_FORT_CODE, "function draw(")
    for h in ("五層資金", "預留清單", "每月必要支出", "壓力測試", "要改請到桌機的堡壘分頁", "＋記一筆預留"):
        assert h in d, h
    t = js_func_body(_FORT_CODE, "function testHtml(")
    assert "<details" in t and "<details open" not in t, "壓力測試預設全收合"
    assert "個月" in js_func_body(_FORT_CODE, "const lineVal =")
    w = js_func_body(_FORT_CODE, "export function wan(")
    assert "/ 10000" in w and "toFixed(1)" in w and "萬" in w


# ── 總覽頂卡 ──────────────────────────────────────────────────────

def test_overview_fetches_fortress_in_parallel_and_survives_its_failure():
    js = js_code_only(_OV)
    assert "fortressCardHtml" in js and "FORTRESS_API" in js
    load = js_func_body(js, "async function load(")
    assert "Promise.all" in load and "mfetch(FORTRESS_API).catch(" in load, "堡壘掛了總覽照畫"
    assert "fortressCard(ft)" in load
    assert "location.hash = 'fortress'" in js_func_body(js, "export async function render(")


def test_fortress_css_block_uses_shell_tokens():
    block = _CSS[_CSS.index("/* ── 堡壘（士源帳本）── "):]
    for cls in (".ft-big", ".ft-mini", ".ft-layer", ".ft-test", ".ft-tap"):
        assert cls in block, cls
    assert "var(--ok)" in block and "var(--warn)" in block and "var(--bad)" in block and "var(--pri)" in block
    assert not re.search(r"#[0-9a-fA-F]{6}(?![0-9a-fA-F])", block.replace("#fff", "")), "顏色用殼的變數，不另配一組"
