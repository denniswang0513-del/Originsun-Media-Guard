# -*- coding: utf-8 -*-
"""每人額度（年度活動）的純規則 —— docs/BENEFIT_POOL_PLAN.md §9.4。

> owner 2026-08-21：「我想要有一個像是活動，然後下面有好幾個人的額度與
>   有效期間，然後有一些活動說明。」

跟共用池的差別只有兩條，但兩條都是會出事的：

  🔴 超額要**擋**（共用池只轉紅不擋）。個人額度超額是「這張券本來就沒這麼多」，
     放過去只是把問題推到請款那一關才爆。
  🔴 期間外的登記也要擋。券寫 2026/01/01–12/31，2027 年拿去用不算數。
"""
from datetime import date

import pytest

from core.hr_logic import (in_window, pool_allowance_rollup,
                           staff_allowance_balance, validate_against_allowance)

D = date


# ── in_window ────────────────────────────────────────────────────

def test_window_includes_both_ends():
    """券寫 2026/01/01–12/31，那兩天當然算數。"""
    lo, hi = D(2026, 1, 1), D(2026, 12, 31)
    assert in_window(lo, lo, hi) is True
    assert in_window(hi, lo, hi) is True


def test_window_excludes_outside():
    lo, hi = D(2026, 1, 1), D(2026, 12, 31)
    assert in_window(D(2025, 12, 31), lo, hi) is False
    assert in_window(D(2027, 1, 1), lo, hi) is False


def test_open_ended_window():
    """留空＝不限。共用池就是兩邊都留空。"""
    assert in_window(D(2020, 5, 5), None, None) is True
    assert in_window(D(2020, 5, 5), D(2026, 1, 1), None) is False
    assert in_window(D(2030, 5, 5), D(2026, 1, 1), None) is True
    assert in_window(D(2030, 5, 5), None, D(2026, 12, 31)) is False


def test_no_day_is_not_in_any_window():
    """沒有日期不能當成「在期間內」—— 那等於預設放行。"""
    assert in_window(None, D(2026, 1, 1), D(2026, 12, 31)) is False
    assert in_window(None, None, None) is False


# ── 個人用量 ─────────────────────────────────────────────────────

def test_only_committed_counts_as_used():
    """跟共用池同一條規則：待審不扣，另外顯示。"""
    r = staff_allowance_balance(10000, [
        ("已付款", 3000, True), ("已核准", 2000, True),
        ("待審", 500, True), ("退回", 9999, True),
    ])
    assert r["used"] == 5000
    assert r["pending"] == 500
    assert r["balance"] == 5000
    assert r["over"] is False


def test_out_of_window_entries_are_not_counted():
    """期間外的本來就不該被核准 —— 真的混進來也不吃額度。"""
    r = staff_allowance_balance(10000, [
        ("已付款", 3000, True), ("已付款", 7000, False),
    ])
    assert r["used"] == 3000, "期間外的被算進去了"


def test_over_quota_shows_negative_not_clamped():
    """真的超了就顯示負的（同共用池：夾成 0 等於把問題藏起來）。"""
    r = staff_allowance_balance(1000, [("已付款", 1500, True)])
    assert r["balance"] == -500
    assert r["over"] is True


def test_empty_allowance():
    r = staff_allowance_balance(0, [])
    assert r == {"quota": 0, "used": 0, "pending": 0, "balance": 0,
                 "available": 0, "over": False}


def test_pending_occupies_my_own_quota():
    """🔴 跟共用池不同：**待審也佔住**自己的額度。

    共用池「待審不扣」是對的（桶子大、退件不用回沖）。但個人額度只有
    10,000 時，那條規則會讓同一個人連送三筆 10,000 全部待審都不被擋 ——
    擋超額形同虛設，等 owner 一核准就爆了。這筆是他自己的待審、佔的是他
    自己的額度，退回就放回來，不牽涉別人。
    """
    r = staff_allowance_balance(10000, [
        ("已付款", 3000, True), ("待審", 4000, True)])
    assert r["balance"] == 7000, "顯示用的餘額仍然是 額度 − 已用"
    assert r["available"] == 3000, "可再申請的要把待審也扣掉"
    # 送 3,001 就該被擋
    al = {"amount": 10000, "valid_from": None, "valid_to": None,
          "available": r["available"]}
    assert validate_against_allowance(3000, D(2026, 5, 1), al) == ""
    assert validate_against_allowance(3001, D(2026, 5, 1), al) != ""


def test_rejected_pending_frees_the_quota_again():
    """退回之後那筆就不再佔額度（沒有回沖問題 —— 它一直只屬於這個人）。"""
    r = staff_allowance_balance(10000, [
        ("已付款", 3000, True), ("退回", 4000, True)])
    assert r["available"] == 7000


# ── 活動層的配發概況 ─────────────────────────────────────────────

def test_rollup_counts_untouched_people():
    """券發下去沒人用才是問題 —— owner 要看得到還有幾個人沒動。"""
    r = pool_allowance_rollup([10000, 10000, 10000], [0, 4200, 0])
    assert r["granted"] == 30000
    assert r["used"] == 4200
    assert r["balance"] == 25800
    assert r["people"] == 3
    assert r["untouched"] == 2


def test_rollup_of_nobody():
    r = pool_allowance_rollup([], [])
    assert r["granted"] == 0 and r["people"] == 0 and r["untouched"] == 0


# ── 登記時的守衛 ─────────────────────────────────────────────────

def _al(available=10000, lo=D(2026, 1, 1), hi=D(2026, 12, 31)):
    return {"amount": 10000, "valid_from": lo, "valid_to": hi,
            "available": available}


def test_no_allowance_is_refused():
    """🔴 沒發給他就是沒有 —— 不能因為活動存在就放行。"""
    err = validate_against_allowance(100, D(2026, 5, 1), None)
    assert err and "沒有發給你" in err.replace("沒有發給你額度", "沒有發給你")


def test_within_quota_and_window_passes():
    assert validate_against_allowance(3000, D(2026, 5, 1), _al()) == ""


def test_exactly_the_remaining_balance_passes():
    """剛好用完不是超額。差一個等號就會讓最後一筆永遠送不出去。"""
    assert validate_against_allowance(2500, D(2026, 5, 1), _al(available=2500)) == ""


def test_one_dollar_over_is_refused():
    err = validate_against_allowance(2501, D(2026, 5, 1), _al(available=2500))
    assert err and "額度" in err
    assert "2,500" in err, "沒告訴他還剩多少，等於只說『不行』"


def test_out_of_window_is_refused():
    err = validate_against_allowance(100, D(2027, 1, 1), _al())
    assert err and "期間" in err
    assert "2026-01-01" in err and "2026-12-31" in err, "沒告訴他期間是什麼"


def test_window_is_checked_before_quota():
    """兩個都錯時先講期間 —— 日期填錯是更根本的問題，先講額度會讓人
    以為改小金額就能過。"""
    err = validate_against_allowance(999999, D(2027, 1, 1), _al())
    assert "期間" in err and "額度" not in err


@pytest.mark.parametrize("bad", [None, 0, "", "abc"])
def test_broken_available_is_treated_as_zero_not_unlimited(bad):
    """🔴 壞掉的餘額要當成 0（擋下來），不能當成無限（放行）。"""
    al = {"amount": 1, "valid_from": None, "valid_to": None, "available": bad}
    if bad in (None, 0, ""):
        assert validate_against_allowance(1, D(2026, 5, 1), al) != ""
    else:
        with pytest.raises((ValueError, TypeError)):
            validate_against_allowance(1, D(2026, 5, 1), al)
