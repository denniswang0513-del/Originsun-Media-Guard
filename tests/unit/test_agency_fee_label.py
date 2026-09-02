# -*- coding: utf-8 -*-
"""發票代開費不叫「銀行手續費」（owner 2026-09-02「這個是哪裡來的？」）。

代開費走 `bank_fee` 鏈是刻意的 —— 那條守的是「淨流入不變」
（`recognize_receipt_fee`），對帳與銀行餘額鏈都靠它。但損益上那一行因此叫
「銀行手續費（各筆匯費合計）」，而 owner 看到的那 66,960 **一毛真的匯費都
沒有**（同期間帳上 bank_fee 是 0 筆）。金額沒錯，名字錯了 —— 而錯的名字會讓
人以為銀行收了六萬七的手續費。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from core.finance_logic import agency_fee_total, bank_fee_total  # noqa: E402
from services.finance_statements import explode_cash_splits  # noqa: E402
from tests.unit._srcscan import code_only, func_body, repo_src  # noqa: E402

MSET = {"2026-08"}


def _entry(**kw):
    base = {"id": "e1", "entry_date": "2026-08-13", "deposit": 350436,
            "expense": None, "bank_fee": None, "claim": None, "category": "公司_專案",
            "note": "", "project_id": None, "invoice_id": None,
            "advance_payment_id": None, "payment_request_id": None}
    base.update(kw)
    return base


def test_the_fee_is_tagged_so_the_two_buckets_can_be_named_apart():
    """展開時同時記 `agency_fee` —— 金額與淨流完全不變（那條靠 bank_fee），
    這一欄只給名字用。"""
    rows = explode_cash_splits([_entry()], {"e1": [
        {"id": "s1", "amount": 291640, "fee": 66960, "category": "公司_專案",
         "project_id": "p1"},
        {"id": "s2", "amount": 58796, "category": "公司_專案", "project_id": "p2"},
    ]})
    s1 = [r for r in rows if r["id"] == "s1"][0]
    assert s1["deposit"] == 291640 + 66960          # 毛額進營收
    assert s1["bank_fee"] == 66960                  # 淨流不變那條沒動
    assert s1["agency_fee"] == 66960                # 名字用的標記
    assert (s1["deposit"] - s1["bank_fee"]) == 291640, "淨流入被改掉了"
    # 沒有 fee 的拆項不帶這一欄
    assert not [r for r in rows if r["id"] == "s2"][0].get("agency_fee")


def test_the_two_lines_add_up_to_the_old_single_line():
    """🔴 拆名字**不可以動到金額**：真匯費 ＝ bank_fee_total − agency_fee_total，
    兩行相加＝原本那一行，管理費用小計不變。"""
    rows = explode_cash_splits([_entry(bank_fee=30)], {"e1": [
        {"id": "s1", "amount": 291640, "fee": 66960, "category": "公司_專案"},
        {"id": "s2", "amount": 58796, "category": "公司_專案"},
    ]})
    total = bank_fee_total(rows, MSET)
    agency = agency_fee_total(rows, MSET)
    assert agency == 66960
    assert total == 66960 + 30          # 父列那 30 元真匯費還在
    assert total - agency == 30, "真匯費被代開費吃掉了"


def test_old_rows_without_the_tag_report_zero():
    """沒有拆項的收支一律 0 —— 這支對舊資料回 0，總額不受影響。"""
    assert agency_fee_total([_entry(bank_fee=30)], MSET) == 0
    assert bank_fee_total([_entry(bank_fee=30)], MSET) == 30


def test_both_display_ends_split_the_bucket():
    """損益摘要與 drilldown 明細**兩邊都要分**：只改一邊的話，點進去的合計
    跟表頭那一行對不上。"""
    summ = code_only(func_body(repo_src("core/finance_logic/_statements.py"),
                               "def build_pnl("))
    assert "agency = agency_fee_total(cash_entries, mset)" in summ
    assert "fee = bank_fee_total(cash_entries, mset) - agency" in summ
    assert "_AGENCY_FEE_LABEL, agency" in summ
    drill = repo_src("services/finance_statements.py")
    assert "agency = agency_fee_total(inputs[\"cash_entries\"], mset)" in drill
    assert 'bank_fee_total(inputs["cash_entries"], mset) - agency' in drill
    assert "發票代開費（收款時被扣，各筆合計）" in drill
