# -*- coding: utf-8 -*-
"""貸款繳款記的是**銀行實扣**，不是攤還表算出來的（owner 2026-08-20 拍板）。

實測數字：合庫 315614 攤還表 4,456／銀行實扣 4,470；315611 是 25,286／25,290。
記攤還表那個數字的話，系統的銀行餘額每期歪十幾元且累積 —— owner 剛把三個帳戶
餘額對到分毫不差（869,478／155,078／4,538），這個偏差會慢慢把它吃掉。
而且對帳工作台要求金額完全相等才勾得掉，那些列會永遠配不上。
"""
from types import SimpleNamespace

from tests.unit._srcscan import code_only, func_body, repo_src  # noqa: E402


def _body(fn):
    return code_only(func_body(repo_src('routers/api_finance.py'), fn))


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def _pay(actual=None, principal=4152, interest=304):
    from routers.api_finance import _record_loan_payment
    s = FakeSession()
    loan = SimpleNamespace(name="合庫1（315614）", entity="parent")
    row = SimpleNamespace(id="p1", period_no=1, principal_due=principal,
                          interest_due=interest, status="scheduled",
                          paid_at=None, paid_amount=None, cash_entry_id=None)
    entry = _record_loan_payment(s, loan, row, "2026-09-08", "acct1",
                                 note="銀行對帳單匯入", actual_amount=actual)
    return entry, row


def test_actual_bank_amount_wins_over_the_schedule():
    entry, row = _pay(actual=4470)
    assert entry.expense == 4470, "記成攤還表的 4,456 → 銀行餘額每期歪 14 元"
    assert row.paid_amount == 4470


def test_note_spells_out_both_numbers_and_the_difference():
    """差額要看得見 —— 對不起來的時候人要知道是差在哪，不是自己去減。"""
    entry, _ = _pay(actual=4470)
    assert "4,470" in entry.note and "4,456" in entry.note and "+14" in entry.note


def test_no_actual_amount_falls_back_to_the_schedule():
    """手動繳款沒填金額時照舊 —— 這條路不能因為新欄位而變。"""
    entry, row = _pay(actual=None)
    assert entry.expense == 4456
    assert row.paid_amount == 4456
    assert "差" not in (entry.note or ""), "沒有差額就不要多嘴"


def test_principal_interest_split_still_comes_from_the_schedule():
    """現金看銀行的、本息拆分看攤還表 —— 貸款餘額的推進不可以被那幾元帶偏。"""
    _, row = _pay(actual=4470)
    assert (row.principal_due, row.interest_due) == (4152, 304)


def test_statement_import_passes_the_bank_amount_through():
    """匯入那條路真的有把銀行金額傳下去（不然上面幾條都白測）。"""
    body = _body('async def apply_bank_statement(')
    assert 'actual_amount=abs(int(r.amount or 0))' in body, \
        '對帳單匯入又改回記攤還表金額了'


def test_statement_import_also_fills_the_bank_side_of_the_workbench():
    """匯入要同時建對帳單明細並自動配對 —— 否則工作台會顯示「銀行 0 筆」。"""
    body = _body('async def apply_bank_statement(')
    assert 'BankStatementLine(' in body, '匯入沒有填工作台的銀行側'
    assert 'matched_entry_id=ce.id' in body, '建了卻沒配對，等於留一堆待處理給人'
