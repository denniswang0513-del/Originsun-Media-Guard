"""財務階段三 三表推導引擎黃金測試（core/finance_logic.py 純函式）。

手工構造 3 個月合成資料（2026-01..03）：發票已收/未收/代開/作廢/進項、
請款已付/未付/預支/轉存、收支各 treatment（含未歸類/未掛帳戶/匯費）、
器材折舊跨月（含餘數月與除役月）、調整列 opening/owner/correction —
三表預期數字全部手算寫死斷言。另附「乾淨資料」恆等式測試
（BS diff=0、CF 期初+淨流=期末）與 period_months 各格式。

科目/對映 fixture 直接取 db/seed_finance.py 種子（值形狀 =
{"treatment", "account_id"} — 同時回歸驗 classify_cash_entry 的 dict 相容）。
"""
from datetime import datetime

import pytest

from core.finance_logic import (
    accumulated_depreciation,
    ap_open_payments,
    ar_open_invoices,
    bank_balances_asof,
    build_balance_sheet,
    build_cashflow,
    build_pnl,
    cash_entry_activity,
    classify_cash_entry,
    depreciation_for_month,
    equipment_net_rows,
    invoice_ex_tax,
    invoice_tax,
    merge_cf,
    merge_pnl,
    month_range,
    period_months,
    shift_month,
    statement_interpretation,
    statement_warnings,
    vat_position,
)
from db.seed_finance import SEED_ACCOUNTS, SEED_CATEGORY_MAP
from services.finance_statements import VALID_DRILL_KINDS, _cf_side

# ── fixture：種子科目 + 對映（id == code，同 seed 慣例）──────
ACCOUNTS = {code: {"code": code, "name": name, "pnl_group": pnl_group,
                   "cf_activity": cf_activity, "acct_type": acct_type}
            for code, name, _plain, acct_type, cf_activity, pnl_group in SEED_ACCOUNTS}
CAT_MAP = {(r["source"], r["category_text"]): {"treatment": r["treatment"],
                                               "account_id": r["account_code"]}
           for r in SEED_CATEGORY_MAP}

Q1 = ["2026-01", "2026-02", "2026-03"]


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d")


# ── 黃金合成資料 ─────────────────────────────────────────────

INVOICES = [
    # INV1 收款/專案 已收（Jan 認列、Feb 收現）
    {"id": "INV1", "payment_type": "收款", "issue_status": "已開立",
     "payment_status": "已收款", "category": "專案", "amount_total": 105000,
     "amount_ex_tax": 100000, "tax_amount": 5000, "commission": None,
     "invoice_date": _d("2026-01-10"), "paid_date": _d("2026-02-05")},
    # INV2 收款/專案 未收（ex_tax 缺值 → round(210000/1.05)=200000）
    {"id": "INV2", "payment_type": "收款", "issue_status": "已開立",
     "payment_status": "未收款", "category": "專案", "amount_total": 210000,
     "amount_ex_tax": None, "tax_amount": 10000, "commission": None,
     "invoice_date": _d("2026-02-15"), "paid_date": None},
    # INV3 內部代開（commission 1000 → 業外收入；稅額進銷項）
    {"id": "INV3", "payment_type": "收款", "issue_status": "已開立",
     "payment_status": "未收款", "category": "內部代開", "amount_total": 52500,
     "amount_ex_tax": 50000, "tax_amount": 2500, "commission": 1000,
     "invoice_date": _d("2026-02-20"), "paid_date": None},
    # INV4 作廢 → 全部不計
    {"id": "INV4", "payment_type": "收款", "issue_status": "作廢",
     "payment_status": "未收款", "category": "專案", "amount_total": 99999,
     "amount_ex_tax": None, "tax_amount": None, "commission": None,
     "invoice_date": _d("2026-01-15"), "paid_date": None},
    # INV5 付款發票（進項稅 1000；不進營收）
    {"id": "INV5", "payment_type": "付款", "issue_status": "已開立",
     "payment_status": "未收款", "category": "專案", "amount_total": 21000,
     "amount_ex_tax": 20000, "tax_amount": 1000, "commission": None,
     "invoice_date": _d("2026-03-05"), "paid_date": None},
]

PAYMENTS = [
    # PAY1 外包 已付（Jan 認列費用；Feb 付現走 C2 ap_settlement）
    {"id": "PAY1", "is_advance": 0, "request_date": _d("2026-01-20"),
     "amount": 20000, "category": "專案外包", "summary": "外包A",
     "payee_name": "王", "payment_status": "已付款", "payment_date": _d("2026-02-01")},
    # PAY2 外包 未付（Mar 認列；期末 AP）
    {"id": "PAY2", "is_advance": 0, "request_date": _d("2026-03-02"),
     "amount": 30000, "category": "專案外包", "summary": "外包B",
     "payee_name": "李", "payment_status": "未付款", "payment_date": None},
    # PAY3 預支款 → 不進損益；未結清餘額進 BS 員工往來
    {"id": "PAY3", "is_advance": 1, "request_date": _d("2026-01-05"),
     "amount": 40000, "category": "專案外包", "summary": "預支製片",
     "payee_name": "張", "payment_status": "應付款", "payment_date": None},
    # PAY4 零用金（treatment=transfer）→ 不進損益、不進 AP
    {"id": "PAY4", "is_advance": 0, "request_date": _d("2026-02-10"),
     "amount": 10000, "category": "零用金", "summary": "零用金補充",
     "payee_name": "", "payment_status": "未付款", "payment_date": None},
]

CASH = [
    # C1 AR 收現（INV1）→ 不進損益；CF operating +105000
    {"id": "C1", "entry_date": _d("2026-02-05"), "deposit": 105000,
     "invoice_id": "INV1", "bank_account_id": "A", "summary": "INV1 收款"},
    # C2 AP 付現（PAY1）→ 不進損益；CF operating −20000
    {"id": "C2", "entry_date": _d("2026-02-01"), "expense": 20000,
     "payment_request_id": "PAY1", "bank_account_id": "A", "summary": "付外包A"},
    # C3 direct_expense 房租（6200 管理）
    {"id": "C3", "entry_date": _d("2026-01-05"), "expense": 12000,
     "category": "房租", "bank_account_id": "A", "summary": "一月房租"},
    # C4 direct_expense 設備耗材（6230 → 營業成本-料）+ 匯費 30
    {"id": "C4", "entry_date": _d("2026-02-10"), "expense": 3000, "bank_fee": 30,
     "category": "設備耗材", "bank_account_id": "A", "summary": "SD卡"},
    # C5 direct_income 銀行利息（4220 業外收入）
    {"id": "C5", "entry_date": _d("2026-03-01"), "deposit": 500,
     "category": "銀行利息", "bank_account_id": "A", "summary": "利息"},
    # C6/C7 預支 發款/還款（advance）→ 不進損益、CF 活動不列（note 外顯）
    {"id": "C6", "entry_date": _d("2026-01-06"), "expense": 40000,
     "advance_payment_id": "PAY3", "bank_account_id": "A", "summary": "預支發款"},
    {"id": "C7", "entry_date": _d("2026-03-20"), "deposit": 1000,
     "advance_payment_id": "PAY3", "bank_account_id": "A", "summary": "預支還款"},
    # C8/C9 轉存成對（A→B 50000）→ 不進損益、CF 本金互抵
    {"id": "C8", "entry_date": _d("2026-02-14"), "expense": 50000,
     "category": "轉存", "bank_account_id": "A", "summary": "轉出"},
    {"id": "C9", "entry_date": _d("2026-02-14"), "deposit": 50000,
     "category": "轉存", "bank_account_id": "B", "summary": "轉入"},
    # C10 營業稅繳納（tax_vat）→ 不進損益；vat.paid
    {"id": "C10", "entry_date": _d("2026-03-10"), "expense": 5250,
     "category": "營業稅", "bank_account_id": "A", "summary": "繳營業稅"},
    # C11 營所稅（tax_income）→ 損益稅區
    {"id": "C11", "entry_date": _d("2026-03-15"), "expense": 7000,
     "category": "營所稅", "bank_account_id": "A", "summary": "繳營所稅"},
    # C12 未歸類 category → 管理/未歸類支出 + warning
    {"id": "C12", "entry_date": _d("2026-02-01"), "expense": 1000,
     "category": "幽靈類別", "bank_account_id": "A", "summary": "??"},
    # C13 未掛帳戶 → 損益照計（行 6330 管理）、CF 排除 + warning
    {"id": "C13", "entry_date": _d("2026-02-03"), "expense": 800,
     "category": "行政", "bank_account_id": None, "summary": "郵資"},
]

EQUIPMENT = [
    # EQ1 36000/36 個月 → 1000/月；2025-12 購入（baseline 前照算）
    {"id": "EQ1", "name": "攝影機", "purchase_cost": 36000,
     "purchase_date": _d("2025-12-15"), "depreciation_months": 36,
     "retired_date": None, "status": "在庫"},
    # EQ2 10000/10 個月；2026-02 購入、2026-03 除役 → 只折 Feb 1000；BS 出表
    {"id": "EQ2", "name": "燈", "purchase_cost": 10000,
     "purchase_date": _d("2026-02-10"), "depreciation_months": 10,
     "retired_date": _d("2026-03-05"), "status": "除役"},
    # EQ3 1000/3 個月 → 333+333+334（餘數掛最後一攤月）
    {"id": "EQ3", "name": "腳架", "purchase_cost": 1000,
     "purchase_date": _d("2026-01-05"), "depreciation_months": 3,
     "retired_date": None, "status": "在庫"},
]

# 預支核銷支出（crm_project_expenses 有掛 advance_id 者；date=created_at）
ADV_EXPENSES = [{"id": "E1", "date": _d("2026-02-20"), "amount": 38000,
                 "label": "場地+交通", "category": "其他"}]

ADJUSTMENTS = [
    {"id": "ADJ1", "adj_date": _d("2026-01-01"), "amount": 50000,
     "adj_type": "opening", "account_id": "3100"},
    {"id": "ADJ2", "adj_date": _d("2026-02-01"), "amount": 10000,
     "adj_type": "owner_in", "account_id": "3200"},
    {"id": "ADJ3", "adj_date": _d("2026-02-15"), "amount": 5000,
     "adj_type": "owner_out", "account_id": "3200"},
    {"id": "ADJ4", "adj_date": _d("2026-03-10"), "amount": 2000,
     "adj_type": "correction", "account_id": "3900"},
]

BANKS = [
    {"id": "A", "name": "主帳戶", "opening_balance": 100000,
     "opening_date": _d("2026-01-01"), "acct_kind": "bank", "active": True},
    {"id": "B", "name": "零用金", "opening_balance": 0,
     "opening_date": _d("2026-01-01"), "acct_kind": "cash", "active": True},
]

_PNL_KW = dict(invoices=INVOICES, payments=PAYMENTS, cash_entries=CASH,
               equipment=EQUIPMENT, advance_expenses=ADV_EXPENSES,
               cat_map=CAT_MAP, accounts=ACCOUNTS)


def _group(pnl, side, name):
    return next(g for g in pnl[side]["groups"] if g["group"] == name)


def _line(group, label):
    return next((ln["amount"] for ln in group["lines"] if ln["label"] == label), 0)


# ═══ 期間解析 ═══════════════════════════════════════════════

class TestPeriodMonths:
    def test_single_month(self):
        assert period_months("2026-06") == ["2026-06"]

    def test_quarter(self):
        assert period_months("2026-Q2") == ["2026-04", "2026-05", "2026-06"]
        assert period_months("2026-q4") == ["2026-10", "2026-11", "2026-12"]

    def test_year(self):
        months = period_months("2026")
        assert len(months) == 12
        assert months[0] == "2026-01" and months[-1] == "2026-12"

    def test_range_cross_year(self):
        months = period_months("2025-07..2026-06")
        assert len(months) == 12
        assert months[0] == "2025-07" and months[-1] == "2026-06"

    @pytest.mark.parametrize("bad", [
        "", "garbage", "2026-13", "2026-Q5", "2026/06",
        "2026-06..2025-01",           # 起訖反轉
        "2000-01..2200-01",           # 超過 120 個月上限
    ])
    def test_invalid_raises(self, bad):
        with pytest.raises(ValueError):
            period_months(bad)

    def test_shift_and_range(self):
        assert shift_month("2026-01", -1) == "2025-12"
        assert shift_month("2025-12", 1) == "2026-01"
        assert month_range("2026-01", "2026-03") == Q1


# ═══ 折舊 ═══════════════════════════════════════════════════

class TestDepreciation:
    def test_before_purchase_is_zero(self):
        assert depreciation_for_month(EQUIPMENT[0], "2025-11") == 0

    def test_purchase_month_charges(self):
        assert depreciation_for_month(EQUIPMENT[0], "2025-12") == 1000

    def test_after_schedule_is_zero(self):
        assert depreciation_for_month(EQUIPMENT[0], "2028-12") == 0  # 36攤到2028-11

    def test_remainder_on_last_month(self):
        eq = EQUIPMENT[2]  # 1000/3 → 333, 333, 334
        assert depreciation_for_month(eq, "2026-01") == 333
        assert depreciation_for_month(eq, "2026-02") == 333
        assert depreciation_for_month(eq, "2026-03") == 334

    def test_retired_month_stops(self):
        eq = EQUIPMENT[1]  # 2026-03 除役 → 當月起不折
        assert depreciation_for_month(eq, "2026-02") == 1000
        assert depreciation_for_month(eq, "2026-03") == 0

    def test_accumulated_sums_to_cost(self):
        assert accumulated_depreciation(EQUIPMENT[2], "2026-03") == 1000
        assert accumulated_depreciation(EQUIPMENT[2], "2030-01") == 1000  # 攤滿停

    def test_net_rows_exclude_retired_and_future(self):
        rows = equipment_net_rows(EQUIPMENT, "2026-03")
        labels = [r["label"] for r in rows["lines"]]
        assert "燈" not in labels          # 除役出表
        assert rows["net_total"] == 32000  # EQ1 36000−4000；EQ3 淨值 0
        rows_jan = equipment_net_rows(EQUIPMENT, "2026-01")
        assert "燈" not in [r["label"] for r in rows_jan["lines"]]  # 尚未購入


# ═══ 發票小工具 / 營業稅 ═══════════════════════════════════

class TestInvoiceHelpers:
    def test_ex_tax_fallback(self):
        assert invoice_ex_tax(INVOICES[0]) == 100000
        assert invoice_ex_tax(INVOICES[1]) == 200000  # round(210000/1.05)

    def test_tax_fallback(self):
        assert invoice_tax(INVOICES[1]) == 10000
        assert invoice_tax({"amount_total": 105000, "amount_ex_tax": 100000}) == 5000
        assert invoice_tax({"amount_total": 105000}) == 5000

    def test_vat_position_q1(self):
        v = vat_position(INVOICES, CASH, CAT_MAP, Q1)
        assert v == {"output": 17500, "input": 1000, "paid": 5250, "net": 11250}

    def test_classify_accepts_dict_map_values(self):
        """階段三 cat_map 值改 dict 後 classify 仍正確（回歸）。"""
        assert classify_cash_entry({"category": "房租"}, CAT_MAP) == "direct_expense"
        assert classify_cash_entry({"category": "轉存"}, CAT_MAP) == "transfer"

    def test_month_of_tz_representation_invariant(self):
        """同一瞬間的 +08 與 UTC 表示必須落同一個月（timestamptz 回讀回歸）。

        使用者填 2026-02-01（台灣本地）→ DB 回讀 2026-01-31T16:00Z；
        修復前直接取 .month 會把月初資料歸到前一個月。
        """
        from datetime import timedelta, timezone
        from core.finance_logic import month_of
        tw = timezone(timedelta(hours=8))
        local_dt = datetime(2026, 2, 1, 0, 0, tzinfo=tw)
        utc_dt = local_dt.astimezone(timezone.utc)  # 2026-01-31T16:00Z
        assert month_of(utc_dt) == month_of(local_dt)


# ═══ 損益表黃金數字 ═════════════════════════════════════════

@pytest.fixture(scope="module")
def pnl():
    return build_pnl(Q1, **_PNL_KW)


class TestBuildPnlGolden:
    def test_revenue(self, pnl):
        # INV1 100000 + INV2 200000（INV4 作廢、INV5 付款、INV3 代開不計）
        assert pnl["revenue"]["total"] == 300000
        assert pnl["revenue"]["by_collection"] == {
            "collected": 100000, "receivable": 200000, "cash": 0}
        assert pnl["revenue"]["lines"] == [
            {"key": "invoiced", "label": "開立發票營收", "amount": 300000}]

    def test_cost_groups(self, pnl):
        assert _line(_group(pnl, "cost", "營業成本-料"), "設備耗材") == 3000
        assert _line(_group(pnl, "cost", "營業成本-工"), "外包成本") == 50000
        fee_g = _group(pnl, "cost", "營業成本-費")
        # 折舊：Jan 1000+333、Feb 1000+1000+333、Mar 1000+0+334 = 5000
        assert _line(fee_g, "折舊費用") == 5000
        assert _line(fee_g, "預支核銷支出") == 38000
        assert pnl["cost"]["total"] == 96000

    def test_gross(self, pnl):
        assert pnl["gross"] == {"amount": 204000, "rate": 68.0}

    def test_opex(self, pnl):
        mgmt = _group(pnl, "opex", "營業費用-管理")
        assert _line(mgmt, "房租") == 12000
        assert _line(mgmt, "行政") == 800          # C13 未掛帳戶損益照計
        assert _line(mgmt, "未歸類支出") == 1000    # C12
        assert _line(mgmt, "銀行手續費") == 30      # C4 匯費
        assert pnl["opex"]["total"] == 13830

    def test_operating(self, pnl):
        assert pnl["operating"]["amount"] == 190170
        assert pnl["operating"]["rate"] == 63.39
        assert pnl["operating"]["expense_rate"] == 4.61

    def test_non_operating(self, pnl):
        assert pnl["non_operating"]["total"] == 1500
        inc = {x["label"]: x["amount"] for x in pnl["non_operating"]["income"]}
        assert inc == {"代開手續費收入": 1000, "利息收入": 500}
        assert pnl["non_operating"]["expense"] == []

    def test_tax_and_net(self, pnl):
        assert pnl["pretax"] == 191670
        assert pnl["tax"]["income_tax"] == 7000
        assert pnl["tax"]["vat_info"] == {
            "output": 17500, "input": 1000, "paid": 5250, "net": 11250}
        assert pnl["net"]["amount"] == 184670
        assert pnl["net"]["rate"] == 61.56

    def test_monthly_avg(self, pnl):
        assert pnl["monthly_avg"] == {"revenue": 100000, "cost": 32000,
                                      "opex": 4610}

    def test_settlement_entries_not_double_counted(self, pnl):
        """C1/C2（AR/AP 結清）與 C6-C9（預支/轉存）不得再進損益。"""
        all_labels = [ln["label"] for side in ("cost", "opex")
                      for g in pnl[side]["groups"] for ln in g["lines"]]
        # 若重複計，外包成本會變 70000/管理費會多出 105000 級數字
        assert _line(_group(pnl, "cost", "營業成本-工"), "外包成本") == 50000
        assert "INV1 收款" not in all_labels

    def test_merge_pnl_equals_full_build(self):
        parts = [build_pnl([m], **_PNL_KW) for m in Q1]
        assert merge_pnl(parts, 3) == build_pnl(Q1, **_PNL_KW)


# ═══ 資產負債表黃金數字 ═════════════════════════════════════

def _bs_golden():
    bank_lines = bank_balances_asof(BANKS, CASH, "2026-03")
    ar = ar_open_invoices(INVOICES, "2026-01", "2026-03")
    ap = ap_open_payments(PAYMENTS, CAT_MAP, "2026-01", "2026-03")
    vat = vat_position(INVOICES, CASH, CAT_MAP, Q1)
    pnl = build_pnl(Q1, **_PNL_KW)
    warn = statement_warnings(CASH, PAYMENTS, CAT_MAP, Q1)
    return build_balance_sheet(
        "2026-03", bank_lines=bank_lines,
        receivable_total=sum(i["amount_total"] for i in ar),
        advance_balance=1000,  # PAY3：40000−38000−1000（未結清）
        equipment=EQUIPMENT, adjustments=ADJUSTMENTS,
        payable_total=sum(p["amount"] for p in ap),
        vat_payable=vat["net"], cumulative_net=pnl["net"]["amount"],
        note_counts=warn)


@pytest.fixture(scope="module")
def bs():
    return _bs_golden()


class TestBalanceSheetGolden:
    def test_bank_balances(self):
        lines = {b["name"]: b["amount"]
                 for b in bank_balances_asof(BANKS, CASH, "2026-03")}
        # A：100000 +105000−20000−12000−3030+500−40000+1000−50000−5250−7000−1000
        assert lines == {"主帳戶": 68220, "零用金": 50000}

    def test_ar_ap_selection(self):
        ar = ar_open_invoices(INVOICES, "2026-01", "2026-03")
        assert [i["id"] for i in ar] == ["INV2"]  # 代開/已收/作廢/付款皆不計
        ap = ap_open_payments(PAYMENTS, CAT_MAP, "2026-01", "2026-03")
        assert [p["id"] for p in ap] == ["PAY2"]  # 已付/預支/零用金(transfer)不計

    def test_assets(self, bs):
        amounts = {x["key"]: x["amount"] for x in bs["assets"]["current"]}
        assert amounts == {"cash:A": 68220, "cash:B": 50000,
                           "receivable": 210000, "advance": 1000}
        assert bs["assets"]["noncurrent"] == [
            {"key": "equipment", "label": "器材淨值", "amount": 32000,
             "pct": bs["assets"]["noncurrent"][0]["pct"]}]
        assert bs["assets"]["total"] == 361220

    def test_liabilities(self, bs):
        amounts = {x["key"]: x["amount"] for x in bs["liabilities"]["current"]}
        assert amounts == {"payable": 30000, "vat_payable": 11250}
        assert bs["liabilities"]["noncurrent"] == []
        assert bs["liabilities"]["total"] == 41250

    def test_equity(self, bs):
        amounts = {x["key"]: x["amount"] for x in bs["equity"]["lines"]}
        assert amounts == {"opening": 50000, "owner": 5000,
                           "retained": 184670, "adjustments": 2000}
        assert bs["equity"]["total"] == 241670

    def test_check_diff_exposed(self, bs):
        # 361220 − 41250 − 241670 = 78300（合成資料含未入帳器材等 — 誠實外顯）
        assert bs["check"]["diff"] == 78300
        assert bs["check"]["notes"]  # 附原因清單
        assert any("未歸類" in n for n in bs["check"]["notes"])

    def test_ratios(self, bs):
        assert bs["ratios"]["current_ratio"] == round(329220 / 41250, 2)
        assert bs["ratios"]["debt_ratio"] == round(41250 * 100 / 361220, 2)
        assert "65" in bs["ratios"]["labels"]["debt_ratio"]  # <65% 門檻文案

    def test_pct_denominator_is_assets_total(self, bs):
        assert bs["assets"]["current"][0]["pct"] == round(68220 * 100 / 361220, 2)
        assert bs["liabilities"]["current"][0]["pct"] == round(30000 * 100 / 361220, 2)
        assert bs["equity"]["lines"][2]["pct"] == round(184670 * 100 / 361220, 2)


# ═══ 現金流量表黃金數字 ═════════════════════════════════════
# _cf_side 直接 import services.finance_statements（不再本地拷貝一份）

def _cf_golden(months):
    opening = _cf_side(bank_balances_asof(BANKS, CASH, shift_month(months[0], -1)))
    closing = _cf_side(bank_balances_asof(BANKS, CASH, months[-1]))
    return build_cashflow(months, opening=opening, closing=closing,
                          cash_entries=CASH, cat_map=CAT_MAP, accounts=ACCOUNTS)


@pytest.fixture(scope="module")
def cf():
    return _cf_golden(Q1)


class TestCashflowGolden:
    def test_opening_closing(self, cf):
        assert cf["opening"]["total"] == 100000
        assert cf["closing"]["total"] == 118220

    def test_activities(self, cf):
        # operating：C1+105000 C2−20000 C3−12000 C4−3030 C5+500 C10−5250
        #            C11−7000 C12−1000（轉存/預支不列；C13 未掛帳戶排除）
        assert cf["operating"] == 57220
        assert cf["investing"] == 0
        assert cf["financing"] == 0
        assert cf["net"] == 57220

    def test_check_diff_equals_advance_net(self, cf):
        # 期末 118220 −（期初 100000 + 淨流 57220）= −39000 = 預支往來淨流
        assert cf["check"]["diff"] == -39000
        assert any("預支" in n for n in cf["check"]["notes"])
        assert any("未掛帳戶" in n for n in cf["check"]["notes"])
        # 轉存成對 → 不出未成對 note
        assert not any("轉存" in n for n in cf["check"]["notes"])

    def test_activity_classifier(self):
        assert cash_entry_activity(CASH[0], CAT_MAP, ACCOUNTS) == "operating"
        assert cash_entry_activity(CASH[5], CAT_MAP, ACCOUNTS) is None  # advance
        assert cash_entry_activity(CASH[7], CAT_MAP, ACCOUNTS) is None  # transfer

    def test_merge_cf_matches_full_period(self, cf):
        parts = [_cf_golden([m]) for m in Q1]
        merged = merge_cf(parts, cf["opening"], cf["closing"])
        for k in ("operating", "investing", "financing", "net"):
            assert merged[k] == cf[k]
        assert merged["check"]["diff"] == cf["check"]["diff"]


# ═══ drill 欄位（單一來源 VALID_DRILL_KINDS）════════════════

class TestDrillFields:
    """報表行 drill 欄位：值必屬 VALID_DRILL_KINDS（後端 drilldown 的合法
    kind 集合），且只掛在規格指定的行 — PnL revenue 總行與 cost/opex group
    行、BS 應收/應付、CF 三活動。前端只讀這些欄位，不自拼 kind 字串。"""

    def test_pnl_drills(self, pnl):
        assert pnl["revenue"]["drill"] == "revenue"
        assert [g["drill"] for g in pnl["cost"]["groups"]] == [
            "cost.料", "cost.工", "cost.費"]
        assert [g["drill"] for g in pnl["opex"]["groups"]] == [
            "opex.銷售", "opex.管理", "opex.研發"]
        # 收入明細行不帶 drill（invoiced/cash 不是合法 kind）
        assert all("drill" not in ln for ln in pnl["revenue"]["lines"])

    def test_bs_drills_only_receivable_payable(self, bs):
        assert [(x["key"], x["drill"]) for x in bs["assets"]["current"]
                if x.get("drill")] == [("receivable", "receivable")]
        assert [(x["key"], x["drill"]) for x in bs["liabilities"]["current"]
                if x.get("drill")] == [("payable", "payable")]
        assert all("drill" not in x for x in
                   bs["assets"]["noncurrent"] + bs["equity"]["lines"])

    def test_cf_drills(self, cf):
        assert cf["drills"] == {"operating": "cash.operating",
                                "investing": "cash.investing",
                                "financing": "cash.financing"}

    def test_all_drill_values_are_valid_kinds(self, pnl, bs, cf):
        drills = [pnl["revenue"]["drill"]]
        drills += [g["drill"] for g in pnl["cost"]["groups"] + pnl["opex"]["groups"]]
        drills += [x["drill"] for x in (bs["assets"]["current"]
                                        + bs["liabilities"]["current"])
                   if x.get("drill")]
        drills += list(cf["drills"].values())
        assert drills and set(drills) <= set(VALID_DRILL_KINDS)


# ═══ 警示 + 白話解讀 ════════════════════════════════════════

class TestWarningsAndInterpretation:
    def test_warnings_counts(self):
        w = statement_warnings(CASH, PAYMENTS, CAT_MAP, Q1)
        assert w["unmapped"] == 1     # C12 幽靈類別
        assert w["unassigned"] == 1   # C13
        assert w["undated"] == 0
        assert len(w["messages"]) == 2

    def test_undated_counted(self):
        w = statement_warnings([{"id": "X", "entry_date": None, "expense": 1}],
                               [], CAT_MAP, Q1)
        assert w["undated"] == 1

    def test_interpretation_sentences(self):
        pnl = build_pnl(Q1, **_PNL_KW)
        bs = _bs_golden()
        cf = _cf_golden(Q1)
        out = statement_interpretation(pnl, bs, cf, ar_over_60=210000)
        assert isinstance(out, list) and 5 <= len(out) <= 8
        text = "\n".join(out)
        assert "毛利率 68.0%" in text
        assert "60 天" in text and "210,000" in text
        assert "營運現金流 +57,220" in text
        assert "檢核差額" in text

    def test_interpretation_skips_when_no_data(self):
        empty_pnl = build_pnl(Q1, cat_map=CAT_MAP, accounts=ACCOUNTS)
        out = statement_interpretation(empty_pnl, None, None)
        assert out == []  # 資料不足的句子不出


# ═══ 恆等式（乾淨資料）══════════════════════════════════════

class TestIdentitiesCleanBooks:
    """帳務完整（無預支/器材未入帳/未歸類/未掛帳戶）時，BS diff 與 CF diff
    必須為 0 — 引擎自身不得製造差額。"""

    INV = [{"id": "I1", "payment_type": "收款", "issue_status": "已開立",
            "payment_status": "未收款", "category": "專案",
            "amount_total": 105000, "amount_ex_tax": 100000, "tax_amount": 5000,
            "commission": None, "invoice_date": _d("2026-01-10"), "paid_date": None}]
    PAY = [{"id": "P1", "is_advance": 0, "request_date": _d("2026-02-05"),
            "amount": 20000, "category": "專案外包", "summary": "外包",
            "payee_name": "王", "payment_status": "未付款", "payment_date": None}]
    ENTRIES = [{"id": "K1", "entry_date": _d("2026-02-20"), "expense": 10000,
                "category": "房租", "bank_account_id": "A", "summary": "房租"}]
    ADJ = [{"id": "J1", "adj_date": _d("2026-01-01"), "amount": 100000,
            "adj_type": "opening", "account_id": "3100"}]
    BANK = [{"id": "A", "name": "主帳戶", "opening_balance": 100000,
             "opening_date": _d("2026-01-01"), "acct_kind": "bank",
             "active": True}]

    def test_bs_diff_zero(self):
        pnl = build_pnl(Q1, invoices=self.INV, payments=self.PAY,
                        cash_entries=self.ENTRIES, cat_map=CAT_MAP,
                        accounts=ACCOUNTS)
        assert pnl["net"]["amount"] == 70000  # 100000 − 20000 − 10000
        vat = vat_position(self.INV, self.ENTRIES, CAT_MAP, Q1)
        bs = build_balance_sheet(
            "2026-03",
            bank_lines=bank_balances_asof(self.BANK, self.ENTRIES, "2026-03"),
            receivable_total=sum(i["amount_total"] for i in ar_open_invoices(
                self.INV, "2026-01", "2026-03")),
            advance_balance=0, equipment=(), adjustments=self.ADJ,
            payable_total=sum(p["amount"] for p in ap_open_payments(
                self.PAY, CAT_MAP, "2026-01", "2026-03")),
            vat_payable=vat["net"], cumulative_net=pnl["net"]["amount"],
            note_counts=None)
        assert bs["assets"]["total"] == 195000   # 現金 90000 + AR 105000
        assert bs["liabilities"]["total"] == 25000  # AP 20000 + VAT 5000
        assert bs["equity"]["total"] == 170000   # 期初 100000 + 累積 70000
        assert bs["check"] == {"diff": 0, "notes": []}

    def test_cf_identity_zero(self):
        opening = _cf_side(bank_balances_asof(self.BANK, self.ENTRIES, "2025-12"))
        closing = _cf_side(bank_balances_asof(self.BANK, self.ENTRIES, "2026-03"))
        cf = build_cashflow(Q1, opening=opening, closing=closing,
                            cash_entries=self.ENTRIES, cat_map=CAT_MAP,
                            accounts=ACCOUNTS)
        assert cf["opening"]["total"] == 100000
        assert cf["closing"]["total"] == 90000
        assert cf["net"] == -10000
        assert cf["check"] == {"diff": 0, "notes": []}
