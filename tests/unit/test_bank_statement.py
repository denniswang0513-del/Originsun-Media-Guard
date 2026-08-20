# -*- coding: utf-8 -*-
"""core/bank_statement.py — 用兩家銀行的真實版面測（2026-08-19 owner 提供的
第一銀行「交易明細查詢」與合作金庫「歷史交易查詢」）。

這兩份版面的差異正是解析器存在的理由：
  一銀   空金額欄印 `-` 佔位，欄序 日期 時間 幣別 支出 存入 餘額 票號 摘要 備註
  合庫   空金額欄**直接消失**，欄序 序號 日期 時間 摘要 行庫 幣別 提款 存款 餘額 備註
欄序不同、佔位符不同 —— 所以方向一律由餘額鏈推，不靠欄位位置。
"""
from core.bank_statement import (  # noqa: E402
    KEYWORD_RULES, StmtRow, _to_int, match_loan_payments, parse_statement)

# ── 第一銀行（空欄有 `-` 佔位）──
FIRST_BANK = """
交易日期 交易時間 幣別 支出金額 存入金額 餘額 票據號碼 摘要 備註
2025/09/10 06:48:19 新臺幣 - 5,000.00 9,742.00 跨行轉帳 0120082120000062728
2025/09/15 01:22:03 新臺幣 1,850.00 - 7,892.00 放款本息 13366031755IN1850
2025/09/15 01:22:03 新臺幣 278.00 - 7,614.00 放款本息 13366031755IN278
2025/10/03 10:42:56 新臺幣 - 3,185.00 10,799.00 中小７月
幣別 支出金額總計 存入金額總計
新臺幣 2,128.00 8,185.00
"""

# ── 合作金庫（空欄直接消失，第一列沒有前列餘額）──
COOP_BANK = """
序號 交易日期 摘要 交易行庫 幣別 提款金額 存款金額 餘額 備註 支票號碼
1 2025/08/21 01:58:01 中心轉存 合庫營 TWD 2865.00 206895.00 文創補貼息
2 2025/08/28 06:31:12 跨行轉入 B012 TWD 35000.00 241895.00 0082120000062728
3 2025/09/08 01:12:41 攤還本息 合庫松山 TWD 4470.00 237425.00 09-08 315614
4 2025/09/08 01:12:42 攤還本息 合庫松山 TWD 25290.00 212135.00 09-08 315611
幣別 提款總筆數 提款總金額 存款總筆數 存款總金額
TWD 2 29,760.00 2 37,865.00
"""


class TestParseFirstBank:
    def test_directions_come_from_balance_chain_not_columns(self):
        r = parse_statement(FIRST_BANK)
        assert r.ok, r.errors
        assert len(r.rows) == 4
        assert [x.amount for x in r.rows] == [5000, -1850, -278, 3185]

    def test_totals_cross_check_passes(self):
        r = parse_statement(FIRST_BANK)
        assert (r.total_out, r.total_in) == (2128, 8185)
        assert not r.errors

    def test_account_number_not_mistaken_for_amount(self):
        """備註 13366031755IN1850 是帳號＋利息註記，不能被當成金額欄。"""
        r = parse_statement(FIRST_BANK)
        assert r.rows[1].amount == -1850
        assert r.rows[1].balance == 7892

    def test_categories_from_keywords(self):
        r = parse_statement(FIRST_BANK)
        assert [x.category for x in r.rows] == [
            "轉存", "貸款繳款", "貸款繳款", "貸款補貼"]   # 中小７月＝信保補貼


class TestParseCoopBank:
    def test_first_row_direction_solved_from_printed_totals(self):
        """合庫第一列沒有前列可減 —— 用銀行印的總計反推，不該標 inferred。"""
        r = parse_statement(COOP_BANK)
        assert r.ok, r.errors
        assert r.rows[0].amount == 2865
        assert r.rows[0].inferred is False

    def test_all_amounts(self):
        r = parse_statement(COOP_BANK)
        assert [x.amount for x in r.rows] == [2865, 35000, -4470, -25290]
        assert (r.total_out, r.total_in) == (29760, 37865)

    def test_opening_balance_overrides_inference(self):
        no_totals = "\n".join(l for l in COOP_BANK.splitlines()
                              if "總金額" not in l and "提款總筆數" not in l
                              and not l.startswith("TWD"))
        r = parse_statement(no_totals, opening_balance=204030)
        assert r.rows[0].amount == 2865
        assert r.rows[0].inferred is False


class TestValidation:
    def test_broken_balance_chain_is_rejected_not_guessed(self):
        bad = FIRST_BANK.replace("7,892.00", "7,000.00")
        r = parse_statement(bad)
        assert not r.ok
        assert any("對不上" in e for e in r.errors)

    def test_totals_mismatch_is_an_error(self):
        bad = FIRST_BANK.replace("存入金額總計\n新臺幣 2,128.00 8,185.00",
                                 "存入金額總計\n新臺幣 2,128.00 9,999.00")
        r = parse_statement(bad)
        assert not r.ok

    def test_image_only_pdf_gives_actionable_error(self):
        r = parse_statement("這是一張掃描影像\n沒有任何交易列")
        assert not r.ok
        assert any("複製貼上" in e for e in r.errors)

    def test_missing_totals_warns_but_still_parses(self):
        no_totals = "\n".join(l for l in FIRST_BANK.splitlines() if "總計" not in l
                              and not l.startswith("新臺幣 2,128"))
        r = parse_statement(no_totals)
        assert r.ok
        assert any("沒有印總計" in w for w in r.warnings)


class TestLoanMatching:
    """配對是照「到期日最接近」挑期別（上限 20 天），不是從第一期依序消耗。"""

    def _rows(self, specs):
        return [StmtRow(line_no=i, date=d, amount=a, balance=0, note=n,
                        category="貸款繳款" if a < 0 else "")
                for i, (d, a, n) in enumerate(specs, start=1)]

    def _loan(self, **kw):
        base = {"id": "L", "name": "測試貸款",
                "periods": [{"period_no": n, "total": 4456,
                             "due_date": f"2026-{n + 8:02d}-08", "paid": False}
                            for n in (1, 2, 3)]}   # 2026-09-08 / 10-08 / 11-08
        base.update(kw)
        return base

    def test_split_deductions_sum_to_one_period(self):
        """一銀 150 萬分兩次撥款 → 銀行每月扣 29,418＋3,269＝32,687 算一期。"""
        rows = self._rows([("2026-09-15", -29418, "放款本息 IN2448"),
                           ("2026-09-15", -3269, "放款本息 IN272")])
        loan = {"id": "L1", "name": "一銀150萬",
                "periods": [{"period_no": 1, "total": 32687,
                             "due_date": "2026-09-15", "paid": False}]}
        m = match_loan_payments(rows, [loan])
        assert len(m) == 1
        assert m[0]["confidence"] == "exact"
        assert m[0]["amount"] == 32687
        assert sorted(m[0]["lines"]) == [1, 2]

    def test_account_number_beats_amount_mismatch(self):
        """銀行月付固定 4,470、系統依剩餘本金重算是 4,456 —— 金額配不上，
        但備註帶放款帳號就認得出來。"""
        rows = self._rows([("2026-09-08", -4470, "攤還本息 09-08 315614")])
        m = match_loan_payments(rows, [self._loan(account_no="0070040315614")])
        assert (m[0]["loan_id"], m[0]["period_no"], m[0]["confidence"]) == ("L", 1, "account")

    def test_partial_statement_matches_by_date_not_by_order(self):
        """只匯 10 月那份對帳單 → 要配到第 2 期（到期日 10-08），不是第 1 期。"""
        rows = self._rows([("2026-10-08", -4470, "攤還本息 10-08 315614")])
        m = match_loan_payments(rows, [self._loan(account_no="0070040315614")])
        assert m[0]["period_no"] == 2

    def test_reimporting_an_already_paid_period_is_flagged(self):
        """重匯同一份對帳單：該期已記過繳款 → already_paid（UI 標重複、不再寫一次）。"""
        loan = self._loan(account_no="0070040315614")
        loan["periods"][0]["paid"] = True
        rows = self._rows([("2026-09-08", -4470, "攤還本息 09-08 315614")])
        m = match_loan_payments(rows, [loan])
        assert m[0]["confidence"] == "already_paid"
        assert m[0]["period_no"] == 1

    def test_payments_predating_the_schedule_are_left_alone(self):
        """貸款是用「導入舊貸」建的，攤還表從 2026-09 起 —— 對帳單裡 2025 年的
        歷史繳款不該被硬塞進第 1 期（那會蓋上完全錯的繳款日）。"""
        rows = self._rows([("2025-09-08", -4470, "攤還本息 09-08 315614")])
        m = match_loan_payments(rows, [self._loan(account_no="0070040315614")])
        assert m[0]["loan_id"] is None
        assert m[0]["confidence"] == ""

    def test_each_period_claimed_once_per_batch(self):
        """同一批裡兩筆都指向同一期 → 只有一筆配得到，另一筆留給人判斷。"""
        rows = self._rows([("2026-09-08", -4470, "攤還本息 315614"),
                           ("2026-09-09", -4470, "攤還本息 315614")])
        m = match_loan_payments(rows, [self._loan(account_no="0070040315614")])
        assert sorted(x["confidence"] for x in m) == ["", "account"]

    def test_unmatched_is_left_for_human_not_guessed(self):
        rows = self._rows([("2026-09-08", -9999, "放款本息 未知")])
        loan = {"id": "L3", "name": "某貸款",
                "periods": [{"period_no": 1, "total": 1234,
                             "due_date": "2026-09-08", "paid": False}]}
        m = match_loan_payments(rows, [loan])
        assert m[0]["loan_id"] is None
        assert m[0]["confidence"] == ""

    def test_deposits_are_never_treated_as_loan_payments(self):
        rows = self._rows([("2026-09-08", 35000, "跨行轉入")])
        rows[0].category = "貸款繳款"      # 就算分類錯了
        loan = {"id": "X", "periods": [{"period_no": 1, "total": 35000,
                                        "due_date": "2026-09-08", "paid": False}]}
        assert match_loan_payments(rows, [loan]) == []


def test_keyword_rules_are_ordered_specific_before_generic():
    """比對是**由上而下第一個中的就算**，所以泛用詞排在專用詞前面會把它遮掉
    （'轉存' 若排在 '中心轉存' 前面，文創補貼息就會被歸成轉存）。這測釘住順序。"""
    seen = []
    for kw, _cat, _d in KEYWORD_RULES:
        shadowed = [e for e in seen if e in kw]
        assert not shadowed, f"「{kw}」永遠輪不到 —— 更泛用的 {shadowed} 排在它前面"
        seen.append(kw)


def test_account_number_survives_into_note_for_loan_matching():
    """回歸測試：`09-08 315614` 的帳號被當數字濾掉的話，貸款配對會全部失效
    （2026-08-19 實測 24 筆繳款一筆都配不上）。金額 token 才該濾，純數字不該。"""
    r = parse_statement(COOP_BANK)
    loan_row = next(x for x in r.rows if x.category == "貸款繳款")
    assert "315614" in loan_row.note
    assert "4,470" not in loan_row.note and "237,425" not in loan_row.note

    m = match_loan_payments([loan_row], [{
        "id": "L", "name": "合庫1", "account_no": "0070040315614",
        "periods": [{"period_no": 1, "total": 4456,
                     "due_date": "2025-09-08", "paid": False}]}])
    assert m[0]["confidence"] == "account"


def test_first_bank_subsidy_rows_are_classified():
    """一銀的利息補貼摘要長「中小７月」「中小十一」——跟合庫的「中心轉存」
    是同一件事，都要歸貸款補貼（不然這 5 筆會變未分類）。"""
    txt = ("交易日期 交易時間 幣別 支出金額 存入金額 餘額 票據號碼 摘要 備註\n"
           "2025/10/03 10:42:56 新臺幣 - 3,185.00 7,375.00 中小７月\n"
           "2026/02/04 14:11:49 新臺幣 - 1,438.00 8,813.00 中小十一\n")
    r = parse_statement(txt, opening_balance=4190)
    assert [x.category for x in r.rows] == ["貸款補貼", "貸款補貼"]
    assert [x.amount for x in r.rows] == [3185, 1438]


class TestTaiwanFormats:
    """台灣網銀常見寫法。這些是把 banking.js 那支貼上解析器收掉的前提 ——
    能力沒對齊就合併等於功能倒退。"""

    def test_roc_year(self):
        """114/07/01 = 2025-07-01。只認西元的話整份對帳單一列都解析不出來。"""
        txt = ("114/07/01 跨行轉入 5,000.00 9,742.00\n"
               "114/07/15 放款本息 1,850.00 7,892.00\n")
        r = parse_statement(txt, opening_balance=4742)
        assert [x.date for x in r.rows] == ["2025-07-01", "2025-07-15"]
        assert [x.amount for x in r.rows] == [5000, -1850]

    def test_chinese_date_separators(self):
        txt = ("2025年9月10日 跨行轉入 5,000.00 9,742.00\n"
               "2025年9月15日 放款本息 1,850.00 7,892.00\n")
        r = parse_statement(txt, opening_balance=4742)
        assert [x.date for x in r.rows] == ["2025-09-10", "2025-09-15"]

    def test_accounting_parentheses_are_negative(self):
        assert _to_int("(1,234)") == -1234
        assert _to_int("（1,234）") == -1234
        assert _to_int("1,234") == 1234

    def test_impossible_month_day_is_not_a_date(self):
        """13/45/99 不是日期 —— 別讓金額被誤配成日期而吃掉整列。"""
        assert parse_statement("13/45/99 什麼 1,000.00 2,000.00\n").rows == []


class TestUnknownDirection:
    """摘要看不出方向時（KEYWORD_RULES 的 direction=0）不可以裝作知道。

    「轉存 / 跨行轉帳 / 網路轉帳」三條規則的方向提示刻意是 0 —— 兩個方向都常見。
    第一列又沒有前列餘額、沒有總計時只能填一個值，但必須讓人知道那是填的。
    """

    STMT = ("2026/08/01 轉存 500,000.00 1,500,000.00\n"
            "2026/08/02 手續費 30.00 1,499,970.00\n")

    def test_direction_zero_first_row_is_flagged_inferred(self):
        r = parse_statement(self.STMT)
        assert r.ok, r.errors
        assert r.rows[0].inferred is True

    def test_warning_says_it_could_not_tell(self):
        """警告要講「看不出方向、先當支出」，不能講成「照摘要推的」。

        🔴 一筆客戶匯進來的 500,000 轉存會被填成 -500,000，帳差 100 萬，而回頭
        核對只驗 abs() 所以驗不出來 —— 唯一的防線就是這句話有沒有講清楚。
        """
        r = parse_statement(self.STMT)
        joined = "\n".join(r.warnings)
        assert "看不出方向" in joined and "先當支出" in joined, joined

    def test_opening_balance_removes_the_guess(self):
        """知道期初餘額就不用猜 —— 方向由餘額鏈算出來，且不再標 inferred。"""
        r = parse_statement(self.STMT, opening_balance=1_000_000)
        assert r.rows[0].amount == 500_000      # 是存入，不是支出
        assert r.rows[0].inferred is False
