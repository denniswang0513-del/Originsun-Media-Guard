# -*- coding: utf-8 -*-
"""貸款利息要進損益表 —— 以及攤還表沒蓋到那些月份時，報表必須出聲。

owner 2026-08-24：「貸款會產生利息要繳，這塊需要補進損益表裡面」。

🔴 實測到的沉默失敗（這支測試存在的理由）：五筆貸款建檔時填的是「從下一期起
   的剩餘期數」，攤還表因此全部從 2026-09 起算。但帳上 2024-03 ~ 2026-08 有 30
   個月、每月都真的被銀行扣息，而利息費用是**按攤還表的 due_date 權責認列**
   （iter_loan_interest）—— 沒有期別就認列 0。

   為什麼沒人發現：現金流量表照樣勾稽為 0（繳款走 financing 那側，跟攤還表無
   關）、資產負債表的貸款餘額也看起來正常（用 opening_balance 當基準）。**只有
   損益表看不到那筆費用，而它不會抱怨自己少了東西。** 錢真的出去了、帳上也記
   了、三張表沒有一張出聲 —— 缺口只能靠明講浮出來。
"""
from core.finance_logic import (amortization_schedule, iter_loan_interest,
                                loan_interest_total, statement_warnings)

CAT = {("cash", "貸款繳款"): "loan", ("cash", "貸款撥款"): "loan"}


def _pay(month, day="15", **kw):
    d = dict(entry_date=f"{month}-{day}", category="貸款繳款",
             expense=5552, bank_account_id="acct1")
    d.update(kw)
    return d


def _period(month):
    return {"due_date": f"{month}-15", "principal_due": 0, "interest_due": 1850}


# ── 認列本身 ──────────────────────────────────────────────────

def test_interest_is_recognised_by_due_date_month():
    rows = [_period("2025-06"), _period("2025-07"), _period("2025-08")]
    assert loan_interest_total(rows, {"2025-06", "2025-07"}) == 3700
    assert loan_interest_total(rows, {"2025-09"}) == 0, "期間外不該認列"


def test_a_grace_period_payment_is_all_interest():
    """一銀三筆 2025-06 ~ 2026-05 是寬限期：銀行扣的 5,552 全部是利息，
    一毛本金都沒還。攤還表要能重現這件事（本金 0、利息＝實扣）。"""
    rows = amortization_schedule(
        principal=1000000, annual_rate=2.22, term_months=60, method="annuity",
        grace_months=12, first_payment_date="2025-06-15", start_date=None)
    grace = rows[:12]
    assert all(r["principal_due"] == 0 for r in grace), "寬限期不還本金"
    assert {r["interest_due"] for r in grace} == {1850}, "每期息＝本金×2.22%÷12"
    # 攤還期首期＝銀行實扣 21,791（貸款備註裡寫著的數字）
    first = rows[12]
    assert first["principal_due"] + first["interest_due"] == 21791


def test_the_schedule_must_span_the_whole_life_not_the_remainder():
    """🔴 這就是那個沉默失敗的形狀：同一筆貸款，填「剩餘 45 期」與填「完整
    60 期含 12 期寬限」，2025 年的利息一個是 0、一個是 38,850。**兩張攤還表
    都長得很正常**，差別只在起算點。"""
    m2025 = {f"2025-{i:02d}" for i in range(1, 13)}
    remainder = amortization_schedule(
        principal=1000000, annual_rate=2.22, term_months=45, method="annuity",
        grace_months=0, first_payment_date="2026-09-15", start_date=None)
    whole_life = amortization_schedule(
        principal=1000000, annual_rate=2.22, term_months=60, method="annuity",
        grace_months=12, first_payment_date="2025-06-15", start_date=None)
    assert loan_interest_total(remainder, m2025) == 0, \
        "這個樣本沒重現那個陷阱（剩餘期數版本不該蓋到 2025）"
    assert loan_interest_total(whole_life, m2025) == 1850 * 7, \
        "2025-06 ~ 2025-12 七期寬限息"


def test_zero_interest_periods_are_not_yielded():
    assert list(iter_loan_interest([{"due_date": "2025-06-15", "interest_due": 0}],
                                   {"2025-06"})) == []


# ── 缺口要出聲 ────────────────────────────────────────────────

def test_loan_payments_outside_the_schedule_raise_a_warning():
    """攤還表從 2026-09 起，帳上 2025-06 ~ 2025-08 卻有繳款 → 必須明講。"""
    entries = [_pay("2025-06"), _pay("2025-07"), _pay("2025-08")]
    w = statement_warnings(entries, [], CAT, ["2025-06", "2025-07", "2025-08"],
                           [_period("2026-09")])
    assert w["loan_gap_months"] == ["2025-06", "2025-07", "2025-08"]
    joined = " ".join(w["messages"])
    assert "攤還表涵蓋範圍外" in joined and "利息費用" in joined, \
        f"訊息沒把後果講出來：{w['messages']}"
    assert "2025-06 ~ 2025-08" in joined, "沒指出是哪幾個月"


def test_a_covered_month_is_silent():
    entries = [_pay("2025-06")]
    w = statement_warnings(entries, [], CAT, ["2025-06"], [_period("2025-06")])
    assert w["loan_gap_months"] == []
    assert not any("攤還表" in m for m in w["messages"])


def test_a_disbursement_before_the_first_period_is_not_a_gap():
    """🔴 撥款月（2025-05）本來就早於第一期繳款（2025-06）—— 那是正常的，
    不能報成缺口，否則每一筆貸款都會固定噴一條假警示，真的缺口就被稀釋掉。"""
    draw = dict(entry_date="2025-05-15", category="貸款撥款",
                deposit=1000000, expense=0, bank_account_id="acct1")
    w = statement_warnings([draw], [], CAT, ["2025-05"], [_period("2025-06")])
    assert w["loan_gap_months"] == [], "撥款（存入）被誤判成缺口"


def test_the_gap_check_respects_the_reporting_period():
    """期間外的繳款不該影響這期的警示（否則每期報表都在講別期的事）。"""
    w = statement_warnings([_pay("2024-01")], [], CAT, ["2025-06"],
                           [_period("2025-06")])
    assert w["loan_gap_months"] == []


def test_an_entry_hard_linked_to_a_period_still_counts_as_a_loan_payment():
    """收支掛上 loan_payment_id 時 treatment 走硬連結（不查 cat_map）——
    分類路徑換了，缺口偵測不能因此失效。"""
    e = _pay("2025-06", category="", loan_payment_id="p1")
    w = statement_warnings([e], [], {}, ["2025-06"], [_period("2026-09")])
    assert w["loan_gap_months"] == ["2025-06"]


# ── 真的接上去了嗎 ────────────────────────────────────────────

def test_the_statements_service_actually_passes_the_schedule_in():
    """算得出來還要真的傳下去 —— 少接這一步，上面每一條在正式報表裡都不生效。"""
    from tests.unit._srcscan import code_only, repo_src
    src = code_only(repo_src("services/finance_statements.py"))
    calls = src.count("statement_warnings(")
    assert calls == 2, f"呼叫點數量變了（{calls}）—— 新的那個也要帶攤還表"
    assert src.count('inputs["loan_payments"])') == 2, \
        "有 statement_warnings 沒帶 loan_payments —— 那條路徑上的缺口偵測是死的"
