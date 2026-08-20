"""財務階段二純邏輯測試（core/finance_logic.py）。

cat_map fixture 直接用 db/seed_finance.py 的種子對映建 —— 種子改了
（新增/改 treatment）測試會跟著驗，對映表與分類邏輯不會漂移。
"""
from datetime import date, datetime, timezone

import pytest

from core.finance_logic import (
    auto_match_statement_lines,
    bank_running_balance,
    cash_entry_flow,
    classify_cash_entry,
    month_of,
    reconciliation_diff,
    statement_line_status,
    workbench_summary,
)
from db.seed_finance import SEED_CATEGORY_MAP

# {(source, category_text): treatment} — classify_cash_entry 吃的形狀
CAT_MAP = {(r["source"], r["category_text"]): r["treatment"] for r in SEED_CATEGORY_MAP}

# 收支明細每個 category 的**預期** treatment —— 刻意手寫，不從種子推導。
#
# 🔴 原本這張表是 `{r["category_text"]: r["treatment"] for r in SEED_CATEGORY_MAP …}`，
# 也就是從種子自己抄一份再拿去跟種子比 —— 循環論證。實測（2026-08-20）：把種子的
# 「貸款繳款」從 loan 改成 direct_expense，底下 32 個參數化案例**全部照樣通過**。
# 一個對帳單匯入進來的還本被當成費用、直接吃掉當期損益，測試會全綠。
# 手寫在這裡，改種子就必須也改這裡，那一下就是「你確定要改分錄嗎」的關口。
_CASH_EXPECTED = {
    "水電網路": "direct_expense",
    "交際應酬": "direct_expense",
    "行政": "direct_expense",
    "其他": "direct_expense",
    "其他收入": "direct_income",
    "房租": "direct_expense",
    "建構": "direct_expense",
    "專案": "direct_income",
    "專案外包": "direct_expense",
    "專案雜支": "direct_expense",
    "教育訓練": "direct_expense",
    "設備耗材": "direct_expense",
    "設備維護": "direct_expense",
    "軟體網路服務": "direct_expense",
    "勞健保": "direct_expense",
    "發票代開": "passthrough",      # 代開：錢過路，不是我們的收入
    "會計": "direct_expense",
    "業務推廣": "direct_expense",
    "製作金": "direct_expense",
    "銀行利息": "direct_income",
    "獎金": "direct_expense",
    "請款單": "ap_settlement",      # 沖應付，不重複認費用
    "辦公室管理費": "direct_expense",
    "營所稅": "tax_income",
    "營業稅": "tax_vat",
    "薪資": "direct_expense",
    "轉存": "transfer",             # 帳戶間搬錢，兩邊都不進損益
    "貸款繳款": "loan",             # 還本走籌資，不進損益
    "貸款撥款": "loan",
    "貸款補貼": "direct_income",    # 政府貼息是真的收入
    "銀行借款": "loan",
    "後期雜支": "direct_expense",
}
_SEED_CASH = {r["category_text"]: r["treatment"]
              for r in SEED_CATEGORY_MAP if r["source"] == "cash"}


class TestClassifyCashEntry:
    def test_seed_covers_32_cash_categories(self):
        """種子必須恰好覆蓋收支明細現行 category 值，而且分錄與預期一致。

        27 原生 + 2 貸款（階段四）+ 1 後期雜支（2026-08-17 零用金整合：Sheet 上
        實際用過但對映表缺的唯一一個）+ 2 對帳單匯入（2026-08-20：貸款補貼＝政府
        貼息入帳、銀行借款＝撥款/還本）。這個數字是**刻意的釘子** —— 加科目要在
        這裡明白地改，不能默默長。

        集合相等這條比數字更重要：加了新科目卻忘了在 _CASH_EXPECTED 想清楚它的
        分錄，就會在這裡停下來。
        """
        assert len(_CASH_EXPECTED) == 32
        assert set(_SEED_CASH) == set(_CASH_EXPECTED), (
            "種子與預期表的科目集合不一致 —— 新增/移除科目要兩邊一起想清楚。"
            f" 只在種子: {sorted(set(_SEED_CASH) - set(_CASH_EXPECTED))}"
            f" 只在預期: {sorted(set(_CASH_EXPECTED) - set(_SEED_CASH))}")

    def test_keyword_rules_only_produce_seeded_categories(self):
        """對帳單解析器吐出的 category 必須都在種子裡。

        🔴 不然那些列會靜默落到報表的「未歸類」—— 2026-08-20 的「貸款補貼」與
        「銀行借款」就是這樣漏的（解析器會產、種子沒有），靠人工比對才發現。
        兩邊都是模組常數，這條零成本。
        """
        from core.bank_statement import KEYWORD_RULES
        produced = {cat for _kw, cat, _d in KEYWORD_RULES}
        assert produced <= set(_SEED_CASH), (
            f"解析器會產出但種子沒有的科目: {sorted(produced - set(_SEED_CASH))}")

    @pytest.mark.parametrize("category,expected", sorted(_CASH_EXPECTED.items()))
    def test_all_cash_categories(self, category, expected):
        """32 個 cash category：無關聯 id 時走 category 對映，且分錄要對。

        預期值來自手寫的 _CASH_EXPECTED（不是種子），所以種子改錯這裡會紅。
        """
        entry = {"category": category, "invoice_id": None,
                 "advance_payment_id": None, "payment_request_id": None}
        assert classify_cash_entry(entry, CAT_MAP) == expected

    def test_spot_check_key_treatments(self):
        """抽查關鍵對映（種子如被改掉會在這裡爆，不是默默變）。"""
        assert _CASH_EXPECTED["請款單"] == "ap_settlement"
        assert _CASH_EXPECTED["轉存"] == "transfer"
        assert _CASH_EXPECTED["營業稅"] == "tax_vat"
        assert _CASH_EXPECTED["營所稅"] == "tax_income"
        assert _CASH_EXPECTED["發票代開"] == "passthrough"
        assert _CASH_EXPECTED["專案"] == "direct_income"
        assert _CASH_EXPECTED["貸款繳款"] == "loan"   # 階段四：不進損益、CF financing
        assert _CASH_EXPECTED["貸款撥款"] == "loan"
        # 對帳單匯入帶進來的兩個：貼息是真的收入、借款本金走籌資不進損益
        assert _CASH_EXPECTED["貸款補貼"] == "direct_income"
        assert _CASH_EXPECTED["銀行借款"] == "loan"

    # ── 關聯 id 優先序 ──
    def test_advance_id_wins_over_everything(self):
        entry = {"category": "專案", "invoice_id": "inv1",
                 "advance_payment_id": "adv1", "payment_request_id": "pr1"}
        assert classify_cash_entry(entry, CAT_MAP) == "advance"

    def test_invoice_id_wins_over_payment_request(self):
        entry = {"category": "請款單", "invoice_id": "inv1",
                 "advance_payment_id": None, "payment_request_id": "pr1"}
        assert classify_cash_entry(entry, CAT_MAP) == "ar_settlement"

    def test_payment_request_id_wins_over_category(self):
        entry = {"category": "房租", "payment_request_id": "pr1"}
        assert classify_cash_entry(entry, CAT_MAP) == "ap_settlement"

    def test_empty_string_link_ids_ignored(self):
        """空字串關聯 id 視同無關聯（DB 撈出來常是 '' 不是 None）。"""
        entry = {"category": "房租", "invoice_id": "",
                 "advance_payment_id": "", "payment_request_id": ""}
        assert classify_cash_entry(entry, CAT_MAP) == "direct_expense"

    # ── unmapped ──
    def test_unknown_category_unmapped(self):
        assert classify_cash_entry({"category": "沒見過的類別"}, CAT_MAP) == "unmapped"

    def test_none_and_missing_category_unmapped(self):
        assert classify_cash_entry({"category": None}, CAT_MAP) == "unmapped"
        assert classify_cash_entry({}, CAT_MAP) == "unmapped"

    def test_payment_source_category_not_matched_for_cash(self):
        """cat_map 查表只認 source='cash'：payment 專屬值（零用金）查不到。"""
        assert classify_cash_entry({"category": "零用金"}, CAT_MAP) == "unmapped"


class TestCashEntryFlow:
    def test_deposit_minus_all_outflows(self):
        entry = {"deposit": 10000, "expense": 3000, "bank_fee": 30, "claim": 500}
        assert cash_entry_flow(entry) == 6470

    def test_none_fields_coerce_to_zero(self):
        assert cash_entry_flow({"deposit": None, "expense": None,
                                "bank_fee": None, "claim": None}) == 0
        assert cash_entry_flow({}) == 0

    def test_pure_expense_is_negative(self):
        assert cash_entry_flow({"expense": 1200}) == -1200

    def test_bank_fee_and_claim_count_as_outflow(self):
        assert cash_entry_flow({"bank_fee": 30}) == -30
        assert cash_entry_flow({"claim": 800}) == -800


class TestBankRunningBalance:
    def test_empty_entries_returns_opening(self):
        assert bank_running_balance(50000, []) == 50000

    def test_mixed_entries(self):
        entries = [
            {"deposit": 100000},                  # +100000
            {"expense": 40000, "bank_fee": 30},   # -40030
            {"claim": 2000},                      # -2000
        ]
        assert bank_running_balance(10000, entries) == 67970

    def test_none_opening_coerces_to_zero(self):
        assert bank_running_balance(None, [{"deposit": 500}]) == 500

    def test_aggregate_equals_itemized(self):
        """流水公式線性 → SQL SUM 聚合餵法與逐筆餵法等價（endpoint 依賴此性質）。"""
        entries = [{"deposit": 100, "expense": 40}, {"deposit": 60, "bank_fee": 5}]
        agg = [{"deposit": 160, "expense": 40, "bank_fee": 5}]
        assert bank_running_balance(1000, entries) == bank_running_balance(1000, agg)


class TestReconciliationDiff:
    def test_balanced(self):
        assert reconciliation_diff(88000, 88000) == {"diff": 0, "status": "balanced"}

    def test_statement_higher(self):
        r = reconciliation_diff(88000, 90000)
        assert r == {"diff": 2000, "status": "diff"}

    def test_statement_lower_negative_diff(self):
        r = reconciliation_diff(90000, 88000)
        assert r == {"diff": -2000, "status": "diff"}

    def test_none_coerce(self):
        assert reconciliation_diff(None, None) == {"diff": 0, "status": "balanced"}


class TestMonthOf:
    def test_datetime(self):
        assert month_of(datetime(2026, 7, 11, 10, 30, tzinfo=timezone.utc)) == "2026-07"

    def test_date(self):
        assert month_of(date(2025, 1, 31)) == "2025-01"

    def test_iso_string(self):
        assert month_of("2026-07-11") == "2026-07"
        assert month_of("2026-07-11T09:00:00+08:00") == "2026-07"
        assert month_of("2026-07") == "2026-07"

    def test_none_returns_none(self):
        assert month_of(None) is None

    def test_garbage_string_returns_none(self):
        assert month_of("not-a-date") is None
        assert month_of("2026/07/11") is None
        assert month_of("2026-13-01") is None  # 不存在的月份


# ── 對帳工作台（明細逐筆勾銷）────────────────────────────────

def _line(id, amount, day=None, matched=None, note=None):
    return {"id": id, "amount": amount,
            "line_date": date(2026, 7, day) if day else None,
            "matched_entry_id": matched, "note": note}


def _entry(id, flow, day=None):
    """flow 有號：正=deposit、負=expense（cash_entry_flow 會還原成同值）。"""
    return {"id": id, "entry_date": date(2026, 7, day) if day else None,
            "deposit": flow if flow > 0 else None,
            "expense": -flow if flow < 0 else None,
            "bank_fee": None, "claim": None}


class TestStatementLineStatus:
    def test_matched_beats_noted(self):
        assert statement_line_status(_line("a", 100, matched="e1", note="x")) == "matched"

    def test_noted(self):
        assert statement_line_status(_line("a", 100, note="上月已入帳（時間差）")) == "noted"

    def test_blank_note_is_unmatched(self):
        assert statement_line_status(_line("a", 100, note="  ")) == "unmatched"


class TestAutoMatchStatementLines:
    def test_exact_amount_and_date(self):
        pairs = auto_match_statement_lines(
            [_line("l1", 5000, day=3)], [_entry("e1", 5000, day=3)])
        assert pairs == [("l1", "e1")]

    def test_signed_amount_no_cross_sign_match(self):
        """存入 +5000 不可配到支出 −5000（有號比對）。"""
        assert auto_match_statement_lines(
            [_line("l1", 5000, day=3)], [_entry("e1", -5000, day=3)]) == []

    def test_nearest_date_wins(self):
        pairs = auto_match_statement_lines(
            [_line("l1", 5000, day=10)],
            [_entry("far", 5000, day=2), _entry("near", 5000, day=9)])
        assert pairs == [("l1", "near")]

    def test_tolerance_days_blocks_far_match(self):
        assert auto_match_statement_lines(
            [_line("l1", 5000, day=1)], [_entry("e1", 5000, day=20)]) == []

    def test_tie_prefers_earlier_entry(self):
        """|日期差| 同 → entry_date 較早者（確定性）。"""
        pairs = auto_match_statement_lines(
            [_line("l1", 5000, day=10)],
            [_entry("later", 5000, day=12), _entry("earlier", 5000, day=8)])
        assert pairs == [("l1", "earlier")]

    def test_entry_used_once(self):
        """兩列同金額搶一筆收支 → 只有日期較早的列配到。"""
        pairs = auto_match_statement_lines(
            [_line("l2", 5000, day=6), _line("l1", 5000, day=4)],
            [_entry("e1", 5000, day=5)])
        assert pairs == [("l1", "e1")]

    def test_missing_date_only_matches_sole_candidate(self):
        assert auto_match_statement_lines(
            [_line("l1", 5000)], [_entry("e1", 5000, day=5)]) == [("l1", "e1")]
        assert auto_match_statement_lines(
            [_line("l1", 5000)],
            [_entry("e1", 5000, day=5), _entry("e2", 5000, day=6)]) == []

    def test_matched_line_and_taken_entry_skipped(self):
        """已配對的列不重配；被其他列認領的收支不再是候選。"""
        pairs = auto_match_statement_lines(
            [_line("l1", 5000, day=3, matched="e1"), _line("l2", 5000, day=3)],
            [_entry("e1", 5000, day=3), _entry("e2", 5000, day=4)])
        assert pairs == [("l2", "e2")]

    def test_bank_fee_included_in_flow(self):
        """收支淨流含匯費：expense 4970 + bank_fee 30 = 對帳單 −5000。"""
        entry = {"id": "e1", "entry_date": date(2026, 7, 3),
                 "deposit": None, "expense": 4970, "bank_fee": 30, "claim": None}
        assert auto_match_statement_lines(
            [_line("l1", -5000, day=3)], [entry]) == [("l1", "e1")]


class TestWorkbenchSummary:
    def test_counts_and_sums(self):
        lines = [_line("l1", 5000, matched="e1"),
                 _line("l2", -2000, note="時間差：上月已記"),
                 _line("l3", 800)]
        entries = [{"id": "e1", "amount": 5000, "matched": True},
                   {"id": "e2", "amount": -300, "matched": False}]
        s = workbench_summary(lines, entries)
        assert (s["lines_total"], s["lines_matched"], s["lines_noted"],
                s["lines_unmatched"]) == (3, 1, 1, 1)
        assert s["lines_bank_only"] == 2                 # noted+unmatched 同一桶（計數與金額同源）
        assert s["lines_unmatched_sum"] == -2000 + 800   # noted 也算銀行有系統沒有
        assert (s["entries_total"], s["entries_matched"], s["entries_unmatched"]) == (2, 1, 1)
        assert s["entries_unmatched_sum"] == -300

    def test_empty(self):
        s = workbench_summary([], [])
        assert s["lines_total"] == 0 and s["entries_unmatched_sum"] == 0
