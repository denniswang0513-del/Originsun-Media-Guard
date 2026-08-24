# -*- coding: utf-8 -*-
"""/my-ledger.html 版面釘子 —— 固定視窗高的殼要能捲。

2026-08-24 實測：財務 tab 的 #finance-content 帶 inline `overflow:hidden`
（原意是防 flex 橫向撐破），在主系統沒事（整頁跟著 body 捲），但
/my-ledger.html 是 `height:calc(100vh-40px); overflow:hidden` 的殼 ——
縱向也被關掉且**外部樣式表覆寫不了 inline** → 資產儀表板圖表以下整段
看不到、哪一層都捲不動。修法是兩半，缺一不可，所以兩半都釘。
"""
from pathlib import Path

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
    fn = js.split("function _fitBody()")[1].split("\n}")[0]
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
    card = js.split("function _cardAmt(e)")[1].split("\n}")[0]
    bank = js.split("function _bankOut(e)")[1].split("\n}")[0]
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
