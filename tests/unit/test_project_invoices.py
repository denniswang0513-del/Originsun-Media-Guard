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
    """🔴 方向（payment_type）**永遠**由後端在入口定案。前端自己決定過一次，
    結果漏掉「待撥款」那條路，代開發票被當成收款跑進應收帳款。

    2026-09-10 開放選發票類別之後多了一個窄例外：**代開**要送 payment_status。
    不送的話會吃到 schema 預設的「未收款」＝收款方向，那張過路錢的票就跑進
    應收帳款 —— 正好是上面那個坑。所以送的值必須是**付款方向**的狀態，
    這裡拿後端那支純函式當尺，兩邊不會各寫一套。
    """
    js = _js()
    i = js.index("_fetch('/invoices'")
    seg = js[i:i + 1200]
    assert "isPassthroughCategory(" in seg, "只有代開才明著送 —— 收款那條仍然交給後端"
    assert "payment_type: INV_DIR_PAYOUT" in seg and "payment_status: INV_UNPAID" in seg,         "代開要把方向與狀態都送出去，而且用共用常數不要寫死中文"
    # 🔴 收款那條**不可以**送方向（那是後端在入口定案的事）
    recv = seg.split("isPassthroughCategory(")[0]
    assert "payment_type" not in recv, "收款那條又自己送方向了"

    from core.finance_logic import invoice_direction
    from core.schemas import InvoicePayload
    from tests.unit._srcscan import repo_src as _src
    utils = _src("frontend/tabs/crm/crm-utils.js")
    unpaid = utils.split("export const INV_UNPAID = '")[1].split("'")[0]
    payout = utils.split("export const INV_DIR_PAYOUT = '")[1].split("'")[0]
    assert payout == "付款", payout

    # 🔴 為什麼非送 payment_type 不可：schema 的預設是非空的「收款」，
    #    所以 create_invoice 的 `if not payment_type` 永遠不成立 ——
    #    後端那句「沒送就依狀態推方向」對這條路根本走不到。
    #    哪天預設改成空字串，這條會紅，那時就可以把前端那個 payment_type 拿掉。
    assert InvoicePayload.model_fields["payment_type"].default == "收款",         "schema 預設變了 —— 重新確認前端還需不需要明著送 payment_type"
    assert invoice_direction(unpaid) == "付款",         f"前端送的 {unpaid!r} 在後端被判成「{invoice_direction(unpaid)}」—— 代開會跑進應收帳款"


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


def test_the_project_stays_pinned_but_the_category_is_chosen():
    """**專案**永遠是這一頁的專案，不讓人再選一次 —— 選錯了沒人會知道，
    而那正是本來 65% 的發票沒掛專案的原因。

    **類別**則是 owner 2026-09-10 要求可以改（本來寫死「專案」，從專案頁開不出
    代開發票）。字彙走共用常數，不准在這裡再寫一份中文清單 —— 兩份遲早分岔，
    而分岔的症狀是「某個入口開出來的票，篩選器篩不到」。
    """
    js = _js()
    i = js.index("_fetch('/invoices'")
    seg = js[i:i + 1200]
    assert "project_id: _cur.id" in seg, "專案不可以讓人再選一次"
    assert "category: val('cat')" in seg
    assert "category: '專案'" not in seg, "類別已經改成可選，這行是舊的"

    from tests.unit._srcscan import js_code_only, repo_src as _src
    page = js_code_only(_src(JS))
    assert "INV_CATEGORIES" in page and "'內部代開'" not in page,         "類別字彙要從 crm-utils 拿，不要在這一頁再寫一份"


def test_the_category_vocabulary_has_one_copy():
    """`INV_CATEGORIES` 是前端唯一那份，而且要跟後端那組代開字對得上。"""
    from core.finance_logic import INVOICE_PASSTHROUGH_CATEGORIES
    from tests.unit._srcscan import js_code_only, repo_src as _src
    utils = js_code_only(_src("frontend/tabs/crm/crm-utils.js"))
    line = utils.split("export const INV_CATEGORIES = [")[1].split("]")[0]
    cats = [x.strip().strip("'\"") for x in line.split(",") if x.strip()]
    assert cats[0] == "專案", "第一個是預設，要是「專案」"
    assert tuple(cats[1:]) == tuple(INVOICE_PASSTHROUGH_CATEGORIES),         f"前端 {cats[1:]} 跟後端 {list(INVOICE_PASSTHROUGH_CATEGORIES)} 對不上"


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
    assert "loadInvoicesTab(projectId, 'proj-pay-invoices', { proj: d.proj, invoices: d.inv })" in pay, "發票要嵌進收付款（吃預載，不重抓兩支）"
    assert 'id="proj-pay-invoices"' in repo_src("frontend/tabs/crm/crm-projects-pay.js")
    inv = js_code_only(repo_src(JS))
    assert "export async function loadInvoicesTab(projectId, hostId, preloaded = null)" in inv and "document.getElementById(_hostId)" in inv


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


def test_everything_but_the_invoice_number_is_required():
    """owner 2026-09-10：「除了發票號碼外都是必填」。

    發票號碼常常是事後才拿到的（`issue_status_for` 就是靠它判「未開立」），
    所以只有它可以留空。其餘八格漏一格，那張票就是一筆之後要有人回頭補的爛帳，
    而補的人通常不是開的人。

    🔴 錯誤訊息要**指名哪一格**並把游標放過去：只說「請填必填欄位」的話，
       欄位有八個，使用者得自己一格一格找。
    """
    from tests.unit._srcscan import js_code_only, repo_src as _src
    save = js_code_only(_src(JS)).split("_P.save = async")[1].split("btn.disabled = true")[0]
    for field, label in (("date", "開立日期"), ("title", "品名"), ("applicant", "申請人"),
                         ("item", "品項"), ("cat", "發票類別"),
                         ("company", "抬頭"), ("taxid", "統一編號")):
        assert f"missing('{field}', '{label}')" in save, f"{label} 沒有擋"
    assert "amounts.amount_total" in save, "金額沒有擋"
    assert "'number'" not in save, "發票號碼不可以是必填 —— 常常是事後才拿到"
    assert "focus(id)" in save, "擋下來要把游標放到那一格"
    # 🔴 只准在**那一格自己的** .ss-wrap 裡找可見輸入框。用 parentNode.querySelector
    #    的話 parent 是整張表單的 grid，會抓到別一格的 ss-input ——
    #    訊息說「品項要填」、游標卻跳到申請人（2026-09-10 真的瀏覽器驗證抓到）。
    assert "node.dataset.searchable" in save and "closest('.ss-wrap')" in save,         "focus 沒有收斂到那一格自己的 wrapper"
    assert "parentNode?.querySelector" not in save, "那個寫法會在整張表單裡亂抓"
    assert "'.ss-input'" in save, (
        "select-upgrade 會把選項 ≥4 的 select 藏起來換成打字下拉 —— "
        "對藏起來的元素 focus() 沒有作用，訊息說了哪一格但游標不會過去")

    page = _src(JS)
    assert "除了發票號碼，其他都要填。" in page, "畫面上要先講，不要等按了才說"


def test_passthrough_says_what_it_will_do():
    """選了代開就把後果寫在畫面上 —— 方向會從收款變成付款，那張票不算這個案的
    應收、也不佔「還能開」。靜默改方向的話，使用者只會發現「數字怪怪的」。"""
    from tests.unit._srcscan import js_code_only, repo_src as _src
    page = js_code_only(_src(JS))
    assert "proj-inv-cat-note" in page and "catNote" in page
    src = _src(JS)
    assert "代開是過路錢" in src and "不算這個案的應收" in src
