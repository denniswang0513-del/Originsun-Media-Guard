"""預支款結算純邏輯（core/crm_logic.py — CLAUDE.md §7.15 規則）。

這是公司帳務的判定規則，任何改動必須先過這裡。
情境對照 CLAUDE.md 的「預支 $40,000 給製片拍技術展示影片」例子。
"""
from core.crm_logic import compute_advance_status


class TestAdvanceLifecycle:
    """CLAUDE.md 文件例：預支 40,000 的完整生命週期。"""

    def test_step1_created_not_paid_not_returned(self):
        """建立預支款：還沒發款、沒收款、餘額 = 全額。"""
        s = compute_advance_status(40000, 0, 0, 0)
        assert s == {"is_paid": False, "is_returned": False,
                     "balance": 40000, "is_settled": False}

    def test_step2_cash_out_marks_paid(self):
        """收支記一筆發款 40,000：已發款、待收款。"""
        s = compute_advance_status(40000, 0, 40000, 0)
        assert s["is_paid"] and not s["is_returned"] and not s["is_settled"]

    def test_step3_expenses_reduce_balance(self):
        """製片花了 38,000：餘額 2,000（需還款），未結清。"""
        s = compute_advance_status(40000, 38000, 40000, 0)
        assert s["balance"] == 2000
        assert s["is_paid"] and not s["is_settled"]

    def test_step4_return_settles_when_balance_zero(self):
        """還款 2,000：發款 ✓ 收款 ✓ 餘額 0 → 已結清。"""
        s = compute_advance_status(40000, 38000, 40000, 2000)
        assert s == {"is_paid": True, "is_returned": True,
                     "balance": 0, "is_settled": True}

    def test_full_spend_needs_token_return_to_settle(self):
        """全部花完（餘額 0）但沒有任何收款紀錄 → 不算結清（已收款條件不成立）。"""
        s = compute_advance_status(40000, 40000, 40000, 0)
        assert s["balance"] == 0
        assert not s["is_returned"] and not s["is_settled"]


class TestAdvanceEdgeCases:
    def test_partial_cash_out_not_paid(self):
        """發款不足額（30,000 < 40,000）→ 未發款。"""
        s = compute_advance_status(40000, 0, 30000, 0)
        assert not s["is_paid"]

    def test_over_cash_out_still_paid(self):
        """發款超額也算已發款（>= 判定）。"""
        s = compute_advance_status(40000, 0, 45000, 0)
        assert s["is_paid"]

    def test_zero_amount_never_paid(self):
        """金額 0 的預支永遠不算已發款（amt > 0 是硬條件），也不可能結清。"""
        s = compute_advance_status(0, 0, 0, 0)
        assert not s["is_paid"] and not s["is_settled"]
        s2 = compute_advance_status(0, 0, 100, 100)
        assert not s2["is_paid"] and not s2["is_settled"]

    def test_none_inputs_coerce_to_zero(self):
        """DB 聚合可能回 None — 一律當 0 處理，不能炸。"""
        s = compute_advance_status(None, None, None, None)
        assert s == {"is_paid": False, "is_returned": False,
                     "balance": 0, "is_settled": False}

    def test_over_returned_negative_balance_not_settled(self):
        """多還了（餘額變負）→ 帳不平，不算結清。"""
        s = compute_advance_status(40000, 38000, 40000, 5000)
        assert s["balance"] == -3000 and not s["is_settled"]

    def test_returned_without_expenses(self):
        """沒花錢直接全額退回：發款 ✓ 收款 ✓ 餘額 0 → 結清。"""
        s = compute_advance_status(40000, 0, 40000, 40000)
        assert s["is_settled"]
