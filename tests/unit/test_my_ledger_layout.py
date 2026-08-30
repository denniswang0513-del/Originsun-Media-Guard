# -*- coding: utf-8 -*-
"""/my-ledger.html 版面釘子 —— 固定視窗高的殼要能捲。

2026-08-24 實測：財務 tab 的 #finance-content 帶 inline `overflow:hidden`
（原意是防 flex 橫向撐破），在主系統沒事（整頁跟著 body 捲），但
/my-ledger.html 是 `height:calc(100vh-40px); overflow:hidden` 的殼 ——
縱向也被關掉且**外部樣式表覆寫不了 inline** → 資產儀表板圖表以下整段
看不到、哪一層都捲不動。修法是兩半，缺一不可，所以兩半都釘。
"""
import re
from pathlib import Path

from tests.unit._srcscan import js_func_body
from tests.unit._srcscan import finance_src

ROOT = Path(__file__).resolve().parent.parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_finance_content_blocks_horizontal_overflow_only():
    """inline 只准擋橫向 —— 寫 `overflow:hidden` 會連縱向一起關掉。"""
    html = _read("frontend/tabs/finance/finance.html")
    main_tag = next(ln for ln in html.splitlines() if 'id="finance-content"' in ln)
    assert "overflow-x:hidden" in main_tag, main_tag
    assert "overflow:hidden" not in main_tag, "inline overflow:hidden 會讓固定高的殼捲不動"


def test_my_ledger_makes_content_scrollable():
    """殼頁自己補縱向捲動（主系統靠 body 捲，本頁沒有 body 捲可用）。"""
    css = _read("frontend/my-ledger.html")
    assert "#app-view #finance-content" in css
    assert "overflow-y: auto" in css


def test_assets_fx_rate_not_string_divided():
    """匯率顯示不可拿 fmtNum() 的字串去做算術（字串 / 1000 → NaN，實測畫面出現）。"""
    js = _read("frontend/tabs/finance/subviews/assets.js")
    fx_line = next(ln for ln in js.splitlines() if "美元匯率" in ln)
    assert "fmtNum" not in fx_line, fx_line
    assert "toFixed" in fx_line, fx_line


def test_project_ledger_table_is_height_bounded():
    """逐案損益的表格要框在可視高度內（清單自己內捲）。

    2026-08-25 實測：不框高度時 #fpl-list-body 會長到 16,884px、內捲永不發生 ——
    (1) 表頭跟著整頁捲走；(2) 更嚴重的是詳情面板是清單的 flex 兄弟，捲到第 300
    列點開，詳情畫在整個表格頂端（往上一萬多 px）＝看不到。
    """
    js = _read("frontend/tabs/finance/subviews/projects.js")
    assert "_fitBody" in js
    fn = js_func_body(js, "function _fitBody()")
    # 量出來的、不是寫死 px：要拿捲動容器的可視底部減表格頂端
    assert "getBoundingClientRect" in fn
    assert "finance-content" in fn
    assert "addEventListener('resize', _fitBody)" in js, "視窗縮放要重算"


def test_cashbook_card_column_separates_card_from_bank_expense():
    """刷卡與銀行支出是兩欄，同一筆錢只能出現在其中一欄。

    刷卡當下不動銀行（月底繳款才是銀行支出）—— 兩者混在「支出」欄裡，
    看不出「這個月刷了多少、實際從帳戶出去多少」。
    """
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    for fn_name in ("_cardAmt", "_bankOut"):
        assert f"function {fn_name}(e)" in js
    card = js_func_body(js, "function _cardAmt(e)")
    bank = js_func_body(js, "function _bankOut(e)")
    assert "'card'" in card and "'card'" in bank
    # 互斥：卡費列的銀行支出必須是 0
    assert "? 0 :" in bank


def test_cashbook_card_column_hidden_when_book_has_no_cards():
    """母公司帳 0 筆刷卡 —— 永遠空的欄是雜訊。

    🔴 用 display:none 而不是不渲染：nth-child 數 DOM 位置，抽掉節點會讓
    後面每一欄的欄寬規則整排錯位。
    """
    css = _read("frontend/tabs/crm/crm.css")
    assert "#cash-list-panel:not(.has-card) .cash-col-card { display: none; }" in css
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    assert "classList.toggle('has-card'" in js


def test_card_summary_needs_opening_balance():
    """卡片未繳＝期初＋刷卡−還款。期初不可省 —— 資料起點前的卡債沒有它就對不平
    （2026-08-25 實測：不給期初算出 −36,988，實際是 +2,428；反推期初 39,416）。"""
    src = _read("routers/api_finance_card.py")
    assert "derive_opening_from" in src, "要能從『現在實際欠多少』反推期初"
    assert 'status.is_distinct_from("card")' in src, "還款不能把刷卡列也算進去"


def test_card_repay_account_dropdown_uses_bank_only():
    """「哪些算真銀行帳戶」的正本在 fin-utils.bankOnly（含 active 判斷、排除
    股東往來）—— 自己 filter 會漏掉 active，停用帳戶就出現在還款下拉裡，
    還款會落到死帳戶上（/simplify 第 2 輪抓到）。"""
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    assert "bankOnly as _bankOnly" in js
    panel = js.split("window._cashCardPanel = function ()")[1].split("\n};")[0]
    assert "_bankOnly(_bankAccounts" in panel
    assert ".filter(" not in panel, "帳戶清單只能問 bankOnly，不准就地再 filter 一次"


def test_cashbook_card_summary_skipped_on_pure_filters():
    """卡片餘額只跟 entity 有關 —— 換帳戶頁籤/搜尋/兩個篩選下拉都不該重算。
    🔴 cards 預設 true：漏標異動點＝數字過期（看不出來），漏標篩選點只是多打
    一次請求（看得出來、不傷帳）。"""
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    assert "async function loadEntries({ render = true, cards = true } = {})" in js
    assert js.count("cards: false") >= 4


def test_chart_dots_have_instant_hover_labels():
    """owner 2026-08-25「滑鼠移動到每個點點都可以看到當時的數字」——原本只有
    原生 <title>（2.4px 的點壓不準＋一秒延遲＝看不到）。釘：大命中區＋
    :hover 即亮的標籤。"""
    js = _read("frontend/tabs/finance/subviews/assets.js")
    assert 'r="9" fill="transparent"' in js, "沒有大命中區"
    assert ".fa-dot:hover .fa-lbl{visibility:visible;}" in js


def test_cashbook_surfaces_sub_item():
    """子項目（外出用餐/交通/書籍…3,180 筆）有匯進 DB 但 UI 原本不顯示 ——
    owner 以為漏匯了。釘：列上顯示＋可編輯＋搜尋涵蓋。"""
    js = _read("frontend/tabs/crm/crm-cashbook.js")
    assert "e.sub_item" in js, "清單沒顯示子項目"
    assert "{name:'sub_item', label:'子項目'" in js, "編輯欄位沒有子項目"
    py = finance_src()
    assert "CrmCashEntry.sub_item.ilike(ql)" in py, "搜尋沒涵蓋子項目"


def test_assets_overview_lists_per_account_cash():
    """owner 2026-08-25「這些帳戶與資料要呈現」——銀行現金那顆桶要能攤開成
    各帳戶分列，且口徑同一份（期初＋流水），不是第二份算法。"""
    py = _read("routers/api_finance_assets.py")
    assert '"bank_lines"' in py
    js = _read("frontend/tabs/finance/subviews/assets.js")
    assert "d.bank_lines" in js


def test_receivable_subview_is_project_based_and_gated():
    """私帳應收（owner 2026-08-25「除了應付以外，也需要有應收」）＝執行專案
    的投影 —— 🔴 不是 CRM 應收視圖（那支查 crm_invoices，owner 不開發票，
    對私帳恆空）。同 projects/gear：釘 mine ＋ finance_mine 指名門。"""
    js = _read("frontend/tabs/finance/subviews/receivable.js")
    assert "finFetchMine('/project-ledger')" in js
    assert "omgJumpLedgerProject" in js, "點列要能跳到執行專案（同一條交棒路）"
    html = _read("frontend/tabs/finance/finance.html")
    assert html.count('data-subview="receivable"') == 1, "側欄按鈕恰好一顆（插補丁曾重複）"
    # 指名門是宣告式的：按鈕掛 .fin-nav-mine-only、finance.js 一行 hideNav 收掉
    btn = [ln for ln in html.splitlines() if 'data-subview="receivable"' in ln][0]
    assert "fin-nav-mine-only" in btn
    fin = _read("frontend/tabs/finance/finance.js")
    assert fin.count("hideNav('.fin-nav-mine-only')") == 1


def test_projects_fy_filter_and_live_totals():
    """執行專案的年度篩選（7/1–6/30，同 fin-utils FY 口徑）＋合計列由畫面上
    的列即時計算 —— 選 FY 之後合計＝owner 年度表「實際營收」那排數字。"""
    js = _read("frontend/tabs/finance/subviews/projects.js")
    assert "_closeFY" in js and "m >= 7 ? y + 1 : y" in js
    assert 'id="fpl-fy"' in js
    seg = js.split("function _renderTotals")[1][:300]
    assert "_visible()" in seg, "合計要從畫面上的列算（不然篩選後合計不動）"


def test_fiscal_year_period_for_mine():
    """私帳結帳年度 7/1–6/30（owner 2026-08-26「我的結帳月份是每年 6 月 30，
    這塊預設為年」）：mine 模式期間預設＝年、年＝會計年度（翻成後端本來就吃
    的 from..to 區間 —— 後端零改動）；母公司照曆年、預設月不受影響。"""
    fu = _read("frontend/tabs/finance/fin-utils.js")
    assert "FISCAL_END_MONTH = 6" in fu
    period = js_func_body(fu, "export const defaultPeriodMode")
    # 釘**方向**：三個 token 都在的斷言，對 `finIsMine() ? 'month' : 'year'`
    # 一樣會過 —— 而那個顛倒正是這條規則唯一擋得住的 bug。
    assert re.search(r"finIsMine\(\)\s*\?\s*'year'", period), "私帳＝會計年度"
    assert "fiscalRange(" in fu and "data-fiscal" in fu
    for rel in ("frontend/tabs/finance/subviews/statements.js",
                "frontend/tabs/finance/subviews/dashboard.js"):
        js = _read(rel)
        assert "defaultPeriodMode() === 'year'" in js, rel


def test_household_subview_wiring():
    """🏠 家用（owner 2026-08-26「開一個家用記帳頁面（都我在記）」）：
    固定打 mine、指名門 class、記一筆＝寫一般收支列（資料仍在收支明細）。"""
    js = _read("frontend/tabs/finance/subviews/household.js")
    assert "finFetchMine('/household')" in js
    assert "crmFetch('/cash-entries'" in js
    assert "entity: 'mine'" in js
    html = _read("frontend/tabs/finance/finance.html")
    btn = [ln for ln in html.splitlines() if 'data-subview="household"' in ln]
    assert len(btn) == 1 and "fin-nav-mine-only" in btn[0]
