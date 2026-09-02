# -*- coding: utf-8 -*-
"""清理審查（2026-09-02）收斂掉的幾條「同一個規則長出第二份」。

每一條都是實際已經分岔、或差一步就會分岔的：規則寫在呼叫點 → 第二個顯示端
自己再寫一次 → 兩邊給出不同的答案，而錯的那一邊沒有人會發現。
"""
from core.crm_logic import payment_label, split_gross
from tests.unit._srcscan import js_code_only, js_func_body, repo_src


def test_one_payment_label_for_every_display_end():
    """🔴 一張請款單在哪裡都叫同一個名字。

    原本挑選視窗走 `payee_name or summary`、`resolve_allocs` 退回的 422 走
    `summary or payee_name` —— 同一次操作裡，你點的那張單跟錯誤訊息罵的那張
    單名字不一樣。
    """
    from routers.crm.finance import _ALLOC_KINDS

    class Req:
        payee_name, summary = "王士源", "和平論壇第三期款"

    assert _ALLOC_KINDS["payment"]["label"](Req()) == payment_label(
        Req.payee_name, Req.summary) == "王士源"
    # 沒有收款人才退回摘要
    assert payment_label("", "零用金請款") == "零用金請款"
    assert payment_label("", "") == ""


def test_the_split_gross_rule_has_one_home():
    """拆項對專案的貢獻＝淨額＋代開費。三個後端呼叫端都走 core 那一支。"""
    assert split_gross(291640, 66960) == 358600
    assert split_gross(5000) == 5000          # 沒代開費就是淨額
    assert split_gross(None, None) == 0
    for path in ("routers/crm/cash_splits.py", "routers/api_finance_projects.py"):
        assert "split_gross(" in repo_src(path), path


def test_money_that_was_redacted_is_not_rendered_as_settled():
    """🔴 `amount_receivable` 是 MONEY_FIELDS —— 沒授權的人拿到的是**鍵不存在**。

    `|| 0` 會把「你看不到」壓成「已收齊」：未收清單整個空掉，而「顯示全部」
    每一列都被標成收齊了。判準用 `'key' in obj`（core/money 檔頭那條）。
    """
    picker = js_code_only(repo_src("frontend/js/shared/project-picker.js"))
    assert "'amount_receivable' in p" in picker
    assert "dueOf(p) === null" in picker, "看不到金額時沒有畫成第三態"
    # 🔴 判準只有 `dueOf` 一份：呼叫端自己先 filter 一次未收，就是第二份
    # （而那一份漏掉三態的話，症狀是清單靜默少幾案）
    cash = js_code_only(repo_src("frontend/tabs/crm/crm-cashbook.js"))
    assert "amount_receivable" not in cash, "收支帳本又自己判了一次未收"


def test_the_withholding_rates_come_from_the_backend():
    """源頭代扣的費率是法定的（二代健保動過不只一次）—— 前端不寫死一份。

    寫死的那份改法後會讓預覽跟存進去的值不一致，而預覽值一旦被使用者確認
    就被 `tax_manual` 凍住。同 `fee_pct` 的處理。
    """
    js = js_code_only(repo_src("frontend/tabs/finance/subviews/projects.js"))
    body = js_func_body(js, "function _proTax(")
    for literal in ("0.10", "0.0211", "2000", "20000"):
        assert literal not in body, f"費率 {literal} 又寫死在前端了"
    assert "r.tax_pct" in body and "r.nhi_pct" in body
    assert '"withhold": {"tax_pct": WITHHOLD_TAX_PCT' in repo_src(
        "routers/api_finance_projects.py"), "後端沒把費率送出來"


def test_picking_never_rebuilds_the_list_the_user_is_scrolling():
    """🔴 勾一列不可以重建整份清單 —— 那會把捲軸彈回頂端。

    兩個地方都踩過同一顆：拆項編輯器勾未收案時走整窗 `render()`（重建兩個面板
    約 1,400 個節點），多選挑選視窗勾請款單時走 `redraw()`（重建最多 120 列）。
    使用者連勾三筆就被彈三次，勾到第 40 個案子時勾完找不到自己在哪。
    這條規則本來只寫在「金額輸入」上（patchGross 檔頭），勾選漏掉了。
    """
    ed = js_code_only(repo_src("frontend/js/shared/cash-split-editor.js"))
    # 勾選／新增／刪列都只換 #csp-rows 那一塊
    assert "function renderRows()" in ed
    assert "box.innerHTML = rowsHtml() || EMPTY_ROWS" in ed
    for fn in ("function bindProj(", "function bindAdv("):
        body = js_func_body(ed, fn)
        assert "renderRows();" in body, fn
        assert "\n                render();" not in body, f"{fn} 又整窗重畫了"
    # 🔴 面板不再跟著重畫 → 列上的 ✕ 要自己把對應的 checkbox 取消勾選
    assert "cb.checked = false;" in ed, "刪列之後面板還勾著，清單裡卻沒有它"

    pick = js_code_only(repo_src("frontend/js/shared/row-picker.js"))
    multi = pick.split("if (o.multi) {")[1].split("return;")[0]
    assert "redraw()" not in multi, "多選切換又重建了整份清單"
    assert "box.checked = on;" in multi
