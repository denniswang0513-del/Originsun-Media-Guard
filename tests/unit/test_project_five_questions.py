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
    for k in ("p.client_wire", "p.receivable", "p.payout", "p.ap_open"):
        assert k in row, k
