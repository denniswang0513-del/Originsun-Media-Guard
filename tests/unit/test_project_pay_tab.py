# -*- coding: utf-8 -*-
"""專案詳情「收付款」分頁（owner 2026-09-04：人員配置改成收付款、與發票整合，用業務的觀點看這一案的錢走到哪、下一步該做什麼）。

不新增資料：狀態列／提示／結案檢查是純算式（payStatus／nextSteps／closingChecks），吃的是專案 dict（已收／匯費／帳款狀況
由後端推導）、發票、請款單、費用配置。三個決定（owner 未拍板前的保守做法）：派工不刪、收在可展開區；沒金額權限只看狀態字；結案檢查只提醒不硬擋。"""
from tests.unit._srcscan import between, js_code_only, repo_src

PAY = "frontend/tabs/crm/crm-projects-pay.js"


def test_tab_composes_existing_pieces_instead_of_new_data():
    js = js_code_only(repo_src(PAY))
    assert "groupCostStaff" in js, "執行人員分人配單走 crm-utils 那一份"
    assert "_loadCostStaff(projectId)" in js and "_loadAdvances(projectId)" in js and "loadInvoicesTab(projectId, 'proj-pay-invoices')" in js
    assert "_loadProjectStaff" in js, "派工不刪，收在可展開區"
    assert "POST" not in js and "method: 'PUT'" not in js and "DELETE" not in js, "收付款分頁自己不寫資料——動作都是既有那組"
    detail = repo_src("frontend/tabs/crm/crm-projects-detail.js")
    for hid in ("proj-pay-strip", "proj-pay-invoices", "proj-cost-staff", "proj-advance-list", "proj-staff-list", "proj-pay-cash", "proj-pay-check"):
        assert f'id="{hid}"' in detail, hid
    assert 'data-tab="team"' in repo_src("frontend/tabs/crm/crm-projects.html")


def test_status_and_next_steps_are_pure_and_money_gated():
    js = js_code_only(repo_src(PAY))
    fn = between(js, "export function payStatus(", "export function nextSteps(")
    assert "p.amount_received" in fn and "p.transfer_fee" in fn and "p.amount_receivable" in fn, "已收／匯費／未收吃後端推導值"
    assert "contract - payable" in fn
    nx = between(js, "export function nextSteps(", "export function closingChecks(")
    assert "opts.money !== false" in nx and "發票還沒開" in nx and "還沒請款" in nx and "應付款還沒付" in nx and "可以結案" in nx
    assert "canSeeMoney()" in js, "沒 money_view 只看狀態字"
    ck = between(js, "export function closingChecks(", "let _cur")
    for label in ("發票全開且已收", "應付全付", "雜支結清", "預支款結清"):
        assert label in ck, label
    assert "不擋結案" in repo_src("frontend/tabs/crm/crm-projects-detail.js"), "結案檢查目前只提醒"


def test_actions_refresh_the_strip():
    fin = js_code_only(repo_src("frontend/tabs/crm/crm-projects-finance.js"))
    assert fin.count("window._projPay?.refresh?.()") >= 2, "請款／付款動作做完要重算狀態列"
    inv = js_code_only(repo_src("frontend/tabs/crm/crm-projects-invoices.js"))
    assert "window._projPay?.refresh?.()" in inv, "開完票要重算狀態列"
