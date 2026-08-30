# -*- coding: utf-8 -*-
"""證券投資頁的圖表（owner 2026-08-29「優化視覺呈現，配色不調整，圖表形式如參考圖」）。

圖是 SVG 字串（js/shared/svg-charts.js 純函式），這裡用掃描式斷言釘住幾條
**用眼睛才看得出來、但看過一次就不想再看第二次**的規則。
"""
from tests.unit._srcscan import (func_body, js_code_only, js_func_body,
                                 repo_src)

CHARTS = "frontend/js/shared/svg-charts.js"
PAGE = "frontend/tabs/finance/subviews/securities.js"


def _fn(rel, header):
    return js_code_only(func_body(repo_src(rel), header))


def test_zero_line_is_only_centred_when_there_are_both_signs():
    """🔴 全正（或全負）時零線不置中 —— 置中等於把一半寬度讓給永遠空著的那側，
    條只剩一半長，小額那幾筆細到看不見。owner 的報酬率實帳就是全正的。"""
    fn = _fn(CHARTS, "export function divergingBars(")
    assert "const both = hasPos && hasNeg;" in fn
    assert "both ? span / 2 : span" in fn
    assert "both ? left + span / 2 :" in fn


def test_grouped_bars_leave_room_for_the_value_labels():
    """條的最大長度要扣掉數值欄，否則最長那列會頂到右邊、跟數字疊在一起
    （2026-08-29 第一版截圖上富邦／盈透兩列就是這樣）。"""
    fn = _fn(CHARTS, "export function groupedBars(")
    assert "W - barLeft - valueW - 12" in fn


def test_tiny_but_nonzero_shares_are_not_printed_as_zero_percent():
    """非 0 卻四捨五入成 0% 要標 <0.1% —— 印「0%」會讓一筆真的有錢的部位
    看起來是空的（美國匯豐 24,996 / 9,369 萬 ＝ 0.027%）。"""
    fn = _fn(CHARTS, "export function hbars(")
    assert "'<0.1%'" in fn and "pctR === 0 && pct !== 0" in fn


def test_allocation_is_bars_not_a_pie():
    """🔴 配置用單色橫條、不是圓餅／甜甜圈：實帳前兩檔 47% vs 42.9%（圓餅上
    分不出誰大）、後兩檔 1.6% 與 0.03%（圓餅上根本看不到）。參考圖是甜甜圈，
    但那份資料的形狀不是這樣。"""
    js = js_code_only(repo_src(PAGE))
    assert "hbars(alloc" in js
    for banned in ("donut", "pie", "arc(", "stroke-dasharray"):
        assert banned not in js, banned


def test_charts_read_from_the_same_rows_as_the_table():
    """四張圖與下面的表格是同一份 `rows` 推導出來的 —— 另外取一次數，
    篩選（連現金／保險一起看）一切換，圖和表就會各說各話。"""
    fn = js_func_body(js_code_only(repo_src(PAGE)), "function _draw(")
    for name in ("const alloc =", "const costVsValue =", "const roiRows =",
                 "const pnlRows ="):
        assert name in fn, name
    assert "finFetch(" not in fn, "圖表不可以自己再打一次 API"


def test_no_fake_trend_line():
    """🔴 沒有折線圖：finance_net_snapshots 是**整份淨值**（含現金／不動產／
    器材）的快照，不是證券部位的歷史。畫在這頁會是一張標錯的圖。"""
    js = js_code_only(repo_src(PAGE))
    assert "lineChart" not in js and "snapshots" not in js


def test_page_has_no_emoji_outside_the_top_level_tab():
    """無 emoji 鐵則（owner 2026-07-17）：標題與按鈕一律純文字。
    側欄 finance.html 的舊標籤不回溯，但這一頁自己的字要乾淨。"""
    js = repo_src(PAGE)
    body = js.split("*/", 1)[1]          # 檔頭註解裡有引用 owner 原句，不算
    assert "📈" not in body
