# -*- coding: utf-8 -*-
"""行政雜支送請款（owner 2026-09-02「crm 系統裡面的雜支，可以送請款進請款單」）。

一列雜支 → 一張請款單，硬連結記在請款單的 `expense_id` 上。

🔴 為什麼是硬連結不是人名＋金額目測：同一案同金額的雜支很常見（owner 截圖裡
8/10 那趟的住宿 4,731／飲食 2,794／交通 2,442 就是同一趟），目測分不出誰請過。
`cost_line_id`（人員費用）與 `expense_id`（行政雜支）是同一條規則的兩半 ——
兩張不同的表，所以兩條硬連結各自一欄。
"""
from tests.unit._srcscan import (code_only, func_body, js_code_only,
                                 js_func_body, repo_src)

COST_JS = "frontend/tabs/crm/crm-projects-cost.js"
FIN_JS = "frontend/tabs/crm/crm-projects-finance.js"


def test_the_backend_already_guards_double_claiming():
    """🔴 重複請款由後端擋，不是靠前端把按鈕換掉 —— 換按鈕擋不住雙擊、
    兩個分頁、或重送。兩條硬連結走**同一段**守衛，各寫一次必漏一個。"""
    fn = code_only(func_body(repo_src("routers/crm/payments.py"),
                             "async def create_payment("))
    assert "CrmPaymentRequest.cost_line_id, req.cost_line_id" in fn
    assert "CrmPaymentRequest.expense_id, req.expense_id" in fn
    assert "這一行已經請過款了" in fn


def test_the_list_endpoint_reports_the_hard_link_not_a_guess():
    """畫面要知道「這一列請過款沒」—— 走 `expense_id` 反查，不是比人名＋金額。
    而且只撈這批列的（同 project_names_map 的理由，不要跟著整個專案長）。"""
    fn = code_only(func_body(repo_src("routers/crm/costs.py"),
                             "async def list_project_expenses("))
    assert "CrmPaymentRequest.expense_id.in_(eids)" in fn
    assert '"payment_id": claims.get(e.id' in fn
    assert '"payment_status": claims.get(e.id' in fn


def test_petty_rows_do_not_get_a_second_claim_path():
    """🔴 零用金流進來的列（`staff_id`）**不給這顆鈕**。

    那些錢已經有自己的一條請款路（零用金批次 → 依「會計項目×月份」開應付款，
    見 routers/crm/petty._build_aps）。兩條路都走＝同一筆錢請兩次，而後端的
    409 守的是 `expense_id` 重複，擋不到「零用金那側另外開了一張」。
    """
    js = js_code_only(repo_src(COST_JS))
    seg = js_func_body(js, "const claimCell = (e) => {")
    assert "if (e.staff_id) return '';" in seg, "零用金列也長出請款鈕了"
    # 已請款的列顯示狀態、點得開那張單，而不是再給一顆鈕
    assert "e.payment_id" in seg and "_costViewPayment" in seg


def test_the_claim_reuses_the_existing_payment_modal():
    """🔴 複用人員費用那顆的 modal，不另刻一份表單 —— 那支已經處理了代墊
    （收款人換成代墊人、費用歸屬留原人）、報支項目、預計付款月與必填檢查。
    再刻一份的話，「代墊怎麼記」就會有兩條規則。"""
    js = js_code_only(repo_src(COST_JS))
    fn = js_func_body(js, "window._expCreatePayment = function(expenseId) {")
    assert "window._costCreatePayment(" in fn
    assert "expenseId: e.id" in fn, "沒帶硬連結，這張單就釘不住是哪一行"
    # 表單自己不 POST —— 只有 modal 那一支在建單
    assert "_fetch('/payments'" not in fn and "crmFetch" not in fn

    # modal 那側要把硬連結送出去
    body = js_func_body(js_code_only(repo_src(FIN_JS)),
                        "window._costCreatePayment = function(payeeName, amount, "
                        "summary, status, advanced, opts) {")
    assert "expense_id: opts.expenseId || ''," in body


def test_claiming_an_expense_redraws_the_expense_section():
    """建完單要重畫**雜支那一區**（按鈕才會變成「已請款」）——
    人員費用那三顆重畫的是自己那區，兩者不能共用一個寫死的重畫目標。"""
    js = js_code_only(repo_src(COST_JS))
    fn = js_func_body(js, "window._expCreatePayment = function(expenseId) {")
    assert "onDone" in fn and "_loadFinancialSummary(state.selectedId)" in fn
    body = js_func_body(js_code_only(repo_src(FIN_JS)),
                        "window._costCreatePayment = function(payeeName, amount, "
                        "summary, status, advanced, opts) {")
    assert "opts.onDone ? " in body or "if (opts.onDone)" in body
