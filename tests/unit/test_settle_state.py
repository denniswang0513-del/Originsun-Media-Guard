# -*- coding: utf-8 -*-
"""收付狀態（owner 2026-08-29 選 C 案）：結案＝一個記號，未結才分開標收／付。"""
from core.ledger_project import SETTLE_FILTERS, settle_match, settle_state
from tests.unit._srcscan import js_func_body



def test_done_collapses_when_both_sides_are_finished():
    """owner：「如果該收該付都完成，我希望變成一個色塊是結案」。
    沒有要付的那種也算結案 —— 那不是「還沒付」。"""
    assert settle_state(0, 0, 5000)["done"] is True      # 收齊＋付清
    assert settle_state(0, 0, 0)["done"] is True         # 收齊＋本來就沒要付的
    assert settle_state(100, 0, 0)["done"] is False      # 還沒收完
    assert settle_state(0, 100, 5000)["done"] is False   # 還沒付完


def test_each_side_reports_its_own_state():
    s = settle_state(5796, 504, 504)
    assert (s["in"], s["out"]) == ("wait", "wait")
    s = settle_state(0, 12000, 18300)
    assert (s["in"], s["out"]) == ("ok", "wait")


def test_nothing_to_pay_is_none_not_paid():
    """🔴 `應付 = 0` 是「這案沒有要付的」，不是「付清了」——
    畫面上那個字不畫出來，畫一個灰「付」會讓人以為還沒付。"""
    assert settle_state(0, 0, 0)["out"] == "none"
    assert settle_state(0, 0, 700)["out"] == "ok"


def test_overcollection_has_its_own_state():
    """溢收不是「收齊」也不是「待收」—— 它是要人去看的異常
    （生產 1 案：工程空拍，營收 0、已收 3,000）。"""
    s = settle_state(-3000, 0, 0)
    assert s["in"] == "over" and s["done"] is False


def test_filter_and_column_share_one_ruling():
    """篩選走 settle_match，跟欄位同一份 `settle` —— 前端自己再判一次，
    篩出來的和看到的就會不一樣。"""
    assert set(SETTLE_FILTERS) == {"done", "in", "out", "either", "over"}
    done, wait_in, wait_out = (settle_state(0, 0, 100), settle_state(5, 0, 0),
                              settle_state(0, 5, 100))
    assert settle_match(done, "") and settle_match(wait_in, "")
    assert settle_match(done, "done") and not settle_match(wait_in, "done")
    assert settle_match(wait_in, "in") and not settle_match(wait_out, "in")
    assert settle_match(wait_out, "out") and not settle_match(wait_in, "out")
    assert settle_match(wait_in, "either") and settle_match(wait_out, "either")
    assert not settle_match(done, "either")
    assert settle_match(settle_state(-1, 0, 0), "over")


def test_frontend_renders_one_char_when_done():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2]
          / "frontend/tabs/finance/subviews/projects.js").read_text(encoding="utf-8")
    fn = js_func_body(js, "function _stHtml(")
    assert "st.done ? ch('結'" in fn, "結案要收成一個字"
    assert "_ST_OUT[st.out] && ch" in fn, "沒有要付的那格不畫"
    assert "_unpaidOnly" not in js, "舊的「只看未收清」要被狀態下拉取代，不並存"
