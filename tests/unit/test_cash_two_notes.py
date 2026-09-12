# -*- coding: utf-8 -*-
"""收支雙備註（owner 2026-08-26「兩個備註欄：銀行帳戶本來的資訊／我的附註」
＋「列表直接點按就編輯、自動儲存」）。

行為釘：bank_memo=機器來源、note=人寫；卡單匯入不再拿標記汙染附註欄；
行內編輯走 PUT 部分更新（只送那一欄）且不觸發整列點擊。
"""
from pathlib import Path
from tests.unit._srcscan import crm_css_src, js_func_body


from scripts.split_cash_notes import split_note
from tests.unit._srcscan import finance_src

ROOT = Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_split_note_classifies_by_origin():
    # 銀行側特徵 → bank_memo
    assert split_note("本筆分期尚有未到期金額NT$ 8,656 [sheet-import]") == \
        ("本筆分期尚有未到期金額NT$ 8,656", "")
    assert split_note("國外交易服務費－161.00 [sheet-import]")[0].startswith("國外交易服務費")
    assert split_note("全支付－全聯福利中心TAIPEI [sheet-import]")[1] == ""
    assert split_note("中國信託 [sheet-import]")[1] == ""       # 跨轉對方行
    assert split_note("網路自轉 [sheet-import]")[1] == ""
    # 人寫的 → note 保留
    assert split_note("256G 記憶卡 [sheet-import]") == ("", "256G 記憶卡")
    assert split_note("昱涵牙刷牙膏等備品 [卡單匯入]") == ("", "昱涵牙刷牙膏等備品")
    # 純標記 → 兩欄都清空；沒標記的列不動
    assert split_note("[sheet-import]") == ("", "")
    assert split_note("客戶說月底匯") == (None, None)
    assert split_note("") == (None, None)


def test_backend_carries_bank_memo():
    src = finance_src()
    assert '"bank_memo"' in src.split("def _to_cash_dict")[1][:1600]
    assert "CrmCashEntry.bank_memo.ilike(ql)" in src, "搜尋要涵蓋銀行資訊欄"
    from tests.unit._srcscan import models_src
    models = models_src()
    assert "bank_memo = Column(Text" in models


def test_card_import_stops_polluting_note():
    """卡單匯入的來源由 status='card' 說明 —— 不再把「[卡單匯入]」寫進附註欄
    （那正是 owner 截圖裡整欄的雜訊）。"""
    src = _read("routers/api_finance_card.py")
    assert 'note="[卡單匯入]"' not in src


def test_cashbook_has_two_note_columns_with_inline_edit():
    html = _read("frontend/tabs/crm/crm-cashbook.html")
    assert 'data-sort-key="bank_memo"' in html and 'data-sort-key="note"' in html
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    fn = js.split("window._cashInline = (ev")[1]
    assert "stopPropagation()" in fn, "格子點擊不可觸發整列選取"
    assert "JSON.stringify({ [f]: v })" in fn, "自動儲存只送那一欄（部分更新）"


def test_cells_are_click_to_edit_and_detail_comes_from_kebab():
    """owner 2026-08-27 更正：「這裡是要點擊就可以調整，但是跳出右側詳情頁，
    要點最右邊的編輯」—— 格子單擊即編輯；整列**不再**開右側詳情，詳情走最右邊
    ⋮ 的「編輯」。（先前把兩者理解反了，做成 ✎ 才能編輯 —— 已退回。）"""
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    # 列的 HTML 2026-08-28 起抽成 _rowHtml（renderList 與 _patchRow 共用）
    row = js.split("function _rowHtml(e) {")[1].split("return `")[1][:2400]
    import re
    # 格子帶 cash-c-<key>（欄位選擇器用）—— 釘「cash-ed 且掛 _cash* onclick」，不釘 class 的拼法
    assert len(re.findall(r'class="cash-ed[^"]*" onclick="window\._cash', row)) >= 5, "五個格子都要單擊即編輯"
    assert "_PEN(" not in row, "✎ 那一版已退回"
    assert 'class="crm-row${e.id === _selectedId' in row
    assert "onclick=\"window._cashSelect" not in row, "整列不可再開右側詳情"
    assert "onEdit: '_cashSelect'" in js, "詳情改從最右邊 ⋮ 的『編輯』開"


def test_taxonomy_cell_has_searchable_dropdown_and_custom_entry():
    """子項目要有下拉（owner 2026-08-27「子項目跳不出下拉清單」），且留得下
    新值 —— 純選單擋掉新詞、純文字打錯字長雙胞胎。

    2026-08-27 起三個分類格共用 `_cashTaxEdit`（順著樹走），「＋ 自訂…」不再是
    寫一個字串進 sub_item，而是**在樹上長一個節點**（值域與寫入是同一份）。
    """
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    fn = js.split("window._cashTaxEdit = (ev")[1].split("/** 刷卡金額")[0]
    # 2026-08-28 起三處（篩選／格內編輯／批次分類）的下拉都由 `_taxSelects` 生成 ——
    # 可搜尋與「＋ 自訂…」那兩件事跟著搬進去了，斷言跟著走
    assert "_taxSelects(" in fn and "custom: true" in fn, "要留自訂入口"
    # 2026-08-30 起下拉的產生器抽到 js/shared/cash-tax-picker（對帳單匯入預覽
    # 也用同一套 —— owner「比照私帳收支表的模式」）：可搜尋的升級留在這個 tab
    # （各 tab 的實作不同），「＋ 自訂…」在共用版裡。
    wrap = js_func_body(js, "function _taxSelects(")
    assert "searchableSelect(" in wrap and "tree: _taxTree" in wrap
    shared = _read("frontend/js/shared/cash-tax-picker.js")
    assert "＋ 自訂" in shared
    assert "o.searchable(sel, i)" in shared, "可搜尋的鉤子要留給呼叫端"
    assert "'/cash-taxonomy/nodes'" in fn, "自訂＝在樹上長節點，不是寫字串"


def test_cashbook_filters_date_sub_amount():
    """篩選列（owner 2026-08-26「可以篩選日期區間、分類、子項目、金額」）：
    日期含當日（迄日 +1 天開區間）、子項目精確、金額比**量級**
    （收入或 支出＋匯費 取大者 —— 比單邊會讓收款列全篩不到）。"""
    src = finance_src()
    fn = src.split("async def list_cash_entries(")[1].split("\n@router")[0]
    for frag in ("date_from", "timedelta(days=1)", "sub_item == sub_item", "_fn.greatest"):
        assert frag in fn, frag
    html = _read("frontend/tabs/crm/crm-cashbook.html")
    # 分類與子項目收斂成一排會長的下拉（cash-filter-tax），深度不定所以不寫死幾顆
    for i in ("cash-filter-from", "cash-filter-to", "cash-filter-tax",
              "cash-filter-amin", "cash-filter-amax"):
        assert i in html, i
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    for k in ("date_from", "date_to", "node_id", "amount_min", "amount_max"):
        assert f"params.set('{k}'" in js, k


def test_category_cell_edits_with_cascading_selects():
    """分類格點按填寫＝格內下拉，**點哪欄編哪欄**（點項目不該先被要求重選類別）。
    自由文字會打錯字長出新類別，選單保證值域。

    2026-08-27 起走分類樹：選到還有下層的節點就再長一格，所以第四、五層不必改
    程式就問得出來。存的是**節點 id**，category/item/sub_item 由後端從路徑推導
    —— 前端自己組複合鍵就是第二份規則。詳細行為釘在 test_cash_tree.py。
    """
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    fn = js.split("window._cashTaxEdit = (ev")[1].split("/** 刷卡金額")[0]
    assert "JSON.stringify({ taxonomy_node_id:" in fn
    assert "category" not in fn.split("const save =")[1].split("};")[0],         "存檔時不准前端自己算 category —— 那是後端從路徑推導的鏡射"
    # 「還有下層就再長一格」＝深度不寫死的關鍵那一行
    assert "last.children.length" in fn
    # 三格各帶自己的起始層
    for lv in (0, 1, 2):
        assert f"_cashTaxEdit(event,'${{e.id}}',{lv})" in js, lv


def test_all_dropdowns_searchable_everywhere():
    """「所有下拉選單都要可以搜尋」：門檻 4（案源這種 4 選項的也要）；
    /my-ledger.html 是獨立頁沒載 app.js —— 要自己開 initSelectAutoUpgrade
    （owner 截圖裡客戶下拉還是原生的就是這個漏）。"""
    up = _read("frontend/js/shared/select-upgrade.js")
    assert "MIN_OPTIONS = 4" in up
    ml = _read("frontend/my-ledger.html")
    assert "select-upgrade.js" in ml and "initSelectAutoUpgrade" in ml


def test_cashbook_marks_weekends_and_holidays():
    """六日／假日標記（owner 2026-08-27「假日的功能也做在收支」）。

    判斷一筆屬公屬私時，那天是不是假日是關鍵線索 —— 日期字串本身看不出來。
    星期用算的（一定準）、假日走 shared/tw-calendar.js 的清單。
    """
    cal = _read("frontend/js/shared/tw-calendar.js")
    # 🔴 只給 'YYYY-MM-DD' 時 JS 當 UTC 午夜解析；用本地 getDay() 讀會差一天
    assert "getUTCDay()" in cal, "星期要用 UTC 讀，否則時區會讓日期整個位移"
    for day in ("2026-02-17", "2026-04-05", "2025-10-25", "2024-02-09"):
        assert day in cal, f"假日清單少了 {day}"
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    assert "tw-calendar.js" in js and "_dayMark" in js
    assert "\nfunction _dayHtml" in js and "\nfunction _dayCls" in js
    # 六日／假日的**篩選鈕**已拿掉（owner 2026-09-04），日期格的假日標記還在
    html = _read("frontend/tabs/crm/crm-cashbook.html")
    assert 'id="cash-filter-off"' not in html and "_offOnly" not in js
    css = crm_css_src()
    assert ".cash-wd" in css and "is-holiday" in css and "is-weekend" in css


def test_cashbook_renders_in_chunks_not_all_rows():
    """🔴 4,733 列全畫＝105,912 個 DOM 元素、每次重畫 ~680ms（2026-08-28 實測
    0.14ms/列）。篩選、排序、改一格分類都會重畫，於是每個動作都要等一秒以上。

    改成一次畫 `_PAGE` 列、捲到底再續（哨兵 + IntersectionObserver，root 取真正
    在捲的祖先 —— 列表在面板裡捲，用 viewport 會永遠不觸發）。
    """
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    assert "const _PAGE" in js and "_drawMore" in js
    assert "IntersectionObserver" in js
    # root 要**量**出來：列表容器通常自己就是捲動的那個（crm.css 給
    # [id$="-list-body"] overflow-y:auto），但那要它真的被限高。寫死成 viewport
    # 的話列表在面板裡捲時永遠不觸發；寫死成容器的話沒限高時會一次畫太多批。
    assert "_scrollRoot(body)" in js and "scrollHeight > body.clientHeight" in js
    # renderList 只畫第一批，不再一次 map 整個陣列
    fn = js_func_body(js, "function renderList()")
    assert "_drawMore(body)" in fn
    assert ".map(" not in fn, "整表 map 就是那 680ms"


def test_inline_edits_patch_one_row_not_the_whole_table():
    """改一格分類／附註之後只換那一列 —— 整表重畫要 ~680ms，換一列是 0。
    列上要有 data-id 才換得掉。"""
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    assert 'data-id="${e.id}"' in js
    assert "function _patchRow(id)" in js
    for fname in ("window._cashInline = (ev", "window._cashTaxEdit = (ev"):
        fn = js.split(fname)[1].split("\n};")[0]
        assert "_patchRow(" in fn, fname
        assert "renderList()" not in fn, f"{fname} 不該整表重畫"


# ── 搬帳本（專案管理 ↔ 私帳，owner 2026-08-28）─────────────────────
_PROJ = open("routers/crm/projects.py", encoding="utf-8").read()


def test_ledger_move_is_a_dedicated_endpoint_not_a_field():
    """🔴 換帳本是全 repo「更新一律不得換帳本」的唯一例外 —— 走專屬端點，
    不是放行 PUT /projects/{id} 的 entity 欄。那條路一開，任何一次普通更新
    都可能把案子的錢流歸屬帶走。"""
    assert "async def move_project_ledger" in _PROJ
    upd = _PROJ[_PROJ.index("async def update_project"):]
    upd = upd[:upd.index("await session.commit()")]
    assert "entity" not in upd or "p.entity =" not in upd, "一般更新不可寫 entity"


def test_ledger_move_requires_mine_scope_both_directions():
    """只有私帳 full scope 能按 —— 兩個方向都是（搬回來也是動私帳的錢）。"""
    for fn in ("async def move_project_ledger", "async def check_project_ledger_move"):
        body = _PROJ[_PROJ.index(fn):]
        body = body[:body.index("return {")]
        assert 'require_entity(request, "mine", level="full")' in body, fn


def test_ledger_move_blocks_only_the_entity_bearing_tables():
    """擋的是**自己帶 entity 的那三張** —— 搬過去它們會留在原本那本帳，
    `_assert_project_same_entity` 當場破功、母公司三表也少掉那幾筆。

    🔴 **專案帳目（雜支／成本行）刻意不擋**（owner 2026-08-28「這個還是要可以
    推啊，我已經做相對應的空間了」）：那兩張沒有 entity 欄、本來就跟著專案走，
    而私帳逐案損益已經有「行政雜支／人員費用」兩欄收納它們。早一版把雜支也擋
    進來，結果是「有帳目的案子永遠推不動」，正好擋掉他要推的那些。
    """
    from routers.crm.projects import _LEDGER_BLOCKERS

    names = {M.__tablename__ for M, _label in _LEDGER_BLOCKERS}
    assert names == {"crm_cash_entries", "crm_invoices", "crm_payment_requests"}


def test_moving_to_mine_keeps_it_visible_in_the_pipeline():
    """搬到私帳要順手設 crm_pushed=1 —— 否則專案管理的預設檢視（entity=parent）
    當場看不到它，使用者會以為案子不見了。"""
    body = _PROJ[_PROJ.index("async def move_project_ledger"):]
    assert 'pushed = 1 if target == "mine" else 0' in body
    assert "p.crm_pushed = pushed" in body


def test_move_button_only_for_accounts_with_finance_mine():
    """按鈕只給帳號上真的有 finance_mine 的人 —— 直接看 _modules、不走 Lv3
    bypass（同 finance.js 私帳子視圖的口徑）。後端 require_entity 才是牆。"""
    js = _read("frontend/tabs/crm/crm-projects-detail.js")
    seg = js.split("const actions = document.getElementById('proj-bar-actions')")[1]
    seg = seg.split("crm-detail-close")[0]
    assert "(window._modules || []).includes('finance_mine')" in seg
    # 2026-09-12 起：母帳案的換帳本從「推送到私帳」彈窗分流（proj-push-mine），
    # 動作列上獨立的「搬回公司帳」只給已經在私帳的案
    assert "proj-move-ledger" in seg and "proj-push-mine" in seg


def test_move_asks_before_it_moves():
    """換帳本前一定 confirm，而且先問後端能不能搬（不要讓人按了才吃 409）。"""
    js = _read("frontend/tabs/crm/crm-projects-core.js")
    fn = js.split("window._projMoveLedger = async function")[1]
    assert "ledger-move-check" in fn and "window.confirm(" in fn
    assert fn.index("ledger-move-check") < fn.index("window.confirm(")
