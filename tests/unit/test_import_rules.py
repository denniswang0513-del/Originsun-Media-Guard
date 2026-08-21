# -*- coding: utf-8 -*-
"""對帳單分類規則（bank_import_rules）—— 純函式層的行為。

規則層在 finance_category_map **之前**：
    銀行摘要文字 →〔本層〕→ 類別 →〔category_map〕→ 會計科目
右半邊本來就是資料驅動的，左半邊原本寫死 14 條在 core/bank_statement.py
（owner 2026-08-20：希望自己能設規則）。
"""
from core.bank_statement import KEYWORD_RULES, _classify, parse_statement  # noqa: E402

# 餘額鏈自洽的兩列（第二列才是要測分類的那筆）
STMT = ("2026/09/01 神秘扣款 5,000.00 995,000.00\n"
        "2026/09/02 手續費 30.00 994,970.00\n")


def test_no_rules_means_no_category():
    """規則是外部給的 —— 給空清單就什麼都不該中（證明它真的不吃寫死那份）。"""
    assert _classify("攤還本息 315614", []) == ("", 0)


def test_injected_rules_win_over_builtin():
    cat, _d = _classify("神秘扣款", [("神秘扣款", "設備耗材", 0)])
    assert cat == "設備耗材"


def test_first_match_wins_so_caller_must_sort():
    """順序即優先序 —— 呼叫端負責照 sort_order 排好再傳進來。

    🔴 這條釘住的是「規則的先後有意義」：帳戶專屬要排在通用之前，
    否則合庫的「攤還本息」會被一銀的通用規則先攔走。
    """
    rules = [("轉存", "A", 0), ("跨行轉存", "B", 0)]
    assert _classify("跨行轉存 12345", rules)[0] == "A"       # 先命中的贏
    assert _classify("跨行轉存 12345", rules[::-1])[0] == "B"  # 換順序換答案


def test_default_falls_back_to_builtin_when_rules_is_none():
    """不給 rules（例如離線跑解析器）時仍用內建那份，不會整份變未分類。"""
    assert _classify("攤還本息 315614")[0] == "貸款繳款"
    assert _classify("攤還本息 315614", None)[0] == "貸款繳款"


def test_parse_statement_threads_rules_through():
    """規則要真的傳到每一列 —— 只接參數卻沒往下傳是最容易漏的一步。"""
    r = parse_statement(STMT, rules=[("神秘扣款", "設備耗材", 0)])
    assert r.ok, r.errors
    cats = {row.note[:4]: row.category for row in r.rows}
    assert cats.get("神秘扣款") == "設備耗材"
    assert cats.get("手續費") == ""       # 沒給這條規則就不該中


def test_builtin_rules_are_the_seed_shape():
    """內建那份的形狀 = 種子灌進 DB 時用的 (關鍵字, 類別, 方向)。

    db/seed_finance.seed_finance_stage2 直接拆這三元組建列，形狀變了種子會炸。
    """
    assert KEYWORD_RULES
    for kw, cat, direction in KEYWORD_RULES:
        assert isinstance(kw, str) and kw
        assert isinstance(cat, str) and cat
        assert direction in (-1, 0, 1)


class TestKeywordShape:
    """關鍵字形狀防呆 —— 擋的是「會生效但意思完全不對」的規則。

    錯的規則不會報錯，只會靜靜把帳分到錯的地方，所以在建立時就擋。
    """

    def _check(self, kw):
        from routers.api_finance import _assert_usable_keyword
        import pytest
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            _assert_usable_keyword(kw)
        assert ei.value.status_code == 422
        return ei.value.detail

    def test_date_like_keywords_are_rejected(self):
        """🔴 `2026/08/` 是原本那個 bug 的字面值 —— 預覽的「＋規則」曾用
        `description.slice(0,8)` 當預設值，而摘要裡留著銀行的入帳日欄。
        存下去就是「2026 年 8 月的交易全歸這一類」，而且它會生效。
        """
        for kw in ("2026/08/", "2026/08/20", "09-08", "8/20", "2026-8"):
            assert "日期" in self._check(kw), kw

    def test_pure_number_keywords_are_rejected(self):
        """帳號/流水號只會中那一筆，卻看起來像設好了規則。"""
        for kw in ("150725950595", "１２３"):
            assert "純數字" in self._check(kw)

    def test_single_char_is_rejected(self):
        """「中」會把「中小」「中信」「台中」全部掃進來。"""
        assert "兩個字" in self._check("中")

    def test_real_keywords_pass(self):
        from routers.api_finance import _assert_usable_keyword
        for kw in ("電信費", "中小７月", "ATM跨行轉入", "網路轉帳", "攤還本息"):
            _assert_usable_keyword(kw)     # 不該拋
