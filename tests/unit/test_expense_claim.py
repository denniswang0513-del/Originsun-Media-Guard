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
    assert "claims.get(e.id" in fn
    assert '"payment_id":' in fn and '"payment_status":' in fn


def test_petty_rows_do_not_get_a_second_claim_path():
    """🔴 零用金流進來的列（`staff_id`）**不給這顆鈕**。

    那些錢已經有自己的一條請款路（零用金批次 → 依「會計項目×月份」開應付款，
    見 routers/crm/petty._build_aps）。兩條路都走＝同一筆錢請兩次，而後端的
    409 守的是 `expense_id` 重複，擋不到「零用金那側另外開了一張」。
    """
    # 規則在後端：帶 expense_id 建單時，那一列若是零用金（staff_id）就 409
    py = code_only(func_body(repo_src("routers/crm/payments.py"), "async def create_payment("))
    assert "exp.staff_id" in py and "409" in py, "零用金列的第二條請款路沒在後端擋"
    # 前端不長按鈕只是禮貌
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
    assert "onDone: window._expClaimDone" in fn
    assert "_loadFinancialSummary(state.selectedId)" in js_func_body(js, "window._expClaimDone = function() {")
    body = js_func_body(js_code_only(repo_src(FIN_JS)),
                        "window._costCreatePayment = function(payeeName, amount, "
                        "summary, status, advanced, opts) {")
    assert "opts.onDone ? " in body or "if (opts.onDone)" in body


# ── 請款之後：可收回、可改狀態（owner 2026-09-02「像私帳那樣」）──

def test_the_claimed_row_can_be_taken_back():
    """🔴 請款之後那一列不能只剩「已請款」三個字 —— 按錯了要能當場收回，
    不用跑去別的 tab 找那張單。"""
    js = js_code_only(repo_src(FIN_JS))
    fn = js_func_body(js, "window._costPayBtns = function(p, onDoneName) {")
    assert "已付款" in fn and "改回應付" in fn
    assert "標記付款" in fn and "收回請款" in fn
    # 已付款的單不給收回 —— 錢都出去了還撤單，帳上會少一筆付款
    head = fn.split("'標記付款'")[0]          # 「標記付款」之前＝已付款那條分支
    assert "_costPayWithdraw" not in head, "已付款那條路也給了收回鈕"


def test_status_changes_go_through_the_shared_endpoints():
    """🔴 走跟私帳同一組端點（batch-pay / batch-unpay），不自己 PUT
    `payment_status` —— 那支端點一次處理付款日與代開發票的撥款狀態，繞過去
    就會出現「請款單說沒付、發票說已撥款」的兩份答案。"""
    js = js_code_only(repo_src(FIN_JS))
    fn = js_func_body(js, "async function _costPayAction(id, paid) {")
    assert "'/payments/batch-pay'" in fn and "'/payments/batch-unpay'" in fn
    assert "payment_status" not in fn, "又自己寫狀態了"
    # 整包 GET→PUT 寫回的舊寫法要真的退場（schema 預設值會洗掉沒送的欄位）
    assert "window._costUpdatePaymentStatus = " not in js


def test_both_entry_points_share_one_set_of_actions():
    """人員費用（列上）與行政雜支（詳情視窗）共用同一份動作 —— 各寫一次的話，
    「已付款能不能收回」這條規則就會有兩個答案。"""
    fin = js_code_only(repo_src(FIN_JS))
    pay = js_code_only(repo_src("frontend/tabs/crm/crm-projects-pay.js"))
    assert "window._costPayBtns(p)" in pay, "人員費用列（收付款分頁）沒接上"
    assert "window._costPayBtns(p, onDoneName) +" in fin, "詳情視窗頁尾沒接上"
    # 雜支那側重畫的是雜支區，不是人員費用區
    cost = js_code_only(repo_src(COST_JS))
    assert "window._expClaimDone" in cost
    assert "_costViewPayment('${e.payment_id}','window._expClaimDone')" in cost
