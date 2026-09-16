# -*- coding: utf-8 -*-
"""polish 階段零（2026-09-17，登記餘額＋月報那批）：把 diff 動到、但還沒有測試釘住的公開函式**現在的行為**釘住。
不判斷對錯，只釘輸入 → 輸出，之後的 review／simplify 改壞了會在這裡先叫。"""
from core.monthly_report import (_wan, compare, flow_block, fortress_block, ladder_fire_block, todo_block, totals_block)


def test_wan_formats_like_the_two_frontends():
    assert _wan(0) == "0 萬" and _wan(123_456) == "12.3 萬" and _wan(1_000_000) == "100 萬"
    assert _wan(-38_200) == "−3.8 萬" and _wan(150_000_000) == "1.5 億" and _wan(None) == "0 萬"


def test_totals_and_compare():
    t = totals_block({"銀行現金": 100, "證券現值": 200, "應收帳款": 30, "固定資產淨值": 5}, {"total": 7, "card": 7, "loan": 0})
    assert t == {"cash": 100, "securities": 200, "receivable": 30, "equipment": 5, "assets": 335,
                 "liabilities": 7, "card": 7, "loan": 0, "net_worth": 328, "financial": 300}
    assert totals_block({}, None)["assets"] == 0
    assert compare(t, None) == {k: None for k in t}
    d = compare(t, {"assets": 300, "cash": None})
    assert d["assets"] == 35 and d["cash"] is None and d["securities"] is None


def test_flow_block_register_gap_is_cash_delta_minus_booked_flow():
    """BUG-7（polish 2026-09-17）：「解釋不了的差額」改成 register_gap ＝ Δ現金 − 帳上真正的淨流（含匯費、請款）。"""
    d = {"assets": 1000, "cash": 250, "securities": 600, "receivable": 100, "equipment": 0}
    f = flow_block({"deposit": 500, "expense": 200, "household_expense": 80, "bank_net": 290}, d, through="2026-10-05")
    assert f["net"] == 300 and f["has_entries"] is True and f["savings_rate"] == 0.6 and f["through"] == "2026-10-05"
    assert f["register_gap"] == 250 - 290 and f["bank_net"] == 290
    g = flow_block({"deposit": 0, "expense": 0}, {"assets": 1000, "cash": None, "securities": None, "receivable": None})
    assert g["has_entries"] is False and g["savings_rate"] is None and g["register_gap"] is None
    assert flow_block({"deposit": 500, "expense": 200}, {"cash": 5})["register_gap"] is None, "沒有 bank_net 就不算"
    assert flow_block({"deposit": 0, "expense": 50}, {})["savings_rate"] is None, "沒收入不算存款率（不除以 0）"


def test_fortress_block_counts_states_and_sorts_layers_top_down():
    ft = fortress_block({"runway": {"months": 8.5, "tone": "a"}, "monthly_need": {"used": 90_000, "override": 90_000, "sample_months": 2},
                         "tests": [{"key": "a", "state": "ok"}, {"key": "b", "state": "bad"}, {"key": "c", "state": "weird"}],
                         "layers": [{"no": 1, "name": "x", "have": 5, "target": 10, "gap": 5, "pct": 50},
                                    {"no": 5, "name": "y", "have": 9, "target": None, "gap": None, "pct": 100}],
                         "earmark_total": 12})
    assert ft["counts"] == {"ok": 1, "warn": 0, "bad": 1, "na": 1}
    assert [L["no"] for L in ft["layers"]] == [5, 1] and ft["need_override"] is True and ft["runway"] == 8.5
    assert fortress_block({})["runway"] is None and fortress_block({})["layers"] == []


def test_ladder_fire_block_finds_next_rung_and_year_10():
    lf = ladder_fire_block({"ladder": {"rung": 2, "name": "b", "net_worth": 5_000_000, "to_next": 1,
                                       "rungs": [{"no": 1, "name": "a"}, {"no": 2, "name": "b"}, {"no": 3, "name": "c"}]},
                            "fire": {"allowed": 1, "spend": 2, "ratio33": 0.5, "state": "warn", "pretax": False},
                            "projection": {"rate": 0.06, "inflation": 0.02, "rows": [{"year": 5, "nominal": 1, "real": 1}, {"year": 10, "nominal": 20, "real": 15}]}})
    assert lf["next_name"] == "c" and lf["y10_nominal"] == 20 and lf["y10_real"] == 15 and lf["fire_pretax"] is False
    empty = ladder_fire_block({})
    assert empty["next_name"] is None and empty["y10_nominal"] is None and empty["rung"] is None


def test_todo_block_lists_unfilled_unregistered_plugs_and_urgent_advice():
    accounts = [{"name": "A", "unfilled": -100, "registered_this_month": True},
                {"name": "B", "unfilled": None, "registered_this_month": False},
                {"name": "C", "unfilled": 0, "registered_this_month": True}]
    brokers = [{"broker": "X", "plug": 50_000}, {"broker": "Y", "plug": 0}]
    advice = [{"level": "bad", "key": "war", "title": "紅。"}, {"level": "info", "key": "fire_tax", "title": "稅"},
              {"level": "warn", "key": "records", "title": "帳沒記齊"}]
    out = todo_block(accounts, brokers, advice)
    assert out[0].startswith("補明細：A") and "1 個帳戶" in out[1] and "B" in out[1]
    assert out[2] == "拆證券明細：X 5 萬" and out[3] == "紅" and len(out) == 4, "records 那條不重複進待辦（上面已列）"
