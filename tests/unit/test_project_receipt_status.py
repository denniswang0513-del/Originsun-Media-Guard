# -*- coding: utf-8 -*-
"""母公司案的帳款狀況從掛帳收入推（owner 2026-09-04「發票與收支連結好了的話，收到多少款＋匯費 等於合約金額的時候，這裡要改已到帳」）。
規則一份 core.ledger_project.parent_receipt_fields；列的來源＝列自己掛本案、或列掛的發票是本案的；沒掛的老案維持手填。"""
from tests.unit._srcscan import code_only, func_body, repo_src


def test_rule_gross_receipts_against_contract():
    from core.ledger_project import parent_receipt_fields
    # deposit 記的是客戶匯出的毛額（cash_entry_flow ＝ deposit − expense − bank_fee），匯費是從中扣走的，
    # 拿 deposit 比合約；不能再加 fee（2026-09-04 加過一次 → 東仁社宅溢收 −15，owner 2026-09-05 抓到）
    assert parent_receipt_fields(150000, 150000, 30) == (0, "全額到帳")        # 客戶匯 150,000、銀行扣 30
    assert parent_receipt_fields(150000, 150000, 0) == (0, "全額到帳")
    assert parent_receipt_fields(150000, 100000, 30) == (50000, "部分到帳")
    assert parent_receipt_fields(150000, 0, 0) == (150000, "未到帳")
    assert parent_receipt_fields(150000, 152000, 0) == (-2000, "全額到帳")     # 溢收看得見


def test_projects_router_derives_from_linked_income_rows():
    src = repo_src("routers/crm/projects.py")
    m = code_only(func_body(src, "async def linked_receipts_map("))
    assert 'CrmCashEntry.entity == "parent"' in m and "CrmCashEntry.deposit > 0" in m
    assert "CrmCashEntry.invoice_id.in_(set(inv_proj))" in m, "掛在本案發票上的收入列也算"
    d = code_only(func_body(src, "def _to_project_dict("))
    assert "parent_receipt_fields(int(p.contract_amount or 0), received, fee)" in d
    assert '"payment_status": status if derived else (p.payment_status or "未到帳")' in d
    assert '(p.entity or "parent") != "mine" and (received or fee)' in d, "私帳案不走這條（增量制）；沒掛帳的老案維持手填"
    lst = code_only(func_body(src, "async def list_projects("))
    assert "linked_receipts_map(" in lst and ", receipts)" in lst
    assert "linked_receipts_map(session, [project.id])" in code_only(func_body(src, "async def project_wire("))
