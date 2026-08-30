# -*- coding: utf-8 -*-
"""逐案損益要一眼回答五件事（owner 2026-08-29）：
① 營收 ② 客戶會匯給我多少 ③ 我要匯出去多少 ④ 還剩多少沒收 ⑤ 還剩多少沒付。
"""
from core.ledger_project import (PAYOUT_FIELDS, WITHHELD_FIELDS, client_wire,
                                 expected_cash_in, payout_total)

D = {"invoice_fee": 44000, "personal_tax": 5086, "outsource": 60000,
     "misc": 3000, "tax_fee": 1200, "buy_invoice": 800, "shareholder": 999}


def test_client_wire_deducts_only_what_is_withheld_at_source():
    """② 客戶會匯給我 ＝ 營收 − 源頭代扣。委外／雜支不扣 —— 那是收到錢之後
    我自己匯出去的，不是客戶少匯的。"""
    assert set(WITHHELD_FIELDS) == {"invoice_fee", "personal_tax"}
    assert client_wire(550000, D) == 550000 - 44000 - 5086
    assert client_wire(550000, {}) == 550000
    assert client_wire(0, D) == -49086       # 沒營收也照扣（負值看得出異常）


def test_client_wire_always_deducts_the_agency_fee():
    """🔴 與 `expected_cash_in` 的差別：那支只在案源＝代開發票時才扣代辦費
    （它服務收款狀態判定）；這支一律扣 —— owner 2026-08-29：「我收到的就已經
    是扣除後的了」，帳上記全額收入＋代辦費支出兩列只是為了看見全額。"""
    d = {"invoice_fee": 8000}
    assert client_wire(100000, d) == 92000
    assert expected_cash_in(100000, d) == 100000, "案源不是代開發票 → 那支不扣"
    assert expected_cash_in(100000, dict(d, source="代開發票")) == 92000


def test_payout_excludes_what_never_reached_us():
    """③ 要自己匯出去的 ＝ 委外＋雜支＋稅金＋買發票。
    🔴 不含代辦費與個人稅款 —— 那筆錢沒進來過，再算一次「要付出去」就是重複計。"""
    assert set(PAYOUT_FIELDS) == {"outsource", "misc", "tax_fee", "buy_invoice"}
    assert payout_total(D) == 60000 + 3000 + 1200 + 800
    assert "invoice_fee" not in PAYOUT_FIELDS and "personal_tax" not in PAYOUT_FIELDS
    assert payout_total({}) == 0


def test_endpoint_and_list_expose_all_five():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    api = (root / "routers/api_finance_projects.py").read_text(encoding="utf-8")
    assert '"client_wire": client_wire(' in api and '"payout": payout_total(' in api
    js = (root / "frontend/tabs/finance/subviews/projects.js").read_text(encoding="utf-8")
    head = js.split('<div class="crm-list-header"')[1].split("</div>")[0]
    for w in ("營收", "應收", "未收", "應付", "未付"):
        assert f">{w}</span>" in head, w
    row = js.split("function _renderList()")[1].split("_renderCount(")[0]
    for k in ("p.client_wire", "p.to_collect", "p.payout", "p.ap_open"):
        assert k in row, k


def test_to_collect_takes_both_bookkeeping_bases():
    """④ 一條規則要同時吃兩種記法（owner 2026-08-29「從新的帳開始，未收就會是
    應收為基準」）：

    · 舊帳：代開收款記**全額**、代辦費另記一筆支出 → 收齊時 已收＝營收，
      應收−已收 ＝ −代辦費
    · 新帳：收款直接記**淨額** → 收齊時 已收＝應收，差額 0
    兩種都該顯示「收齊了」。
    """
    from core.ledger_project import to_collect
    d = {"invoice_fee": 44000}          # 營收 550,000、客戶會匯 506,000
    assert to_collect(550000, 550000, d) == 0, "舊帳收齊（已收記全額）"
    assert to_collect(550000, 506000, d) == 0, "新帳收齊（已收記淨額）"
    assert to_collect(550000, 400000, d) == 106000, "還沒收完＝應收−已收"
    assert to_collect(550000, 0, d) == 506000


def test_real_overcollection_is_not_clamped_away():
    """🔴 只有「落在源頭代扣範圍內」的負差額才當 0（那是記法落差）。
    超出範圍的是真的溢收 —— 夾成 0 會讓它永遠沒人發現。
    2026-08-29 生產實測：164 案是記法落差、**1 案是真的溢收**
    （工程空拍：營收 0、已收 3,000）。"""
    from core.ledger_project import to_collect
    d = {"invoice_fee": 44000}
    assert to_collect(550000, 594000, d) == -88000, "多收了，照實顯示"
    assert to_collect(0, 3000, {}) == -3000, "沒有代扣可解釋 → 一毛都不夾"


def test_list_uses_to_collect_not_the_stored_receivable():
    """列表的未收走 to_collect —— `amount_receivable` 是營收−已收，
    收齊的代開案會差一個代辦費。"""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    api = (root / "routers/api_finance_projects.py").read_text(encoding="utf-8")
    assert "_tc = to_collect(" in api and '"to_collect": _tc' in api
    js = (root / "frontend/tabs/finance/subviews/projects.js").read_text(encoding="utf-8")
    row = js.split("function _renderList()")[1].split("_renderCount(")[0]
    assert "p.to_collect" in row and "p.receivable" not in row
