# -*- coding: utf-8 -*-
"""scripts/_common — 五支匯入腳本共用的金額/日期正規化（純函式）。

這兩支原本各有五份複本、已經漂出四種語意；統一時靠讀 diff 才抓到
import_my_equipment 少了會計括號那支（`(1,004)` 靜默變 0，正負顛倒還不出聲）。
讀出來的東西要有測試扛著，否則下次分岔一樣只能靠讀。
"""
import pytest

from scripts._common import money, parse_date


@pytest.mark.parametrize("raw,expect", [
    ("NT$ 1,004", 1004),
    ("NT$ (1,004)", -1004),      # 🔴 會計式括號＝負數（import_my_equipment 就死在這一格）
    ("（1,004）", -1004),          # 全形括號先換半形
    ("1,234.6", 1235),           # 四捨五入到元
    ("", 0),
    ("#REF!", 0),
    ("看不懂", 0),
])
def test_money_default_semantics(raw, expect):
    assert money(raw) == expect


@pytest.mark.parametrize("raw,expect", [("", None), ("#REF!", None), ("0", 0), ("500", 500)])
def test_money_none_on_bad_distinguishes_missing_from_zero(raw, expect):
    """快照要能區分「這個桶當天沒有值」與「這個桶是 0」——回 0 會讓斷鏈的格子
    被當成真的歸零、畫進趨勢圖。"""
    assert money(raw, none_on_bad=True) == expect


def test_money_usd_flag_is_required_for_dollar_cells():
    """只有私帳總資產表混了外幣欄，所以 usd 是旗標不是預設 —— 沒開就是 0，
    這個差異刻意留在呼叫端看得見的地方。"""
    assert money("US$ 1,234.6", usd=True) == 1235
    assert money("US$ 1,234.6") == 0


def test_parse_date_is_utc_midnight():
    """🔴 UTC 午夜是這個 repo 的日期慣例 —— 造成台北午夜的話前端顯示少一天
    （2026-08-19 第一版匯入 394 筆全中）。"""
    d = parse_date("2024/01/02")
    assert d.isoformat().startswith("2024-01-02T00:00:00+00:00")
    assert parse_date("2024-1-2") == d       # 兩種分隔符同一個結果


def test_parse_date_minguo_only_when_asked():
    """`0114/12/21` 開了 minguo 是民國 114 年；沒開就是打錯字，要回 None 進
    「跳過」清單讓人看見 —— 而不是造出一個西元 114 年的日期。"""
    assert parse_date("0114/12/21", minguo=True).isoformat().startswith("2025-12-21")
    assert parse_date("0114/12/21") is None


@pytest.mark.parametrize("bad", ["", "abc", "2024/13/01x", "2024/1", "0114/12/21"])
def test_parse_date_bad_values_are_none(bad):
    assert parse_date(bad) is None
