# -*- coding: utf-8 -*-
"""格子欄寬可拖（owner 2026-09-07「填寫的格子都可以自己拉動寬度」）：表頭右緣拖曳、localStorage 記住、點兩下恢復預設。
只住 ts-sheet.js 一份（員工頁今天的專案紀錄、工作追蹤我的一天、手機格子都吃）。"""
from tests.unit._srcscan import js_code_only, js_func_body, repo_src


def test_every_editable_column_has_a_resize_handle_and_widths_persist():
    src = repo_src("frontend/js/shared/ts-sheet.js")
    assert "k === 'state' ? '' : '<span class=\"ts-sheet-rz\"" in src, "狀態欄不拖，其餘每欄一個把手"
    assert "cursor:col-resize" in src and "position:relative" in src
    js = js_code_only(src)
    assert "const COLW_KEY = 'ts_sheet_colw'" in js
    assert "_applyColWidths(host);" in js_func_body(js, "export function renderSheet(host, rows, opts = {})"), "重畫要套回記住的寬度"
    start = js_func_body(js, "function _startColResize(ev, host)")
    assert "Math.max(40," in start and "_saveColWidths(w)" in start and "window.removeEventListener('pointermove', move)" in start
    assert "delete w[th.dataset.col]" in js_func_body(js, "function _resetColWidth(ev, host)")
    wire = js_func_body(js, "function _wire(host)")
    assert "_startColResize(ev, host)" in wire and "_resetColWidth(ev, host)" in wire
