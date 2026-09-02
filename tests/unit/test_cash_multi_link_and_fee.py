# -*- coding: utf-8 -*-
"""收支明細的四個就地編輯（owner 2026-09-01 連續回報）：

1. 請款單挑選器要能**多選** —— 一筆匯出常常是一個人的好幾張單併著發。
2. 勾「扣代開費」要帶出建議金額，而且改得動。
3. 代墊清單要有搜尋框。
4. 未收案清單要看得到客戶。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from tests.unit._srcscan import (code_only, func_body, js_code_only,  # noqa: E402
                                 js_func_body, repo_src)

EDITOR = "frontend/js/shared/cash-split-editor.js"
PICKER = "frontend/js/shared/row-picker.js"
CASHBOOK = "frontend/tabs/crm/crm-cashbook.js"


# ── 1. 一筆匯出付多張請款單 ─────────────────────────────────

def test_the_picker_supports_multi_select():
    """殼要有多選模式：點是切換勾選、**不關窗**，按儲存才送出。
    單選（專案）走同一份狀態 —— 兩種模式各留一份「誰被勾到」的話，勾選樣式
    遲早跟實際送出的不一致。"""
    js = js_code_only(repo_src(PICKER))
    fn = js_func_body(js, "export function openRowPicker(o) {")
    assert "const picked = new Set(" in fn
    assert "if (o.multi) {" in fn and "return;" in fn      # 切換後不 close()
    assert "o.onPick([...picked]);" in fn                   # 儲存送陣列
    assert "o.onPick(o.multi ? [] : '');" in fn             # 取消連結兩種形狀
    assert "const on = picked.has(r.id);" in fn, "勾選樣式沒有跟著同一份狀態"


def test_the_list_returns_every_linked_payment_not_just_the_main_one():
    """🔴 清單只帶 `payment_request_id`（金額最大那張）的話，挑選視窗只勾得回
    一張 —— 存檔就把同一筆匯款上的其餘幾張連結洗掉。"""
    src = repo_src("routers/crm/cash.py")
    assert "async def load_payment_links_map(" in src
    assert '"payment_ids": list(payment_ids or []),' in src
    fn = code_only(func_body(src, "async def list_cash_entries("))
    assert "load_payment_links_map(session, entity=ent)" in fn
    assert "paylinks.get(r[0].id, ())" in fn


def test_allocation_fills_up_to_what_the_row_actually_paid():
    """逐張給它自己的金額、累計不超過本列實際流出（匯費算進去）。
    🔴 分配 0 後端會擋 —— 濾掉，但要講出來：勾了三張存完剩兩張而畫面上
    沒有任何跡象，是最難發現的一種。"""
    fn = js_code_only(js_func_body(repo_src(CASHBOOK),
                                   "window._cashPayPick = (ev, id) => _inlineLink({"))
    assert "let left = _payOut(e);" in fn
    assert "Math.min(p ? (p.amount || 0) : 0, left)" in fn
    assert "if (amt > 0) {" in fn and "over.push(" in fn
    assert "crmToast(" in fn, "被濾掉的那幾張沒有告訴使用者"
    # 主要單據跟後端一樣取金額最大的那張
    assert "sort((a, b) => (b.amount || 0) - (a.amount || 0))" in fn


def test_an_empty_array_is_not_reported_as_linked():
    """🔴 `[]` 在 JS 是 truthy —— 用 `pid ?` 判斷的話「取消全部連結」會
    報成「已連結」。"""
    fn = js_code_only(js_func_body(repo_src(CASHBOOK), "function _inlineLink({"))
    assert "Array.isArray(pid) ? pid.length > 0 : !!pid" in fn


# ── 2. 代開費的建議值 ───────────────────────────────────────

def test_the_agency_fee_has_a_default_that_stays_editable():
    """勾了要帶出建議值。未收額推得出來就用它（精確）；推不出來（案子已結清、
    或手動列）改用標準費率把淨額還原成毛額 —— 原本這條給 0，勾了等於沒生效。"""
    fn = js_code_only(js_func_body(repo_src(EDITOR), "const feeGuess = (r) => {"))
    assert "(p.receivable || 0) - net" in fn
    assert "Math.round(net * pct / (100 - pct))" in fn
    body = js_code_only(repo_src(EDITOR))
    assert "setFee(i, feeGuess(rows[i]), cb.checked)" in body
    # 帶出來之後照樣改得動（改費用那條入口還在）
    assert "inp.onchange = () => setFee(Number(inp.dataset.fee)," in body


def test_the_fee_rate_comes_from_the_backend_not_a_hardcoded_eight():
    """🔴 費率正本在 core.ledger_project.DEFAULT_FEE_PCT。前端寫死一個 8
    就是第二份，改費率時改不到。"""
    assert "Number(out.fee_pct)" in js_code_only(repo_src(EDITOR))
    fn = code_only(func_body(repo_src("routers/crm/cash_splits.py"),
                             "async def cash_splits_outstanding("))
    assert '"fee_pct": DEFAULT_FEE_PCT,' in fn


# ── 3 & 4. 兩個清單 ────────────────────────────────────────

def test_both_pick_lists_are_searchable_and_rebind_after_redraw():
    """兩個清單同一個形狀：搜尋只換清單那塊（整窗重畫會打掉打字焦點），
    所以重畫後要重掛勾選事件。已勾的列不受過濾影響。"""
    body = js_code_only(repo_src(EDITOR))
    for q, listId, bind in (("projQ", "csp-projlist", "bindProj"),
                            ("advQ", "csp-advlist", "bindAdv")):
        assert f"let {q} = ''" in body or f"{q} = " in body, q
        assert listId in body, listId
        assert f"{bind}();" in body, bind
    assert "advChecked(a.entry_id) || !advQ" in body, "代墊搜尋會把已勾的濾掉"
    assert "picked.has(p.id) || !projQ" in body


def test_the_outstanding_list_shows_and_searches_the_client():
    """owner 2026-09-01「專案 要看得到客戶」—— 同名的案每年一個，光看案名
    選不出是哪一家。看得到就要搜得到（只加顯示的話，打客戶名反而變成
    「找不到符合的未收案」）。"""
    body = js_code_only(repo_src(EDITOR))
    assert "${p.name || ''} ${p.client || ''}" in body, "客戶沒進搜尋字串"
    assert "esc(p.client)" in body, "畫面上看不到客戶"
    fn = code_only(func_body(repo_src("routers/crm/cash_splits.py"),
                             "async def cash_splits_outstanding("))
    assert '"client": cname or "",' in fn
    assert "outerjoin(Client, Client.id == CrmProject.client_id)" in fn, \
        "逐案回頭問客戶名＝80 趟查詢"


# ── 5. 專案頁要看得到拆項那份錢 ────────────────────────────

def test_the_project_page_lists_money_that_arrived_as_a_split():
    """🔴 owner 2026-09-02「單筆拆分帳的部分，無法連結到專案表單／要連結後
    專案要可以看到明細」。

    一筆收支被拆之後，父列的 `project_id` **讓位給拆項**（拆項才是那筆錢的
    內容正本）。所以只查 `CrmCashEntry.project_id` 的話，走拆項進來的錢在專案
    頁一筆都看不到 —— 而「已收」是算得進去的（`_project_deltas`）。生產實查
    「TEDMA / 12支直式廣告」：已收 80,000 ＝ 整列 30,000 ＋ 拆項 50,000，
    畫面卻只列得出 30,000，看起來像帳掉了一半。
    """
    src = repo_src("routers/api_finance_projects.py")
    assert "async def _project_split_entries(" in src
    helper = code_only(func_body(src, "async def _project_split_entries("))
    assert "CrmCashSplit.project_id == project_id" in helper
    assert "join(CrmCashEntry, CrmCashEntry.id == CrmCashSplit.entry_id)" in helper
    # 🔴 毛額＝amount＋fee，跟 _project_deltas 同一條規則 —— 只列淨額的話
    # 明細加起來會比「已收」少一個代開費
    assert "(amt + fee) if deposit else 0" in helper
    fn = code_only(func_body(src, "async def project_ledger_detail("))
    assert "_project_split_entries(session, project_id, ent)" in fn
    assert "+ split_rows," in fn, "拆項那份沒有併進 entries"


def test_the_project_entry_table_marks_splits_and_totals():
    """列出來還要看得出「這筆是拆項的一部分」，並且有合計 ——
    使用者要拿它跟「已收」對，逐列心算不是驗證。"""
    js = js_code_only(repo_src("frontend/tabs/finance/subviews/projects.js"))
    seg = js.split("掛在本案的收支")[1].split("應付／請款單")[0]
    assert "e.split ?" in seg, "沒有標出拆項"
    assert "reduce((n, e) => n + (e.deposit || 0), 0)" in seg, "沒有合計"


def test_the_cash_detail_panel_shows_the_split_breakdown():
    """🔴 owner 2026-09-02「已拆的明細要在這裡可以看到」。

    一列被拆之後，它的分類／專案／發票**整組讓位給拆項** —— 詳情面板上那幾欄
    因此全是空的，等於只剩「350,436 從源日進來」，看不出這筆錢是誰的。資料
    早就跟著清單回來了（`splits`），只是沒畫。
    """
    js = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    fn = js_func_body(js, "function renderDetail(e) {")
    assert "const _sp = e.splits || [];" in fn
    assert "拆項明細" in fn
    # 代開費要看得出「實匯 vs 專案結清毛額」—— 只印一個數字的話，
    # 對不上專案已收時沒有任何線索
    assert "(s.amount || 0) + (s.fee || 0)" in fn
    assert "代開費" in fn
    # 合計比的是 amount（＝帳目金額），代開費外加不進 Σ
    assert "n + (s.amount || 0)" in fn
    # 從這裡就能改，不必回列表找那顆按鈕
    assert "window._cashSplitOpen('${e.id}')" in fn
