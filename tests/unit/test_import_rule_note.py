# -*- coding: utf-8 -*-
"""規則記憶備註（owner 2026-09-01「對帳單的規則可以記到第四層，也可以記憶備註」）。

分類已經自動了，備註還是每個月手打同一句 —— 每月房租、每月那筆貸款轉帳，
摘要一模一樣。備註跟分類一樣是「這串摘要代表什麼」的一部分，該一起記在規則裡。
"""
from core.bank_statement import RuleHit, _classify, parse_statement
from tests.unit._srcscan import (code_only, func_body, js_code_only,
                                 js_func_body, repo_src)


def test_a_rule_can_carry_a_note_and_classify_returns_it():
    rules = [("房租", "家用_固定支出", 0, 0, "node-4", "中山分行辦公室房租")]
    hit = _classify("網路跨轉 房租中山分行", rules, signed=-47015)
    assert hit.category == "家用_固定支出"
    assert hit.node == "node-4"          # 第四層節點原封帶出來
    assert hit.note == "中山分行辦公室房租"


def test_short_rules_still_work():
    """既有的三元組規則（KEYWORD_RULES 那份、測試裡手寫的那些）不受影響 ——
    沒帶節點或備註就是空字串，不是 IndexError。"""
    assert _classify("攤還本息", [("攤還本息", "貸款繳款", 0)]) == RuleHit("貸款繳款")


def test_the_note_reaches_the_preview_row():
    """規則記的備註要一路帶到預覽列上，人才看得到、才改得動。

    🔴 `StmtRow.note` 早就是**銀行摘要原文**了 —— 備註用 `entry_note`，
    同名會讓兩者靜靜互相蓋掉（畫面上看起來只是「摘要怪怪的」）。
    """
    stmt = ("2026/06/19  2026/06/19 10:00:00  網路跨轉  47,015.00        1,000,000.00  房租中山分行\n"
            "2026/06/20  2026/06/20 10:00:00  利息            8,815.00  1,008,815.00  總行\n")
    res = parse_statement(stmt, rules=[("房租", "家用_固定支出", 0, 0, "", "六月房租")])
    hit = [r for r in res.rows if "房租" in r.note]
    assert hit, res.errors or res.rows
    assert hit[0].entry_note == "六月房租"
    assert "房租" in hit[0].note          # 摘要原文沒有被備註蓋掉


def test_import_writes_the_rule_note_and_falls_back_to_the_stock_line():
    """匯入建列時用規則帶來的備註；沒有才填制式那句。

    制式字樣**只有一份常數** —— 重分類那條路要靠它判斷「這句不是人寫的」，
    字串各寫一份就會判錯。"""
    src = repo_src("routers/api_finance_stmt.py")
    assert 'STMT_IMPORT_NOTE = "銀行對帳單匯入"' in src
    body = code_only(src)
    assert 'note=(r.note or "").strip()[:255] or STMT_IMPORT_NOTE' in body
    # 制式字樣不可以再有第二份字面值（`created_by` 那個是草稿的建立者，不是備註）
    assert body.count('"銀行對帳單匯入"') == 2, \
        "制式備註字樣散成多份了 —— 重分類那條路會判不出「這句不是人寫的」"


def test_reclassifying_old_rows_never_overwrites_a_human_note():
    """🔴「套用到未歸類的歷史列」會掃幾百列既有收支。備註直接覆蓋下去就是
    靜默毀資料 —— 人手寫的那句沒有任何地方留副本。只補空的。"""
    body = code_only(func_body(repo_src("routers/api_finance_stmt.py"),
                               "async def apply_rules_to_unclassified("))
    assert 'if hit.note and (e.note or "").strip() in ("", STMT_IMPORT_NOTE):' in body
    assert "e.note = hit.note" in body


def test_the_rule_list_shows_the_whole_path_not_just_the_category():
    """🔴 清單只印 `r.category` 的話，一條指到第四層的規則長得跟只到第二層的
    一模一樣（category 是路徑前兩層的鏡射）—— 使用者看到的就是「規則記不住
    第三層以後」，而其實早就存進去了。"""
    js = repo_src("frontend/tabs/finance/subviews/recon.js")
    render = js_code_only(js_func_body(js, "function _rulesRender("))
    assert "_rulePathText(r)" in render
    assert "esc(r.category)" not in render, "還在只印 category"
    path = js_code_only(js_func_body(js, "function _rulePathText("))
    assert "_ruleById[r.taxonomy_node_id]" in path


def test_the_rule_form_can_pick_any_depth():
    """新增規則的「歸到類別」在私帳要是**樹**。平面下拉只到 category，
    第三層以後的規則從這個面板永遠建不出來（只能從對帳單那一列的「＋規則」
    繞進去）—— 那正是 owner 說「記不到第四層」的地方。"""
    js = repo_src("frontend/tabs/finance/subviews/recon.js")
    render = js_code_only(js_func_body(js, "function _rulesRender("))
    assert "rule-cat-box" in render
    into = js_code_only(js_func_body(js, "function _ruleTaxInto("))
    assert "taxSelects(" in into          # 深度不設限的共用挑選器，不自己寫層數
    add = js_code_only(js_func_body(js, "_fr.ruleAdd = async (btn) => {"))
    assert "taxonomy_node_id: node" in add and "apply_note:" in add
