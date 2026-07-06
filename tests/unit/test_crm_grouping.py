"""應付/應收帳款分組聚合（core/crm_logic.py group_payables / group_receivables）。

rows 用 SimpleNamespace 模擬 ORM row tuple — 純函式不碰 DB。
"""
from datetime import datetime, timezone
from types import SimpleNamespace as NS

from core.crm_logic import group_payables, group_receivables


def _payment(id, payee="王士源", amount=1000, payee_id="", status="應付款",
             req_date=None, pay_date=None, planned=""):
    return NS(id=id, payee_name=payee, amount=amount, payee_id=payee_id,
              payment_status=status, request_date=req_date, payment_date=pay_date,
              planned_month=planned, summary=f"s{id}", category="外包")


def _invoice(id, company="甲公司", total=50000, tax_id="", status="未收款",
             inv_date=None, title=""):
    return NS(id=id, company_name=company, amount_total=total, tax_id=tax_id,
              payment_status=status, invoice_date=inv_date, title=title,
              invoice_number=f"AB-{id}", category="製作費")


class TestGroupPayables:
    def test_groups_by_payee_and_sums(self):
        rows = [
            (_payment(1, "王士源", 10000), "A123", "台新", "111"),
            (_payment(2, "王士源", 5000), "A123", "台新", "111"),
            (_payment(3, "李小美", 8000), "B456", "國泰", "222"),
        ]
        r = group_payables(rows)
        assert r["grand_total"] == 23000
        assert [p["payee_name"] for p in r["payees"]] == ["王士源", "李小美"]  # 金額降冪
        wang = r["payees"][0]
        assert wang["total_amount"] == 15000 and len(wang["items"]) == 2
        assert wang["bank_name"] == "台新" and wang["bank_account"] == "111"

    def test_dedup_by_payment_id(self):
        """outerjoin 同名 staff 多列 → 同一筆請款重複出現，只能算一次。"""
        p = _payment(1, amount=10000)
        rows = [(p, "A1", "台新", "111"), (p, "A2", "玉山", "999")]
        r = group_payables(rows)
        assert r["grand_total"] == 10000
        assert len(r["payees"][0]["items"]) == 1

    def test_payee_id_prefers_payment_over_staff(self):
        rows = [(_payment(1, payee_id="FROM_PAYMENT"), "FROM_STAFF", "", "")]
        assert group_payables(rows)["payees"][0]["payee_id"] == "FROM_PAYMENT"
        rows2 = [(_payment(2, payee_id=""), "FROM_STAFF", "", "")]
        assert group_payables(rows2)["payees"][0]["payee_id"] == "FROM_STAFF"

    def test_empty_payee_name_becomes_unspecified(self):
        rows = [(_payment(1, payee=""), None, None, None)]
        assert group_payables(rows)["payees"][0]["payee_name"] == "未指定"

    def test_date_formats(self):
        d = datetime(2026, 7, 6, tzinfo=timezone.utc)
        rows = [(_payment(1, req_date=d, pay_date=d, planned="2026-08"), "", "", "")]
        item = group_payables(rows)["payees"][0]["items"][0]
        assert item["date"] == "2026/07/06"          # request_date 用斜線
        assert item["payment_date"] == "2026-07-06"  # payment_date 用連字號（前端月份調整靠這格式）
        assert item["planned_month"] == "2026-08"

    def test_empty_rows(self):
        r = group_payables([])
        assert r == {"payees": [], "grand_total": 0}

    def test_none_amount_counts_as_zero(self):
        rows = [(_payment(1, amount=None), "", "", "")]
        r = group_payables(rows)
        assert r["grand_total"] == 0 and r["payees"][0]["items"][0]["amount"] == 0


class TestGroupReceivables:
    NOW = datetime(2026, 7, 6, tzinfo=timezone.utc)

    def test_groups_by_client_and_days_overdue(self):
        rows = [
            (_invoice(1, "甲公司", 100000, inv_date=datetime(2026, 6, 6, tzinfo=timezone.utc)),
             "案A", "12345678", "匯款資訊", "備註"),
            (_invoice(2, "乙公司", 200000), "案B", None, None, None),
        ]
        r = group_receivables(rows, self.NOW)
        assert r["grand_total"] == 300000
        assert [c["company_name"] for c in r["clients"]] == ["乙公司", "甲公司"]  # 金額降冪
        jia = r["clients"][1]
        assert jia["items"][0]["days_since_issued"] == 30
        assert jia["payment_info"] == "匯款資訊"

    def test_tax_id_prefers_invoice_over_client(self):
        rows = [(_invoice(1, tax_id="INV_TAX"), "", "CLIENT_TAX", "", "")]
        assert group_receivables(rows, self.NOW)["clients"][0]["tax_id"] == "INV_TAX"
        rows2 = [(_invoice(2, tax_id=""), "", "CLIENT_TAX", "", "")]
        assert group_receivables(rows2, self.NOW)["clients"][0]["tax_id"] == "CLIENT_TAX"

    def test_dedup_by_invoice_id(self):
        inv = _invoice(1, total=100000)
        rows = [(inv, "案A", "", "", ""), (inv, "案A", "", "", "")]
        r = group_receivables(rows, self.NOW)
        assert r["grand_total"] == 100000 and len(r["clients"][0]["items"]) == 1

    def test_no_invoice_date_means_zero_days(self):
        rows = [(_invoice(1, inv_date=None), "", "", "", "")]
        assert group_receivables(rows, self.NOW)["clients"][0]["items"][0]["days_since_issued"] == 0

    def test_empty_rows(self):
        assert group_receivables([], self.NOW) == {"clients": [], "grand_total": 0}
