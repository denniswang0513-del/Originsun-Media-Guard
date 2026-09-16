# -*- coding: utf-8 -*-
"""桌機「堡壘」子視圖（docs/FORTRESS_PLAN.md §3）：nav 入口與 fortress.js 的契約掃原始碼。"""
import re

from tests.unit._srcscan import js_code_only, js_func_body, repo_src

JS = "frontend/tabs/finance/subviews/fortress.js"


def test_nav_button_is_mine_only():
    html = repo_src("frontend/tabs/finance/finance.html")
    m = re.search(r'<button class="([^"]*)" data-subview="fortress">', html)
    assert m, "finance.html 要有 data-subview=fortress 的 nav 鈕"
    classes = m.group(1).split()
    assert "finance-nav-btn" in classes
    assert "fin-nav-mine-ok" in classes and "fin-nav-mine-only" in classes, "只在私帳出現（母公司帳沒有堡壘）"


def test_subview_entry_and_endpoints():
    js = js_code_only(repo_src(JS))
    assert "export default async function render(" in js, "createSubviewLoader 要 default export 的 render"
    assert "finFetchMine('/fortress')" in js, "整頁一趟讀 GET /finance/fortress（固定私帳）"
    assert "'/fortress/settings'" in js, "分層／目標／必要支出／戰爭假設都走 PUT /fortress/settings"
    assert "'/fortress/earmarks'" in js and "`/fortress/earmarks/${" in js, "預留清單 POST／PUT／DELETE"
    assert "window._finFortress" in js


def test_imports_only_from_shared_utils():
    js = js_code_only(repo_src(JS))
    for m in re.finditer(r"^\s*import\s.*?from\s+'([^']+)'", js, re.M):
        assert m.group(1) in ("../fin-utils.js", "../../crm/crm-utils.js"), f"不得 import 其他子視圖：{m.group(1)}"


def test_delete_asks_confirm():
    js = js_code_only(repo_src(JS))
    body = js_func_body(js, "_ff.delEarmark = async (id, btn) => {")
    assert "confirm(" in body, "刪預留要先問"
    assert "method: 'DELETE'" in body


def test_write_paths_rerender_from_payload():
    """每次寫入都用回傳的整份 payload 重畫，前端不自己重算（規劃 §0）。"""
    js = js_code_only(repo_src(JS))
    put = js_func_body(js, "async function _put(path, body, okMsg, btn) {")
    assert "method: 'PUT'" in put and "_d = await finFetchMine(" in put and "_render()" in put
    assert "monthly_need_override: 0" in js, "「用自動」送 0 回到自動平均"
    war = js_func_body(js, "_ff.saveWar = (btn) => {")
    assert "/ 100" in war and "fx: 1 +" in war, "戰爭假設送小數（60% → 0.6；貶 30% → fx 1.3）"
