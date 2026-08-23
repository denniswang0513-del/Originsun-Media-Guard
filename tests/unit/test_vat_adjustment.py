# -*- coding: utf-8 -*-
"""應付營業稅可以用一筆具名調整沖平（owner 2026-08-23）。

為什麼需要這個型別：`vat_payable` 是**推出來的** —— 銷項稅 − 進項稅 − 已繳。
生產 2024–2025 的代開發票只記了進項那側（進項 161 張、銷項 3 張），於是帳上
算出來的應納稅額遠低於實際繳出去的錢，`vat_payable` 變成 −714,431。

那個負數不是「政府欠你」，是帳的缺口。owner 的判斷是「2024-2025 我帳沒記好，
但是營業稅我們都有繳好」—— 發票補不回來了，所以用一筆有日期、有說明、看得到
的調整把那個年代沖平，而不是：
  · 讓報表一直掛著一個解釋不了的負數，或
  · 在顯示層把負數夾成 0（那會連真正的溢繳／留抵也一起看不見）。

🔴 vat 是唯一**不落權益**的 adj_type。它單邊加在負債上，因此會讓未對平差額
減少同額 —— 這是對的：那個負數本來就是差額的來源之一。
"""
from core.finance_logic import build_balance_sheet

ADJ = {"adj_date": "2026-07-31", "amount": 714431, "adj_type": "vat",
       "description": "2024–2025 代開銷項發票帳載不全；稅款已依實際申報繳清"}


def _bs(adjustments=(), vat=-714431, as_of="2026-07"):
    return build_balance_sheet(
        as_of, bank_lines=[{"id": "A", "name": "富邦", "amount": 865173}],
        receivable_total=990252, payable_total=173829, vat_payable=vat,
        adjustments=list(adjustments), cumulative_net=1251286)


def _line(bs, key):
    for x in bs["liabilities"]["current"] + bs["liabilities"]["noncurrent"]:
        if x["key"] == key:
            return x["amount"]
    raise AssertionError(f"找不到負債列 {key}")


def _equity(bs, key):
    for x in bs["equity"]["lines"]:
        if x["key"] == key:
            return x["amount"]
    raise AssertionError(f"找不到權益列 {key}")


def test_vat_adjustment_lands_on_the_vat_line():
    """加在應付營業稅上 —— 這是它跟其他所有 adj_type 的差別。"""
    assert _line(_bs(), "vat_payable") == -714431, "沒調整時應該還是那個負數"
    assert _line(_bs([ADJ]), "vat_payable") == 0


def test_vat_adjustment_does_not_touch_equity():
    """🔴 它不是「其他調整」。掉進權益的話：負債仍是負的（畫面照樣難看），
    而且會被誤讀成一筆盈餘。"""
    bs = _bs([ADJ])
    assert _equity(bs, "adjustments") == 0, "vat 調整跑進其他調整了"
    assert bs["equity"]["total"] == 1251286, "權益總計被 vat 調整動到"


def test_vat_adjustment_closes_the_gap_by_exactly_its_amount():
    """單邊入帳 → 未對平差額同額減少。那個負數本來就是差額的來源之一。"""
    before = _bs()["check"]["diff"]
    after = _bs([ADJ])["check"]["diff"]
    assert before - after == 714431, f"{before:,} → {after:,}"


def test_vat_adjustment_respects_as_of():
    """調整開在 2026-07 → 看 2026-06 的報表時不該生效（那個月還沒沖）。"""
    assert _line(_bs([ADJ], as_of="2026-06"), "vat_payable") == -714431
    assert _line(_bs([ADJ], as_of="2026-08"), "vat_payable") == 0


def test_later_vat_activity_still_accrues_on_top():
    """沖平的是**那個年代**，不是把這條線釘死在 0 —— 之後的稅要照常累計。"""
    assert _line(_bs([ADJ], vat=-714431 + 95442), "vat_payable") == 95442


def test_other_adjustment_types_still_go_to_equity():
    """回歸：別的型別的落點不能被這次改動搬走。"""
    def one(t, key, amt=1000):
        return _equity(_bs([{"adj_date": "2026-07-01", "amount": amt,
                             "adj_type": t, "description": "x"}]), key)
    assert one("opening", "opening") == 1000
    assert one("owner_in", "owner") == 1000
    assert one("owner_out", "owner") == -1000
    assert one("accountant", "adjustments") == 1000
    assert one("correction", "adjustments") == 1000


def test_vat_is_accepted_by_the_write_path():
    """值域三處要一致 —— 後端擋掉的話，這條規則在 UI 上根本用不到。"""
    from routers.api_finance import ADJ_TYPES
    assert "vat" in ADJ_TYPES
    from tests.unit._srcscan import js_code_only, repo_src
    js = js_code_only(repo_src("frontend/tabs/finance/subviews/banking.js"))
    assert "v: 'vat'" in js, "帳務調整的下拉沒有這個選項（後端收得下，人選不到）"
