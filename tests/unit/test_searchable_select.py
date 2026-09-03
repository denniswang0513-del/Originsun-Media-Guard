# -*- coding: utf-8 -*-
"""可打字下拉（crm-utils.searchableSelect）的兩條規則（owner 2026-09-04「搜尋沒有填東西他就卡住了」）：
把字刪光＝取消篩選（底層 <select> 一起清、發 change）；打到一半離開＝文字對回真的選著的值。
原本只過濾清單，底層值留著，畫面空白但清單一直被篩住 —— 專案、客戶、人員、收支明細…每個用它的頁都一樣。"""
from tests.unit._srcscan import js_code_only, js_func_body, repo_src


def test_clearing_text_clears_the_underlying_select_and_blur_resyncs():
    fn = js_func_body(js_code_only(repo_src("frontend/tabs/crm/crm-utils.js")), "export function searchableSelect(sel, opts = {}) {")
    assert "if (!input.value.trim() && sel.value) _pick('', '');" in fn
    blur = fn[fn.index("input.addEventListener('blur'"):]
    blur = blur[:blur.index("input.addEventListener('keydown'")]
    assert "input.value = (o && o.value) ? o.textContent : '';" in blur
