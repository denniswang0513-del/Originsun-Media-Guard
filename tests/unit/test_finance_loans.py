"""財務階段四（銀行貸款）黃金測試 — core/finance_logic.py 純函式。

攤還表 amortization_schedule 手算案例錨定（annuity P=100000/年利率 12%/12 期
→ PMT≈8,885、首期 interest=1000/principal=7885、末期本金吸尾差），
三法 × 寬限期 × Σprincipal==principal × due_date 月底邊界 × r=0 全覆蓋。

三表接線：一筆貸款跨月 → P&L 利息（權責按 due_date 進業外支出）/
BS 貸款餘額（非流動負債逐筆分列，單純看繳款事實）/ CF financing
（撥款+繳款收支 treatment='loan' 走科目 2400）各正確；貸款收支不進損益；
快照 merge（merge_pnl 逐月 == 全期）不壞。
"""
from datetime import datetime

import pytest

from core.finance_logic import (
    amortization_schedule,
    bank_balances_asof,
    build_balance_sheet,
    build_cashflow,
    build_pnl,
    cash_entry_activity,
    classify_cash_entry,
    NON_PNL_TREATMENTS,
    loan_interest_total,
    loan_outstanding_rows,
    merge_pnl,
)
from db.seed_finance import SEED_ACCOUNTS, SEED_CATEGORY_MAP
from services.finance_statements import _cf_side

# ── fixture：種子科目 + 對映（同 test_finance_statements 慣例）──
ACCOUNTS = {code: {"code": code, "name": name, "pnl_group": pnl_group,
                   "cf_activity": cf_activity, "acct_type": acct_type}
            for code, name, _plain, acct_type, cf_activity, pnl_group in SEED_ACCOUNTS}
CAT_MAP = {(r["source"], r["category_text"]): {"treatment": r["treatment"],
                                               "account_id": r["account_code"]}
           for r in SEED_CATEGORY_MAP}

Q1 = ["2026-01", "2026-02", "2026-03"]


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d")


# ═══ 攤還表黃金數字 ═════════════════════════════════════════

@pytest.fixture(scope="module")
def sched():
    return amortization_schedule(100000, 12.0, 12, "annuity", "2026-01-15")


class TestAnnuityGolden:
    """等額本息手算錨定：P=100000、年利率 12%（r=0.01）、12 期、起貸 1/15。

    PMT = 100000×0.01/(1−1.01⁻¹²) = 8884.88 → round 8885；
    p1: interest=round(100000×0.01)=1000、principal=8885−1000=7885；
    末期本金吸尾差 = 100000 − Σ前11期本金(91204) = 8796。
    """

    def test_shape_and_due_dates(self, sched):
        assert len(sched) == 12
        assert [r["period_no"] for r in sched] == list(range(1, 13))
        assert sched[0]["due_date"] == "2026-02-15"   # 起貸日下月同日
        assert sched[1]["due_date"] == "2026-03-15"
        assert sched[-1]["due_date"] == "2027-01-15"

    def test_first_periods_hand_computed(self, sched):
        assert (sched[0]["interest_due"], sched[0]["principal_due"]) == (1000, 7885)
        assert (sched[1]["interest_due"], sched[1]["principal_due"]) == (921, 7964)
        # p3：餘額 84151 → interest round(841.51)=842、principal 8885−842=8043
        assert (sched[2]["interest_due"], sched[2]["principal_due"]) == (842, 8043)

    def test_last_period_absorbs_remainder(self, sched):
        assert (sched[10]["interest_due"], sched[10]["principal_due"]) == (175, 8710)
        # 末期：餘額 8796 → 本金吸尾差 8796、interest round(87.96)=88
        assert (sched[11]["interest_due"], sched[11]["principal_due"]) == (88, 8796)

    def test_principal_sums_exactly(self, sched):
        assert sum(r["principal_due"] for r in sched) == 100000

    def test_interest_monotonic_decreasing(self, sched):
        interests = [r["interest_due"] for r in sched]
        assert interests == sorted(interests, reverse=True)


class TestStraightAndInterestOnly:
    def test_straight_golden(self):
        # 等額本金：P=120000/12%/12 → 本金 10000/期；利息 1200,1100,…,100
        sched = amortization_schedule(120000, 12.0, 12, "straight", "2026-01-05")
        assert all(r["principal_due"] == 10000 for r in sched)
        assert [r["interest_due"] for r in sched] == [
            1200, 1100, 1000, 900, 800, 700, 600, 500, 400, 300, 200, 100]
        assert sum(r["principal_due"] for r in sched) == 120000

    def test_straight_remainder_on_last(self):
        # P=1000/3 期 → 333+333+334（末期吸尾差）
        sched = amortization_schedule(1000, 0, 3, "straight", "2026-01-05")
        assert [r["principal_due"] for r in sched] == [333, 333, 334]

    def test_interest_only_golden(self):
        # 按月付息：P=100000/12%/6 → 每期息 1000、末期加還全額本金
        sched = amortization_schedule(100000, 12.0, 6, "interest_only", "2026-01-10")
        assert [r["interest_due"] for r in sched] == [1000] * 6
        assert [r["principal_due"] for r in sched] == [0, 0, 0, 0, 0, 100000]
        assert sum(r["principal_due"] for r in sched) == 100000


class TestGraceAndZeroRate:
    def test_annuity_with_grace(self):
        # 寬限 3 期只付息；第 4 期起 n=9 等額本息 PMT=round(1000/(1−1.01⁻⁹))=11674
        sched = amortization_schedule(100000, 12.0, 12, "annuity", "2026-01-15",
                                      grace_months=3)
        assert len(sched) == 12
        assert [(r["principal_due"], r["interest_due"]) for r in sched[:3]] == \
            [(0, 1000)] * 3
        assert sched[3]["interest_due"] == 1000
        assert sched[3]["principal_due"] == 11674 - 1000
        assert sum(r["principal_due"] for r in sched) == 100000

    def test_straight_with_grace(self):
        sched = amortization_schedule(100000, 12.0, 12, "straight", "2026-01-15",
                                      grace_months=2)
        assert [r["principal_due"] for r in sched[:2]] == [0, 0]
        assert sched[2]["principal_due"] == 10000  # round(100000/10)
        assert sum(r["principal_due"] for r in sched) == 100000

    def test_zero_rate_all_methods(self):
        # r=0 邊界：利息全 0
        ann = amortization_schedule(12000, 0, 12, "annuity", "2026-01-15")
        assert all(r["interest_due"] == 0 for r in ann)
        assert [r["principal_due"] for r in ann] == [1000] * 12
        st = amortization_schedule(12000, 0, 12, "straight", "2026-01-15")
        assert [r["principal_due"] for r in st] == [1000] * 12
        io = amortization_schedule(5000, 0, 3, "interest_only", "2026-01-15")
        assert [(r["principal_due"], r["interest_due"]) for r in io] == \
            [(0, 0), (0, 0), (5000, 0)]


class TestDueDates:
    def test_month_end_overflow_keeps_anchor_day(self):
        # 起貸 1/31 → 2/28（溢出夾到月底）、3/31、4/30、5/31（錨定日保留 31）
        sched = amortization_schedule(60000, 12.0, 4, "straight", "2026-01-31")
        assert [r["due_date"] for r in sched] == [
            "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31"]

    def test_first_payment_date_anchor(self):
        # 指定首期繳款日 → 由它起算每月同日（不再是起貸日下月）
        sched = amortization_schedule(10000, 0, 3, "straight", "2026-01-05",
                                      first_payment_date="2026-03-01")
        assert [r["due_date"] for r in sched] == [
            "2026-03-01", "2026-04-01", "2026-05-01"]

    def test_accepts_datetime_inputs(self):
        sched = amortization_schedule(10000, 0, 2, "straight", _d("2026-01-15"))
        assert sched[0]["due_date"] == "2026-02-15"


class TestValidation:
    @pytest.mark.parametrize("kwargs", [
        dict(principal=0, annual_rate=1, term_months=12, method="annuity",
             start_date="2026-01-15"),
        dict(principal=1000, annual_rate=1, term_months=0, method="annuity",
             start_date="2026-01-15"),
        dict(principal=1000, annual_rate=1, term_months=12, method="foo",
             start_date="2026-01-15"),
        dict(principal=1000, annual_rate=1, term_months=12, method="annuity",
             start_date="2026-01-15", grace_months=12),  # 寬限 ≥ 期數
        dict(principal=1000, annual_rate=1, term_months=12, method="annuity",
             start_date=None),  # 無任何有效日期
    ])
    def test_invalid_raises(self, kwargs):
        with pytest.raises(ValueError):
            amortization_schedule(**kwargs)


# ═══ 三表接線黃金測試（一筆貸款跨月）════════════════════════
# 貸款：文創貸款 P=100000、12%、annuity 12 期、起貸 2026-01-15
# → p1 due 2026-02-15（息 1000/本 7885）、p2 due 2026-03-15（息 921/本 7964）
# p1 已繳（2026-02-15，自動收支 LC1 expense=8885）；另有撥款收支 LC0 +100000。

_SCHED = amortization_schedule(100000, 12.0, 12, "annuity", "2026-01-15")

LOAN = {"id": "L1", "name": "文創貸款", "principal": 100000,
        "opening_balance": None, "start_date": _d("2026-01-15"),
        "status": "active", "bank_account_id": "A"}

LOAN_PAYS = [dict(id=f"LP{r['period_no']}", loan_id="L1",
                  period_no=r["period_no"], due_date=r["due_date"],
                  principal_due=r["principal_due"],
                  interest_due=r["interest_due"],
                  status=("paid" if r["period_no"] == 1 else "scheduled"),
                  paid_at=("2026-02-15" if r["period_no"] == 1 else None))
             for r in _SCHED]

CASH_L = [
    # 撥款（deposit）與繳款（expense=本+息）皆 treatment='loan'
    {"id": "LC0", "entry_date": _d("2026-01-15"), "deposit": 100000,
     "category": "貸款撥款", "bank_account_id": "A", "summary": "文創貸款撥款"},
    {"id": "LC1", "entry_date": _d("2026-02-15"), "expense": 8885,
     "category": "貸款繳款", "bank_account_id": "A", "summary": "文創貸款 第1期"},
]

BANKS = [{"id": "A", "name": "主帳戶", "opening_balance": 0,
          "opening_date": _d("2026-01-01"), "acct_kind": "bank", "active": True}]

_PNL_KW = dict(cash_entries=CASH_L, loan_payments=LOAN_PAYS,
               cat_map=CAT_MAP, accounts=ACCOUNTS)


class TestLoanClassification:
    def test_classify_loan_treatment(self):
        assert classify_cash_entry({"category": "貸款繳款"}, CAT_MAP) == "loan"
        assert classify_cash_entry({"category": "貸款撥款"}, CAT_MAP) == "loan"

    def test_loan_payment_id_hard_link_beats_category(self):
        # F1：pay 自動建的收支靠硬連結 loan_payment_id 認 'loan'，壓過文字 category —
        # 使用者改 category 或 admin 改對映都不會讓繳款誤入損益。
        other = next(cat for (src, cat), v in CAT_MAP.items()
                     if src == "cash" and v["treatment"] != "loan")
        assert classify_cash_entry({"category": other}, CAT_MAP) != "loan"
        assert classify_cash_entry(
            {"category": other, "loan_payment_id": "LP1"}, CAT_MAP) == "loan"
        # 空 category + 硬連結也不掉進 unmapped
        assert classify_cash_entry({"loan_payment_id": "LP1"}, CAT_MAP) == "loan"

    def test_non_pnl_treatments_named_class(self):
        # F3：不進損益的等價類單一具名來源
        assert {"transfer", "passthrough", "loan"} <= NON_PNL_TREATMENTS

    def test_activity_is_financing(self):
        # (cash, 貸款繳款/撥款) → 科目 2400 cf_activity=financing
        assert cash_entry_activity(CASH_L[0], CAT_MAP, ACCOUNTS) == "financing"
        assert cash_entry_activity(CASH_L[1], CAT_MAP, ACCOUNTS) == "financing"


class TestLoanPnl:
    def test_interest_accrued_by_due_date(self):
        # Q1 落 p1(2/15)+p2(3/15) → 1000+921 = 1921（不管繳沒繳）
        assert loan_interest_total(LOAN_PAYS, set(Q1)) == 1921

    def test_pnl_interest_in_non_operating(self):
        pnl = build_pnl(Q1, **_PNL_KW)
        assert pnl["non_operating"]["expense"] == [
            {"label": "利息費用", "amount": 1921}]
        assert pnl["non_operating"]["total"] == -1921
        assert pnl["net"]["amount"] == -1921

    def test_loan_cash_entries_not_in_pnl(self):
        # 撥款不是收入、繳款不是費用 — treatment='loan' 不進損益
        pnl = build_pnl(Q1, **_PNL_KW)
        assert pnl["revenue"]["total"] == 0
        assert pnl["cost"]["total"] == 0
        assert pnl["opex"]["total"] == 0
        assert pnl["non_operating"]["income"] == []

    def test_merge_pnl_monthly_equals_full(self):
        # 快照 merge 相容：利息走既有 non_operating 結構，逐月可加總
        parts = [build_pnl([m], **_PNL_KW) for m in Q1]
        assert merge_pnl(parts, 3) == build_pnl(Q1, **_PNL_KW)


class TestLoanBalanceSheet:
    def test_outstanding_rows(self):
        # 已繳 p1 本金 7885 → 100000 − 7885 = 92115
        rows = loan_outstanding_rows([LOAN], LOAN_PAYS, "2026-03")
        assert rows == [{"key": "loan:L1", "label": "文創貸款", "amount": 92115}]

    def test_outstanding_ignores_future_payment(self):
        # as_of 2026-01：p1 繳款月（2026-02）在 as_of 之後 → 餘額仍 100000
        rows = loan_outstanding_rows([LOAN], LOAN_PAYS, "2026-01")
        assert rows[0]["amount"] == 100000

    def test_not_listed_before_start_month(self):
        assert loan_outstanding_rows([LOAN], LOAN_PAYS, "2025-12") == []

    def test_opening_balance_takes_precedence(self):
        old = {"id": "L2", "name": "舊貸", "principal": 500000,
               "opening_balance": 200000, "start_date": _d("2025-06-01"),
               "status": "active"}
        rows = loan_outstanding_rows([old], [], "2026-03")
        assert rows[0]["amount"] == 200000  # 導入舊貸以剩餘本金為基準

    def test_paid_off_not_listed(self):
        pays = [dict(p, status="paid", paid_at=p["due_date"]) for p in LOAN_PAYS]
        assert loan_outstanding_rows([LOAN], pays, "2027-12") == []

    def test_bs_noncurrent_liability_lines(self):
        pnl = build_pnl(Q1, **_PNL_KW)
        bs = build_balance_sheet(
            "2026-03",
            bank_lines=bank_balances_asof(BANKS, CASH_L, "2026-03"),
            loan_rows=loan_outstanding_rows([LOAN], LOAN_PAYS, "2026-03"),
            cumulative_net=pnl["net"]["amount"])
        nc = bs["liabilities"]["noncurrent"]
        assert [(x["key"], x["label"], x["amount"]) for x in nc] == [
            ("loan:L1", "文創貸款", 92115)]
        assert bs["liabilities"]["total"] == 92115      # 流動 0 + 非流動 92115
        assert bs["assets"]["total"] == 91115           # 0 + 100000 − 8885
        # 流動比率分母只算流動負債（0 → None）；負債比吃負債總計
        assert bs["ratios"]["current_ratio"] is None
        assert bs["ratios"]["debt_ratio"] == round(92115 * 100 / 91115, 2)
        # 檢核差額 = 未繳到期利息 921（權責認列了、現金還沒出）— 誠實外顯
        assert bs["check"]["diff"] == 91115 - 92115 - (-1921) == 921

    def test_bs_identity_zero_before_first_due(self):
        # 撥款當月（尚無到期利息）：現金 100000 = 貸款 100000 + 權益 0 → diff 0
        jan = ["2026-01"]
        pnl = build_pnl(jan, **_PNL_KW)
        assert pnl["net"]["amount"] == 0
        bs = build_balance_sheet(
            "2026-01",
            bank_lines=bank_balances_asof(BANKS, CASH_L, "2026-01"),
            loan_rows=loan_outstanding_rows([LOAN], LOAN_PAYS, "2026-01"),
            cumulative_net=0)
        assert bs["assets"]["total"] == 100000
        assert bs["liabilities"]["total"] == 100000
        assert bs["check"]["diff"] == 0


class TestLoanCashflow:
    def _cf(self, months):
        from core.finance_logic import shift_month
        opening = _cf_side(bank_balances_asof(BANKS, CASH_L,
                                              shift_month(months[0], -1)))
        closing = _cf_side(bank_balances_asof(BANKS, CASH_L, months[-1]))
        return build_cashflow(months, opening=opening, closing=closing,
                              cash_entries=CASH_L, cat_map=CAT_MAP,
                              accounts=ACCOUNTS)

    def test_financing_activity_and_identity(self):
        cf = self._cf(Q1)
        # 撥款 +100000、繳款 −8885 → financing 91115；operating 0
        assert cf["financing"] == 91115
        assert cf["operating"] == 0
        assert cf["investing"] == 0
        assert cf["net"] == 91115
        assert cf["opening"]["total"] == 0
        assert cf["closing"]["total"] == 91115
        assert cf["check"]["diff"] == 0  # 貸款收支恆等式自然成立

    def test_disbursement_month_only(self):
        cf = self._cf(["2026-01"])
        assert cf["financing"] == 100000
        assert cf["check"]["diff"] == 0
