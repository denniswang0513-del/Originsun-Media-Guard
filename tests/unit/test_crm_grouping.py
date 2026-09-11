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


def _staff(id="st-1", name="王士源", id_number="A123", bank_name="台新", bank_account="111"):
    """`core.staff_alias` 對到的人員列（同形物件）。"""
    return NS(id=id, name=name, id_number=id_number, bank_name=bank_name, bank_account=bank_account)


class TestGroupPayables:
    """rows 的形狀是 `(payment, staff_or_None)` —— 對到人就給人員列，對不到給 None。"""

    def test_groups_by_person_and_sums(self):
        wang, lee = _staff(), _staff("st-2", "李小美", "B456", "國泰", "222")
        rows = [
            (_payment(1, "王士源", 10000), wang),
            (_payment(2, "王士源", 5000), wang),
            (_payment(3, "李小美", 8000), lee),
        ]
        r = group_payables(rows)
        assert r["grand_total"] == 23000
        assert [p["payee_name"] for p in r["payees"]] == ["王士源", "李小美"]  # 金額降冪
        w = r["payees"][0]
        assert w["total_amount"] == 15000 and len(w["items"]) == 2
        assert w["bank_name"] == "台新" and w["bank_account"] == "111"
        assert w["person_id"] == "st-1"

    def test_different_spellings_of_one_person_merge_into_one_group(self):
        """🔴 這支功能存在的理由：「停車費 史丹」「早餐 史丹」「停車 史丹」是同一個人，
        原本是三列、要匯三次。群組名用正式姓名，每一列留著當初打的字。"""
        stan = _staff("st-9", "鄭雲全", "K1", "王道(048)", "01000046215888")
        rows = [(_payment(1, "停車費 史丹", 300), stan),
                (_payment(2, "早餐 史丹", 120), stan),
                (_payment(3, "停車 史丹", 200), stan)]
        r = group_payables(rows)
        assert len(r["payees"]) == 1
        pg = r["payees"][0]
        assert pg["payee_name"] == "鄭雲全" and pg["total_amount"] == 620
        assert pg["aliases"] == ["停車費 史丹", "早餐 史丹", "停車 史丹"]
        assert [it["payee_name"] for it in pg["items"]] == ["停車費 史丹", "早餐 史丹", "停車 史丹"]
        assert pg["bank_code"] == "048" and pg["bank_account"] == "01000046215888"

    def test_unresolved_strings_stay_separate_and_verbatim(self):
        """對不到人的不猜：照原字串各自一組、沒有銀行資料。"""
        rows = [(_payment(1, "某外部廠商", 1000), None),
                (_payment(2, "另一個外部", 2000), None)]
        r = group_payables(rows)
        assert [p["payee_name"] for p in r["payees"]] == ["另一個外部", "某外部廠商"]
        assert all(p["person_id"] == "" and p["bank_account"] == "" for p in r["payees"])

    def test_bank_code_and_display_come_from_the_one_table(self):
        """人打什麼寫法都行，翻譯只有 core.bank_codes 一份（不准在這裡切字串）。"""
        rows = [(_payment(1), _staff(bank_name="中信（822)", bank_account="342168993550"))]
        pg = group_payables(rows)["payees"][0]
        assert pg["bank_code"] == "822" and pg["bank_display"] == "中國信託商業銀行"
        assert pg["bank_name"] == "中信（822)", "人打的原字串要留著"

    def test_unknown_bank_passes_through_instead_of_going_blank(self):
        rows = [(_payment(1), _staff(bank_name="某某農會信用部", bank_account="123"))]
        pg = group_payables(rows)["payees"][0]
        assert pg["bank_code"] == "" and pg["bank_display"] == "某某農會信用部"

    def test_items_carry_their_payout_binding(self):
        """面板要標出「這一筆已經在哪張匯款通知上」，才不會再產一張。"""
        p = _payment(1)
        p.payout_id = "po-abc"
        w = group_payables([(p, None)])["payees"][0]
        assert w["items"][0]["payout_id"] == "po-abc"

    def test_no_month_level_totals_here(self):
        """「本次要匯」是**月 × 收款人**粒度，前端算；後端這裡是按人跨全月。
        曾經算過一份沒人讀的 unpaid_amount／unpaid_count（而且註解說反了），不要再長回來。"""
        w = group_payables([(_payment(1), None)])["payees"][0]
        assert "unpaid_amount" not in w and "unpaid_count" not in w

    def test_dedup_by_payment_id(self):
        p = _payment(1, amount=10000)
        rows = [(p, _staff("a")), (p, _staff("b", "別人", "A2", "玉山", "999"))]
        r = group_payables(rows)
        assert r["grand_total"] == 10000
        assert len(r["payees"][0]["items"]) == 1

    def test_payee_id_prefers_payment_over_staff(self):
        rows = [(_payment(1, payee_id="FROM_PAYMENT"), _staff(id_number="FROM_STAFF"))]
        assert group_payables(rows)["payees"][0]["payee_id"] == "FROM_PAYMENT"
        rows2 = [(_payment(2, payee_id=""), _staff(id_number="FROM_STAFF"))]
        assert group_payables(rows2)["payees"][0]["payee_id"] == "FROM_STAFF"

    def test_empty_payee_name_becomes_unspecified(self):
        rows = [(_payment(1, payee=""), None)]
        assert group_payables(rows)["payees"][0]["payee_name"] == "未指定"

    def test_date_formats(self):
        d = datetime(2026, 7, 6, tzinfo=timezone.utc)
        rows = [(_payment(1, req_date=d, pay_date=d, planned="2026-08"), None)]
        item = group_payables(rows)["payees"][0]["items"][0]
        assert item["date"] == "2026/07/06"          # request_date 用斜線
        assert item["payment_date"] == "2026-07-06"  # payment_date 用連字號（前端月份調整靠這格式）
        assert item["planned_month"] == "2026-08"

    def test_empty_rows(self):
        r = group_payables([])
        assert r == {"payees": [], "grand_total": 0}

    def test_none_amount_counts_as_zero(self):
        rows = [(_payment(1, amount=None), None)]
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
