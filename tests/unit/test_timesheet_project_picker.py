# -*- coding: utf-8 -*-
"""工作日誌的專案下拉分「進行中（預設）／已結案」（owner 2026-09-03）。

分組規則住後端（project_options 每筆帶 closed），前端只看旗標；原生 datalist 分不了組，
timesheets.js 用自己的浮層（.ts-proj-pop）；浮層開著時 ↓↑ 歸浮層（capture），關著時才是列的上下移動。
"""
from tests.unit._srcscan import code_only, func_body, js_code_only, repo_src


def test_backend_options_carry_closed_flag():
    body = code_only(func_body(repo_src("services/timesheet_manual.py"), "async def project_options("))
    assert '"closed": st == "結案"' in body
    assert '"closed": False' in body, "最近填過（沒 id）的也要帶旗標，前端不用猜"


def test_frontend_uses_grouped_popover_not_datalist():
    js = js_code_only(repo_src("frontend/tabs/timesheets/timesheets.js"))
    assert 'list="ts-proj-list"' not in js and "<datalist" not in js, "專案格不再用 datalist（分不了組）"
    assert js.count("data-proj-pick") >= 3, "我的一天、總表改列、批次調整三處都要掛浮層"
    assert "進行中（${active.length}）" in js and "已結案（${closed.length}）" in js
    assert "p.closed" in js, "分組只看後端旗標"
    assert "_bindProjectPop(_content);" in js and "_content.addEventListener('keydown', _sheetKeydown);" in js
    assert js.index("_bindProjectPop(_content);") < js.index("_content.addEventListener('keydown', _sheetKeydown);"), "浮層要先綁（capture）"
    assert "root.addEventListener('keydown', _projPopKeydown, true);" in js
    assert "ev.stopPropagation();" in code_only(func_body(js, "function _projPopKeydown(ev)"))
    html = repo_src("frontend/tabs/timesheets/timesheets.html")
    assert ".ts-proj-pop" in html and ".ts-pp-toggle" in html
