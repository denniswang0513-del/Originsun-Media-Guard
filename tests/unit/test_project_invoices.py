# -*- coding: utf-8 -*-
"""專案詳情的「發票」分頁（owner 2026-08-23）。

owner：「專案如果成案，其實就會有一筆發票需要開立，為何不在專案裡面就有開發票的
入口？」資料同意他：收款發票 209 張只有 74 張掛專案（35%），275 個專案裡只有 30 個
看得到自己的發票 —— 89% 的案子在系統裡是「沒有收入」的。

發票表單早就有「關聯專案」欄位，缺的是**入口方向**。所以這一頁不是第二套發票，
是同一批發票的第二個視角。

🔴 兩個視角最容易出的錯，都在這裡釘住：
   ① 規則被複製（稅率、款項狀態）→ 兩邊算出不一樣的數字
   ② 兩邊都能改同一張票 → 兩套規則遲早分岔
"""
from tests.unit._srcscan import js_code_only, repo_src

JS = "frontend/tabs/crm/crm-projects-invoices.js"
INV = "frontend/tabs/crm/crm-invoices.js"
UTIL = "frontend/tabs/crm/crm-utils.js"
HTML = "frontend/tabs/crm/crm-projects.html"
MAIN = "frontend/tabs/crm/crm-projects.js"


def _js():
    return js_code_only(repo_src(JS))


# ── 規則只能有一份 ────────────────────────────────────────────

def test_the_tax_rule_lives_in_exactly_one_place():
    """🔴 稅率不是永恆的 5%。複製一份的話，兩個入口開出來的發票尾差會不一樣 ——
    而且是對帳時才會發現的那種差。"""
    util = js_code_only(repo_src(UTIL))
    assert "export function invoiceAmounts(" in util, "共用層沒有這支"
    assert "const TAX_RATE = 1.05;" in util
    inv = js_code_only(repo_src(INV))
    assert "const TAX_RATE" not in inv, "發票本又自己留了一份稅率"
    assert "1.05" not in inv, "發票本裡還有寫死的稅率"
    proj = _js()
    assert "1.05" not in proj, "專案頁自己寫死了稅率"
    assert "invoiceAmounts" in proj, "專案頁沒走共用的那支"


def test_the_status_rule_is_left_to_the_backend():
    """🔴 payment_status / payment_type 由後端在入口定案（create_invoice 的
    docstring 說得很清楚）。前端自己決定過一次，結果漏掉「待撥款」那條路，
    代開發票被當成收款跑進應收帳款。"""
    js = _js()
    i = js.index("_fetch('/invoices'")
    seg = js[i:i + 900]
    assert "payment_status" not in seg, "又自己送款項狀態了"
    assert "payment_type" not in seg, "又自己送發票方向了"


# ── 兩個入口的分工 ────────────────────────────────────────────

def test_this_page_only_creates_and_reads():
    """編輯／作廢／PDF／收款配對一律留在發票本 —— 兩邊都能改就會分岔。"""
    js = _js()
    assert "method: 'POST'" in js
    for verb in ("'PUT'", "'DELETE'", "'PATCH'"):
        assert verb not in js, f"專案頁出現了 {verb} —— 修改應該留在發票本"


def test_it_tells_people_where_to_go_for_editing():
    """只擋不講＝使用者找不到路。要明講去哪改。"""
    js = repo_src(JS)
    assert "帳務 → 發票" in js, "沒告訴人編輯要去哪一頁"


# ── 預填：這是它跟發票本真正的差別 ──────────────────────────

def test_the_invoice_title_comes_from_the_client_full_name_not_the_short_name():
    """🔴 專案回應只有 client_short_name（代稱）。抬頭要的是**全名**：
    代稱是「泛亞」，抬頭是「泛亞工程顧問股份有限公司」—— 開錯要作廢重開。"""
    js = _js()
    assert "_client?.full_name" in js, "抬頭沒用客戶全名"
    assert "client_short_name" not in js, "拿代稱當抬頭了"
    # 客戶主檔走 crm-utils 的共用快取（列表本來就帶 full_name / tax_id）——
    # 換掉逐次 GET /clients/{id} 之後，規則不變：抬頭只能來自客戶主檔
    assert "crmCacheFetch('clients'" in js, "沒有去拿客戶主檔（全名與統編只在那裡）"


def test_a_missing_client_leaves_the_fields_blank_instead_of_guessing():
    """客戶被刪或沒權限 → 欄位留空讓人自己填，不要猜。"""
    js = _js()
    # 🔴 客戶那一支要**自己**接住失敗，不能靠 loadInvoicesTab 外層那個 catch ——
    #    靠外層的話，客戶清單掛掉會讓整個發票分頁變成「載入失敗」，而其實只是
    #    抬頭帶不出來而已（欄位留空讓人自己填就好）。
    i = js.index("crmCacheFetch('clients'")
    seg = js[i:i + 120]
    assert ".catch(" in seg, "客戶撈取沒有自己的 catch —— 客戶清單掛掉會讓整個發票分頁打不開"


def test_project_and_category_are_pinned_not_chosen():
    """從專案開票，專案與類別就不該讓人再選一次 —— 選錯了沒人會知道，
    而那正是現在 65% 的發票沒掛專案的原因。"""
    js = _js()
    i = js.index("_fetch('/invoices'")
    seg = js[i:i + 900]
    assert "project_id: _cur.id" in seg
    assert "category: '專案'" in seg


# ── 摘要不能亂算 ──────────────────────────────────────────────

def test_it_does_not_pretend_a_missing_contract_amount_is_zero():
    """🔴 238 個專案裡只有 21 個填了 contract_amount。沒填就不顯示「還能開」，
    不要拿 0 當上限 —— 那會讓每個案子都顯示「還能開 -500,000」。"""
    js = _js()
    assert "const left = contract ? contract - issued : null;" in js
    assert "合約金額未填" in repo_src(JS), "沒填時沒有講出來"


def test_voided_and_payable_invoices_stay_out_of_the_summary():
    """作廢的、代開的（付款方向）不能算進這個案子的收入。"""
    js = _js()
    i = js.index("const _receipts =")
    seg = js[i:i + 300]
    assert "'收款'" in seg and "作廢" in seg


# ── 分頁三處要同步 ────────────────────────────────────────────

def test_the_tab_is_wired_in_all_three_places():
    """按鈕、容器、切換 handler 缺任一個，分頁就是點了沒反應（而且不報錯）。"""
    html = repo_src(HTML)
    assert 'data-tab="invoices"' in html, "沒有分頁按鈕"
    assert 'id="proj-detail-invoices"' in html, "沒有內容容器"
    main = js_code_only(repo_src(MAIN))
    assert "'proj-detail-invoices'" in main, "切換時沒有處理這個容器"
    assert "loadInvoicesTab" in main, "沒有接上載入函式"


def test_switching_project_reloads_an_open_invoice_tab():
    """🔴 lazy 分頁的老坑：直接切到另一個專案時，開著的分頁會殘留上一案的內容。
    _reloadActiveDetailTab 要認得這個分頁。"""
    main = js_code_only(repo_src(MAIN))
    i = main.index("function _reloadActiveDetailTab")
    seg = main[i:main.index("callbacks.renderDetail", i)]
    assert "invoices" in seg and "loadInvoicesTab" in seg, \
        "換專案時發票分頁不會跟著換 —— 會看到上一個案子的發票"


# ── 發票本這一側：綁了就要看得見（owner 2026-08-23）──────────

INV_HTML = "frontend/tabs/crm/crm-invoices.html"


def test_the_invoice_book_list_shows_which_project_a_row_belongs_to():
    """🔴 owner：「在專案表填的發票，在發票裡自動綁定好」。實測發現：綁**是**綁上了
    （project_id 存對），但發票本的清單 11 欄裡沒有一欄是專案 —— 從那一頁完全
    看不出來這張票屬於哪個案子。只驗 DB 的測試會漏掉這種「存對了但看不到」。

    ⚠ 這條是**結構檢查**，不是主要防線：源碼掃描抓不到「條件被改成永假」
    （實測把 `inv.project_name ?` 換成 `false ?`，字串還在，這條照樣過）。
    真正證明它畫得出來的是 tests/e2e/ui_project_invoice_create.py —— 那支
    真的開一張票再切到發票本看得到案名，而且它確實抓到過這個洞。
    """
    src = repo_src(INV)
    # 錨在**資料列**的模板上 —— 第一個 crm-row-name 出現在快速新增列裡（實測）
    i = src.index('class="crm-row-name" title="${_esc(inv.title)}')
    seg = src[i:i + 600]
    assert "\n                inv.project_name\n" in seg, "名稱欄的專案判斷被改掉了"
    assert "· ${_esc(inv.project_name)}</span>" in seg, "沒有把專案名畫出來"


def test_the_invoice_book_can_filter_by_project():
    """GET /invoices 早就收 project_id，工具列一直沒有那顆 —— 綁好了卻沒地方按
    「只看這個案子的票」。"""
    assert 'id="inv-filter-project"' in repo_src(INV_HTML), "工具列沒有專案篩選"
    js = js_code_only(repo_src(INV))
    assert "params.set('project_id', _filters.project_id)" in js, "篩選沒送出去"
    assert "_filters.project_id = e.target.value" in js, "下拉沒接上"


def test_the_project_filter_keeps_its_selection_across_reloads():
    """重畫時不保留選取，載入一次就跳回「全部專案」—— 使用者以為篩掉了其實沒有。"""
    js = js_code_only(repo_src(INV))
    i = js.index("function _populateProjectFilter(")
    seg = js[i:i + 400]
    # 改走共用的 projectOptionsHtml 之後，「保留選取」＝把目前的值當 selectedId 傳進去
    assert "projectOptionsHtml(" in seg, "沒走共用的專案下拉（又手抄了一份 option）"
    assert "sel.value" in seg, "沒有把目前選的值帶進去 —— 重畫一次就跳回全部專案"
