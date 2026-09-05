# -*- coding: utf-8 -*-
"""專案詳情「收付款」分頁（owner 2026-09-04：人員配置改成收付款、與發票整合，用業務的觀點看這一案的錢走到哪、下一步該做什麼）。

不新增資料：狀態列／提示／結案檢查是純算式（payStatus／nextSteps／closingChecks），吃的是專案 dict（已收／匯費／帳款狀況
由後端推導）、發票、請款單、費用配置。三個決定（owner 未拍板前的保守做法）：派工不刪、收在可展開區；沒金額權限只看狀態字；結案檢查只提醒不硬擋。"""
from tests.unit._srcscan import between, js_code_only, repo_src

PAY = "frontend/tabs/crm/crm-projects-pay.js"


def test_tab_composes_existing_pieces_instead_of_new_data():
    js = js_code_only(repo_src(PAY))
    assert "groupCostStaff" in js, "執行人員分人配單走 crm-utils 那一份"
    # 版面照示範頁（owner 2026-09-04 第二版「跟 demo 一樣，派工拿掉」）：自己畫表格，動作沿用既有那組
    assert "loadInvoicesTab(projectId, 'proj-pay-invoices')" in js, "發票模組載進藏著的容器（開票視窗／setMeta／del 靠它）"
    assert "_loadProjectStaff" not in js and "proj-staff-list" not in repo_src("frontend/tabs/crm/crm-projects-detail.js"), "派工已拿掉"
    assert "window._costCreatePayment(" in js and "window._costPayBtns" in js and "window._costCreateAdvance()" in js
    assert "POST" not in js and "method: 'PUT'" not in js and "DELETE" not in js, "收付款分頁自己不寫資料——動作都是既有那組"
    for sec in ("function _recvHtml(", "function _payHtml(", "function _chain(", "function _cashHtml(", "function _checksHtml(", "function _metaHtml("):
        assert sec in js, sec
    assert "未請款" in js and "已請款" in js and "已付款" in js, "狀態鏈三段"
    detail = repo_src("frontend/tabs/crm/crm-projects-detail.js")
    for hid in ("proj-pay-root", "proj-pay-ampm-src", "proj-am-display", "proj-pm-display"):
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
    assert "不擋結案" in js, "結案檢查只軟擋"


def test_actions_refresh_the_strip():
    fin = js_code_only(repo_src("frontend/tabs/crm/crm-projects-finance.js"))
    assert fin.count("window._projPay?.refresh?.()") >= 2, "請款／付款動作做完要重算狀態列"
    inv = js_code_only(repo_src("frontend/tabs/crm/crm-projects-invoices.js"))
    assert "window._projPay?.refresh?.()" in inv, "開完票要重算狀態列"


def test_phase_two_and_three_hooks():
    """二期：完稿結案分頁頂端有收付檢查提示條（只提醒）；三期：應付帳款可跳回案子的收付款分頁、手機版抽屜有收付款摘要。"""
    pay = js_code_only(repo_src(PAY))
    assert "export async function loadClosingBanner(" in pay and "closingChecks(s, d.adv, d.exp)" in pay
    assert "window._crmGoToProjectPay = (projectId) =>" in pay
    main = js_code_only(repo_src("frontend/tabs/crm/crm-projects.js"))
    assert "loadClosingBanner(pid, host)" in main
    assert '"project_id": getattr(p, "project_id", None) or ""' in repo_src("core/crm_logic.py").split("def group_payables")[1].split("def ")[0]
    pay_src = js_code_only(repo_src("frontend/tabs/crm/crm-payables.js"))
    assert "window._crmPayableGo('${_esc(it.project_id)}')" in pay_src and "window._crmPayableGo = async function" in pay_src   # 2026-09-06 review: bridge loads the projects tab first
    m = js_code_only(repo_src("frontend/m/views/projects.js"))
    assert "function _payRows(p, d)" in m and "${_payRows(p, d)}" in m and "'amount_receivable' in p" in m, "沒金額權限只剩狀態字"


def test_closing_is_soft_blocked_on_every_status_path():
    """owner 2026-09-04 採建議：結案檢查不硬擋，但推到結案時沒結清要列出來讓人確認。三條推狀態的路都要問：
    專案詳情狀態欄 inline、提案企劃「推階段」（走 window 不跨分頁 import）、手機版推階段（字彙走 options.closed_phases）。"""
    pay = js_code_only(repo_src(PAY))
    fn = between(pay, "export async function confirmClosing(", "window._crmGoToProjectPay")
    assert "closingChecks(s, d.adv, d.exp).filter((c) => !c.ok)" in fn and "return confirm(" in fn
    assert "if (!d.proj) return true;" in fn, "抓不到資料（沒權限）要放行"
    detail = js_code_only(repo_src("frontend/tabs/crm/crm-projects-detail.js"))
    assert "CLOSED_STATUSES.includes(val) && !CLOSED_STATUSES.includes(orig) && window._projPay?.confirmClosing" in detail
    flow = js_code_only(repo_src("frontend/tabs/proposals/flow-view.js"))
    assert "window._projPay.confirmClosing(f.pid)" in flow and "import" not in flow.split("window._projPay.confirmClosing")[0].splitlines()[-1]
    m = js_code_only(repo_src("frontend/m/views/projects.js"))
    assert "closedPhases().includes(body.status)" in m and "paidStatus()" in m
    assert '"closed_phases"' in repo_src("routers/api_crm_mobile.py") and "export const closedPhases" in repo_src("frontend/m/ui.js")


def test_pay_tab_margin_comes_from_financial_summary():
    """owner 2026-09-05：收付款的毛利曾算成「含稅合約 − 已請款」（32%），跟預算結算（未稅 − 雜支 − 人力，20%）對不上。
    毛利只認 /financial-summary 那一份（core.crm_logic.project_margin），沒拿到才退回舊算法並標明。"""
    src = repo_src("frontend/tabs/crm/crm-projects-pay.js")
    assert "/financial-summary`" in src and "fin.actual_profit" in src and "fin.profit_rate" in src
    assert "_strip(s, money, d.fin)" in src


def test_financial_summary_staff_cost_comes_from_cost_lines():
    """owner 2026-09-05：東仁社宅 API 毛利 95%、預算結算畫面 20%——/financial-summary 的人力只算退場的派工表。
    子表有數字就以子表為準。"""
    body = func_body(repo_src("routers/crm/costs.py"), "async def get_financial_summary(") if "async def get_financial_summary(" in repo_src("routers/crm/costs.py") else repo_src("routers/crm/costs.py")
    assert "staff_actual = costline_actual" in body and "staff_estimated = costline_estimated" in body


def test_pay_tab_counts_unassigned_cost_lines():
    src = repo_src("frontend/tabs/crm/crm-projects-pay.js")
    assert "unassignedLines" in src and "+ unassigned;" in src and "未指派" in src
