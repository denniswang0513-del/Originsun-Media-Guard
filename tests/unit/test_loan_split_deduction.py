# -*- coding: utf-8 -*-
"""銀行把「一期」拆成好幾列扣的時候，每一列都要進帳（owner 2026-08-21）。

一銀那三筆是分兩次撥款的，所以銀行每月扣兩列而不是一列：
    150 萬 → 29,418 + 3,269 ＝ 32,687（攤還表一期就是 32,687）
    50 萬 →  9,806 + 1,090 ＝ 10,896

🔴 從前對帳單匯入看到「這期 status 已經是 paid」就把整列丟掉（本意是防重跑），
於是第二列無聲蒸發 —— 沒有收支列、沒進 skipped_duplicates、回應的 entries 也
不算它。錢真的從銀行出去了，帳上卻一毛沒記：一銀兩筆合計每月憑空少 4,359，
而 owner 剛把三個帳戶餘額對到分毫不差。

現在防重跑改由兩層擋：① 帳上已有同日同額的列（跟一般列同一套 seen 多重集）；
② 這一期已經記滿攤還表金額。兩層都不會誤殺分筆扣的第二列。
"""
from types import SimpleNamespace

from tests.unit._srcscan import code_only, func_body, repo_src


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def _period(total=32687, principal=30078, interest=2609):
    return SimpleNamespace(id="p1", period_no=1, principal_due=principal,
                           interest_due=interest, status="scheduled",
                           paid_at=None, paid_amount=None, cash_entry_id=None)


def _loan():
    return SimpleNamespace(name="一銀 150萬（中小多元發展有補貼）", entity="parent")


def _record(session, row, amount, split_total):
    from routers.api_finance import _record_loan_payment
    return _record_loan_payment(session, _loan(), row, "2026-09-15", "acct",
                                note="銀行對帳單匯入", actual_amount=amount,
                                split_total=split_total)


def test_both_lines_of_a_split_deduction_are_booked():
    """🔴 兩列都要建收支明細 —— 第二列被丟掉＝帳上少 3,269。"""
    s, row = FakeSession(), _period()
    _record(s, row, 29418, 32687)
    _record(s, row, 3269, 32687)
    assert len(s.added) == 2, "分筆扣的第二列沒有進帳"
    assert sorted(e.expense for e in s.added) == [3269, 29418]


def test_the_period_remembers_the_full_amount_not_just_the_last_line():
    """累加而不是覆蓋 —— 覆蓋的話這期只記得最後那筆 3,269。"""
    s, row = FakeSession(), _period()
    _record(s, row, 29418, 32687)
    _record(s, row, 3269, 32687)
    assert row.paid_amount == 32687
    assert row.status == "paid"


def test_the_period_keeps_the_first_line_as_its_representative():
    """paid_at 與 cash_entry_id 留第一筆的 —— 第二筆蓋掉會讓這期指向零頭那列。"""
    s, row = FakeSession(), _period()
    first = _record(s, row, 29418, 32687)
    _record(s, row, 3269, 32687)
    assert row.cash_entry_id == first.id
    assert row.paid_at == "2026-09-15"


def test_both_lines_carry_the_hard_link_to_the_loan_period():
    """兩列都要有 loan_payment_id —— 少了它 classify 會把那筆當一般支出進損益，
    而利息已按攤還表權責認列過，等於雙算。"""
    s, row = FakeSession(), _period()
    _record(s, row, 29418, 32687)
    _record(s, row, 3269, 32687)
    assert all(e.loan_payment_id == "p1" for e in s.added)


def test_split_note_says_this_line_and_the_period_total():
    """註記要講清楚本筆多少、本期合計多少 —— 只寫「差 -3,269」會讓人以為短繳。"""
    s, row = FakeSession(), _period()
    _record(s, row, 29418, 32687)
    note = s.added[0].note
    assert "29,418" in note and "32,687" in note
    assert "分筆扣款" in note
    assert "差" not in note, "合計等於攤還表就不該說有差額"


def test_single_line_deduction_is_untouched():
    """單列扣款（合庫）行為與從前一字不差：實扣 4,470、攤還表 4,456、註記說差 +14。"""
    from routers.api_finance import _record_loan_payment
    s = FakeSession()
    row = SimpleNamespace(id="p9", period_no=1, principal_due=4152,
                          interest_due=304, status="scheduled",
                          paid_at=None, paid_amount=None, cash_entry_id=None)
    loan = SimpleNamespace(name="合庫1（315614）", entity="parent")
    _record_loan_payment(s, loan, row, "2026-09-08", "acct",
                         note="銀行對帳單匯入", actual_amount=4470)
    assert s.added[0].expense == 4470 and row.paid_amount == 4470
    assert "銀行實扣 4,470" in s.added[0].note and "差 +14" in s.added[0].note


# ── 匯入端：防重跑的兩層必須都在，而且不能是「paid 就整列跳過」──────

def _apply_body():
    return code_only(func_body(repo_src("routers/api_finance_stmt.py"),
                               "async def apply_bank_statement("))


def test_apply_no_longer_drops_rows_just_because_the_period_is_paid():
    """🔴 回歸釘：`status == "paid"` 直接 continue 是這個 bug 的原形。"""
    body = _apply_body()
    assert 'if (row.status or "") == "paid":' not in body, \
        "又用「這期 paid 就整列跳過」擋重跑了 —— 分筆扣的第二列會再次無聲消失"


def test_apply_still_guards_against_re_import():
    """但重跑防護不能一起拿掉：兩層都要在。"""
    body = _apply_body()
    assert "seen[key] > 0" in body, "貸款列沒走同日同額的重複防護"
    assert "already >= total_due" in body, "少了「該期已記滿」那層"


def test_apply_tells_the_user_what_it_skipped():
    """跳過要說出來 —— 無聲跳過正是這個 bug 四個月沒被發現的原因。"""
    body = _apply_body()
    i = body.index("already >= total_due")
    assert "skipped_dup.append" in body[i:i + 400], "該期已記滿卻沒告訴使用者"


def test_apply_sums_the_lines_of_one_period():
    """要先把同一期的列加總才知道銀行這期扣了多少（註記與判定都靠它）。"""
    body = _apply_body()
    assert "split_totals" in body and "split_total=split_totals[" in body


# ── 取消繳款要把這期恢復乾淨（分筆扣讓這件事變得會咬人）────────────

def _unpay_body():
    return code_only(func_body(repo_src("routers/api_finance.py"),
                               "async def unpay_loan_period("))


def test_unpay_deletes_every_entry_of_the_period():
    """🔴 一期有兩筆收支時，只刪 cash_entry_id 指到的那筆會留下孤兒 ——
    期別已回 scheduled，下次匯入又記一遍，零頭那筆就變雙份。"""
    body = _unpay_body()
    assert "CrmCashEntry.loan_payment_id == row.id" in body, \
        "unpay 還是只認 cash_entry_id，分筆扣的第二筆會留在帳上"


def test_unpay_clears_the_paid_amount():
    """🔴 paid_amount 沒清 → 這期下次被記時從舊數字往上加，一下就超過攤還表
    而被當「已記滿」跳過。實測就是這樣吃掉一列（21,791/29,418/3,269 只進 4 筆）。"""
    body = _unpay_body()
    assert "row.paid_amount = None" in body, "unpay 沒清 paid_amount"


def test_accumulation_ignores_a_stale_amount_on_an_unpaid_period():
    """就算舊資料留著髒的 paid_amount，只要這期不是 paid 就當 0（雙保險）。"""
    from routers.api_finance import _record_loan_payment
    s = FakeSession()
    row = _period()
    row.paid_amount = 29418        # unpay 沒清乾淨的舊值
    row.status = "scheduled"
    _record_loan_payment(s, _loan(), row, "2026-09-15", "acct",
                         actual_amount=29418, split_total=32687)
    assert row.paid_amount == 29418, "把舊值加上去了"
