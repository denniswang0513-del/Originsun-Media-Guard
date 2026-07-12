"""財務階段五 儀表板純函式黃金測試（core/finance_logic.py）。

aging_buckets / client_concentration / runway_months — 邊界值手算寫死斷言：
帳齡跨 current/d1_30/d31_60/d61_90/over90 桶邊界 + total；集中度含 total=0、
warn>50%、pct 加總=100、desc 排序；跑道 不燒錢→None、燒錢→round1、cash≤0→0.0。
"""
from datetime import datetime, timezone

from core.crm_logic import project_margin
from core.finance_logic import (aging_buckets, client_concentration,
                                invoice_vat_side, resolve_client_name,
                                runway_months)


class TestAgingBuckets:
    """基準日 = as_of 月底。2026-06 → 2026-06-30。"""

    def test_bucket_boundaries_and_total(self):
        invoices = [
            {"invoice_date": datetime(2026, 6, 30), "amount_total": 1000},   # 0 天  → current
            {"invoice_date": datetime(2026, 6, 29), "amount_total": 2000},   # 1 天  → d1_30
            {"invoice_date": datetime(2026, 5, 31), "amount_total": 4000},   # 30 天 → d1_30（上界）
            {"invoice_date": datetime(2026, 5, 30), "amount_total": 8000},   # 31 天 → d31_60
            {"invoice_date": datetime(2026, 4, 30), "amount_total": 16000},  # 61 天 → d61_90
            {"invoice_date": datetime(2026, 3, 31), "amount_total": 32000},  # 91 天 → over90
        ]
        r = aging_buckets(invoices, "2026-06")
        assert [b["key"] for b in r["buckets"]] == [
            "current", "d1_30", "d31_60", "d61_90", "over90"]
        assert [b["label"] for b in r["buckets"]] == [
            "未逾期", "1-30 天", "31-60 天", "61-90 天", "90 天以上"]
        by = {b["key"]: b for b in r["buckets"]}
        assert by["current"] == {"key": "current", "label": "未逾期",
                                 "amount": 1000, "count": 1}
        assert by["d1_30"]["amount"] == 6000 and by["d1_30"]["count"] == 2
        assert by["d31_60"]["amount"] == 8000 and by["d31_60"]["count"] == 1
        assert by["d61_90"]["amount"] == 16000
        assert by["over90"]["amount"] == 32000
        assert r["total"] == 63000

    def test_future_invoice_is_current(self):
        # 開票日晚於 as_of 月底（負天數）仍歸 current
        r = aging_buckets([{"invoice_date": datetime(2026, 7, 15),
                            "amount_total": 500}], "2026-06")
        by = {b["key"]: b for b in r["buckets"]}
        assert by["current"]["amount"] == 500
        assert r["total"] == 500

    def test_string_and_tzaware_dates(self):
        # str 與 tz-aware datetime 都能解析（tz-aware 過 local_day 修時區）
        invoices = [
            {"invoice_date": "2026-01-15", "amount_total": 700},              # over90
            {"invoice_date": datetime(2026, 6, 20, tzinfo=timezone.utc),
             "amount_total": 300},                                            # 10 天 → d1_30
        ]
        r = aging_buckets(invoices, "2026-06")
        by = {b["key"]: b for b in r["buckets"]}
        assert by["over90"]["amount"] == 700
        assert by["d1_30"]["amount"] == 300
        assert r["total"] == 1000

    def test_empty_and_undated(self):
        r = aging_buckets([], "2026-06")
        assert len(r["buckets"]) == 5
        assert r["total"] == 0
        assert all(b["amount"] == 0 and b["count"] == 0 for b in r["buckets"])
        # 無法解析日期的列不計、也不進 total
        r2 = aging_buckets([{"invoice_date": None, "amount_total": 999}], "2026-06")
        assert r2["total"] == 0


class TestClientConcentration:
    def test_desc_order_and_pct_sum_100(self):
        r = client_concentration({"A": 20000, "B": 50000, "C": 30000})
        assert [c["name"] for c in r["clients"]] == ["B", "C", "A"]
        assert [c["pct"] for c in r["clients"]] == [50.0, 30.0, 20.0]
        assert round(sum(c["pct"] for c in r["clients"]), 1) == 100.0

    def test_top3_default_and_warn_true(self):
        r = client_concentration({"A": 40000, "B": 30000, "C": 20000, "D": 10000})
        assert r["top_n"] == 3            # 前三 = 40+30+20 = 90k / 100k
        assert r["top_n_pct"] == 90.0
        assert r["warn"] is True

    def test_top1_warn_true(self):
        r = client_concentration({"A": 60000, "B": 25000, "C": 15000}, top=1)
        assert r["top_n"] == 1
        assert r["top_n_pct"] == 60.0
        assert r["warn"] is True

    def test_warn_false_when_not_concentrated(self):
        r = client_concentration({"A": 25000, "B": 25000, "C": 25000, "D": 25000},
                                 top=1)
        assert r["top_n_pct"] == 25.0
        assert r["warn"] is False

    def test_total_zero(self):
        r = client_concentration({"A": 0, "B": 0})
        assert all(c["pct"] == 0.0 for c in r["clients"])
        assert r["top_n_pct"] == 0.0
        assert r["warn"] is False

    def test_empty(self):
        r = client_concentration({})
        assert r["clients"] == []
        assert r["top_n_pct"] == 0.0
        assert r["warn"] is False


class TestRunwayMonths:
    def test_not_burning_returns_none(self):
        assert runway_months(100000, 0) is None       # 打平不燒錢
        assert runway_months(100000, 5000) is None     # 賺錢
        assert runway_months(0, None) is None          # avg None 視同 0

    def test_burning_rounds_1dp(self):
        assert runway_months(100000, -20000) == 5.0
        assert runway_months(100000, -30000) == 3.3    # round(3.333.., 1)

    def test_cash_zero_or_negative(self):
        assert runway_months(0, -20000) == 0.0
        assert runway_months(-500, -20000) == 0.0


class TestInvoiceVatSide:
    """/simplify A：銷項/進項判定單一謂詞，tax_package 明細與 vat_position 摘要共用。"""

    def test_output_input_and_void(self):
        assert invoice_vat_side({"payment_type": "收款"}) == "output"
        assert invoice_vat_side({"payment_type": "付款"}) == "input"
        assert invoice_vat_side({}) == "output"                       # 缺值視同收款→銷項
        assert invoice_vat_side({"issue_status": "作廢",
                                 "payment_type": "收款"}) is None     # 作廢不計

    def test_third_payment_type_goes_output(self):
        # 第三種 payment_type 值 → 銷項（與 vat_position 的 else 分支一致，兩處不漂）
        assert invoice_vat_side({"payment_type": "折讓"}) == "output"


class TestResolveClientName:
    """/simplify D：客戶歸戶 fallback 鏈單一來源。"""

    def test_project_then_company_then_fallback(self):
        pc = {"P1": "觀傳局", "P2": "  "}
        assert resolve_client_name({"project_id": "P1"}, pc) == "觀傳局"
        # project 對映空白 → 退抬頭
        assert resolve_client_name(
            {"project_id": "P2", "company_name": "某公司"}, pc) == "某公司"
        # project 不在對映表 → 退抬頭
        assert resolve_client_name(
            {"project_id": "X", "company_name": "外部客戶"}, pc) == "外部客戶"
        # 無 project、無抬頭 → 未指定
        assert resolve_client_name({}, pc) == "未指定"


class TestProjectMargin:
    """/simplify B：專案毛利公式單一來源（costs.py 摘要 + 儀表板 Top/Bottom 共用）。"""

    def test_basic_margin_and_pct(self):
        m = project_margin(105000, 5, 30000, 20000)   # ex_tax=100000, cost=50000
        assert m == {"ex_tax": 100000, "cost": 50000,
                     "margin": 50000, "margin_pct": 50.0}

    def test_tax_rate_default_5(self):
        assert project_margin(105000, None, 0, 0)["ex_tax"] == 100000

    def test_zero_revenue_pct_none(self):
        m = project_margin(0, 5, 1000, 0)
        assert m["ex_tax"] == 0 and m["margin"] == -1000 and m["margin_pct"] is None

    def test_matches_legacy_costs_formula(self):
        # 對齊 costs.py 舊式：round(contract/(1+tax/100)) − (exp+staff)
        assert project_margin(210000, 5, 50000, 50000) == {
            "ex_tax": 200000, "cost": 100000, "margin": 100000, "margin_pct": 50.0}
