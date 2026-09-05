# -*- coding: utf-8 -*-
"""發票掛專案（owner 2026-09-05「加一個連結發票的按鈕」）。

為什麼需要：發票是**先開、後歸戶**的 —— 開的時候還不知道要掛哪一案，之後就
沒有一條路把它掛回來。快樂學游泳那案 7 張裡有 1 張（錄音租借 $630）就這樣
一直沒進來，而畫面上完全看不出少了什麼（只顯示「已開發票 4 張」）。
"""
from tests.unit._srcscan import js_code_only, repo_src


def test_candidates_endpoint_excludes_what_should_not_be_offered():
    """候選清單三條排除，每一條都有理由。"""
    src = repo_src("routers/crm/finance.py")
    fn = src.split("async def invoice_candidates(")[1].split("\n@router")[0]
    # 作廢的不是漏掉，是刻意作廢
    assert 'CrmInvoice.issue_status != "作廢"' in fn
    # 已經掛在本案的畫面上已經有了
    assert "CrmInvoice.project_id != project_id" in fn
    # 帳本牆：只找同一本帳的
    assert "CrmInvoice.entity ==" in fn
    # 沒有私帳權限的人，連「這張掛在某個私帳案」都不該看到
    assert "hide_mine_projects(request)" in fn and "mine_ids" in fn
    # 沒搜尋字時不倒整份出來
    assert "if not kw and not score:" in fn


def test_candidates_rank_by_how_likely_it_is_this_project():
    """同抬頭 ＋ 標題含案名 → 同抬頭 → 其餘。"""
    src = repo_src("routers/crm/finance.py")
    fn = src.split("async def invoice_candidates(")[1].split("\n@router")[0]
    assert "same_title = " in fn and "hit_name = " in fn
    assert "score = (2 if (same_title and hit_name) else 1 if same_title else 0)" in fn
    # 掛在別案的照列但要標出來 —— 掛錯案要能改回來
    assert '"linked_to": names.get(' in fn


def test_link_endpoint_only_touches_the_attribution():
    """🔴 只改「屬於哪個案」：金額、日期、狀態一個字都不動。

    所以不掛月結守衛（判準同 payments.batch_assign_project：帳沒變），
    但**類別守衛照掛**（私帳案只收「內部代開」且要有私帳權限）。
    """
    src = repo_src("routers/crm/finance.py")
    fn = src.split("async def set_invoice_project(")[1].split("\n@router")[0]
    assert "inv.project_id = target" in fn
    for forbidden in ("amount_total", "amount_ex_tax", "payment_status", "invoice_date"):
        assert f"inv.{forbidden} =" not in fn, forbidden
    assert "_assert_project_link(session, request, target, inv.category)" in fn
    assert 'require_entity(request, inv.entity or "parent", level="full")' in fn
    assert "_check_finance_auth(request)" in fn
    assert "_assert_month_open" not in fn, "只改歸屬，帳沒變 —— 不該擋月結"


def test_the_button_sits_next_to_the_invoice_button():
    """按鈕放「收款」標題列，跟「開發票」並排 —— 那正是發現少了一張的地方。"""
    js = js_code_only(repo_src("frontend/tabs/crm/crm-projects-pay.js"))
    assert "window._projPay.linkInvoice()" in js
    assert "linkInvoice: () =>" in js and "doLink: async (invoiceId)" in js
    assert "/invoice-candidates" in js
    assert "method: 'PATCH'" in js
