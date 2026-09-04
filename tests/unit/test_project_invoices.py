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
import re

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
    # 2026-09-03 搬到零依賴的 js/shared/invoice-amounts.js（手機 CRM 頁也用）；
    # crm-utils 只 re-export，桌機兩個入口的 import 不變
    leaf = js_code_only(repo_src("frontend/js/shared/invoice-amounts.js"))
    assert re.search(r"export function invoiceAmounts\(", leaf), "共用層沒有這支"
    assert "vatPct = 5" in leaf, "稅率預設值要住在共用層的簽章上"
    assert "import" not in leaf, "invoice-amounts.js 必須是葉節點（手機頁 import 不起 crm-utils）"
    util = js_code_only(repo_src(UTIL))
    assert "export { invoiceAmounts } from '../../js/shared/invoice-amounts.js';" in util
    assert "TAX_RATE" not in util and "1.05" not in util, "crm-utils 又留了一份稅率"
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

def test_this_page_stays_inside_its_boundary():
    """這一頁能做什麼、不能做什麼（owner 2026-08-24 調整過界線）。

    可以：開票（POST）、刪票（DELETE，打發票本同一支端點）、補申請人／品項
          （PUT，純標註欄位，且必須是讀整張再整包送回）。
    不可以：作廢、改金額／抬頭／統編／方向、上傳 PDF、配收款 —— 那些留在發票本。
            兩邊都能改同一張票的那些面向 ＝ 兩套規則遲早分岔。

    🔴 這條本來是「一律不准出現 PUT/DELETE」。owner 要求「這裡要可以填申請人跟
       品項」「這裡刪除發票，發票開立那邊也要可以刪除」之後，界線變成**按面向**
       而不是按動詞 —— 所以這裡改成逐項釘死，而不是放寬成什麼都不管。
    """
    js = _js()
    assert "method: 'POST'" in js, "開票沒了"
    assert "'PATCH'" not in js, "專案頁出現了 PATCH —— 沒有任何面向需要它"
    # 刪除只能是那一支共用端點
    assert js.count("'DELETE'") == 1, "DELETE 不只一處"
    assert "_fetch('/invoices/' + id, { method: 'DELETE' })" in js,         "刪除沒走發票本同一支端點（自己實作會留下孤兒的收款分配）"
    # PUT 只能碰那兩個標註欄位，而且只能出現在 setMeta 裡
    assert js.count("method: 'PUT'") == 1, "PUT 不只一處 —— 界線正在鬆掉"
    meta = js[js.index("_P.setMeta"):js.index("_P.del")]
    assert "method: 'PUT'" in meta, "PUT 跑到 setMeta 以外的地方了"
    for forbidden in ("issue_status", "payment_status", "payment_type",
                      "amount_total", "company_name", "tax_id", "file_url"):
        # 裸鍵（issue_status: 'x'）與字串鍵都要擋 —— 只檢查加引號的形式的話，
        # 物件字面量那種寫法會整個溜過去（實測）。
        assert forbidden not in meta,             f"setMeta 碰了 {forbidden} —— 那個面向留在發票本"


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
    # 推導收在 _remaining()（摘要列與開票視窗共用，兩邊各推一次就會漂）
    i = js.index("function _remaining(")
    seg = js[i:i + 300]
    assert "contract ? contract - _sum(_receipts(), 'amount_total') : null" in seg, \
        "沒填合約時沒有回 null —— 拿 0 當上限會讓每個案子都顯示「還能開 -500,000」"
    assert "合約金額未填" in repo_src(JS), "沒填時沒有講出來"


def test_the_amount_owed_uses_the_backends_settled_flag():
    """🔴 尚欠寫成 `issued - got` 就繞過了 NT$50 匯費容差。

    394 張歷史發票裡有 42 張是被匯費短收 30 元、靠容差才算收齊的。自己相減的話
    那 42 張會在專案頁顯示琥珀色「尚欠 $30」，而發票本與應收帳款都說收齊了 ——
    同一個畫面兩個答案，正是 collection_fields 把 `settled` 布林算好送過來要
    消滅的東西。容差是後端的事，不該複製到瀏覽器。
    """
    js = _js()
    i = js.index("function _summaryHtml(")
    seg = js[i:i + 1600]
    assert "!i.settled" in seg, "尚欠沒有用後端算好的 settled"
    assert "'outstanding'" in seg, "尚欠沒有用後端算好的 outstanding"
    assert "issued - got" not in seg, "尚欠又自己相減了（會繞過匯費容差）"


def test_voided_and_payable_invoices_stay_out_of_the_summary():
    """作廢的、代開的（付款方向）不能算進這個案子的收入。"""
    js = _js()
    i = js.index("const _receipts =")
    seg = js[i:i + 300]
    assert "'收款'" in seg and "作廢" in seg


# ── 分頁三處要同步 ────────────────────────────────────────────

def test_the_tab_is_wired_in_all_three_places():
    """發票 2026-09-04 起併進「收付款」分頁（owner：人員配置改成收付款、與發票整合）：
    按鈕只剩 team＝收付款、內容嵌在 #proj-pay-invoices、切換由 loadPayTab 帶。缺任一個，分頁就是點了沒反應（而且不報錯）。"""
    html = repo_src(HTML)
    assert 'data-tab="invoices"' not in html, "發票分頁按鈕已併進收付款，不該再有"
    assert 'data-tab="team"' in html and ">收付款<" in html, "收付款分頁按鈕"
    main = js_code_only(repo_src(MAIN))
    assert "loadPayTab(state.selectedId)" in main and "callbacks.loadPayTab = loadPayTab" in main
    pay = js_code_only(repo_src("frontend/tabs/crm/crm-projects-pay.js"))
    assert "loadInvoicesTab(projectId, 'proj-pay-invoices')" in pay, "發票要嵌進收付款"
    assert 'id="proj-pay-invoices"' in repo_src("frontend/tabs/crm/crm-projects-pay.js")
    inv = js_code_only(repo_src(JS))
    assert "export async function loadInvoicesTab(projectId, hostId)" in inv and "document.getElementById(_hostId)" in inv


def test_switching_project_reloads_an_open_invoice_tab():
    """🔴 lazy 分頁的老坑：直接切到另一個專案時，開著的分頁會殘留上一案的內容。
    _reloadActiveDetailTab 要認得這個分頁。"""
    main = js_code_only(repo_src(MAIN))
    i = main.index("function _reloadActiveDetailTab")
    seg = main[i:main.index("callbacks.renderDetail", i)]
    assert "tab === 'team'" in seg and "loadPayTab(projectId)" in seg, \
        "換專案時收付款分頁不會跟著換 —— 會看到上一個案子的發票與請款"


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
