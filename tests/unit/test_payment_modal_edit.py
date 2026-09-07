# -*- coding: utf-8 -*-
"""請款分頁「⋮ → 編輯」彈窗（owner 2026-09-07「請款的專案我連結不上去」「思沙龍 EP01/EP02 沒出現在應付款」）：
生產 3 張代開單被存成 project_id＝發票 id、payment_status＝''。三個根因各釘一條，後端入口再加一道閘。"""
from tests.unit._srcscan import code_only, func_body, js_code_only, js_func_body, repo_src

JS = "frontend/tabs/crm/crm-payments.js"


def test_modal_save_never_sends_invoice_id_as_project():
    body = js_code_only(js_func_body(repo_src(JS), "async function savePayment()"))
    assert "val = document.getElementById('pay-f-project_id2')?.value || null;" in body
    assert "payload.source_invoice_id = invSel?.value || null;" in body
    assert "val = document.getElementById('pay-f-project_id')?.value || null;" not in body, "發票下拉不准當專案送"


def test_modal_save_skips_fields_the_form_does_not_have():
    body = js_code_only(js_func_body(repo_src(JS), "async function savePayment()"))
    assert "if (!el && f !== 'project_id') continue;" in body, "表單沒有的欄位（payment_status）送空字串會洗掉狀態"
    assert "if (!_editingId) payload.payment_status = '應付款';" in body


def test_modal_open_prefills_invoice_select_from_source_invoice_id():
    src = repo_src(JS)
    assert "_updateExtraFields(p?.category || '', p?.source_invoice_id || '');" in src
    assert "function _updateExtraFields(category, invoiceId = '')" in src
    assert "const current = invoiceId || sel.value;" in src
    assert "inv.issue_status === '已開立' || inv.id === current" in src, "沒開號的票也要留在下拉，否則編輯一存就洗掉發票連結"


def test_backend_rejects_project_id_that_is_not_a_project():
    src = repo_src("routers/crm/payments.py")
    assert "async def _assert_project_exists(session, project_id)" in src
    # helper 要在裝飾器**上方**：插在 @router.post 與 create_payment 之間會把路由搶走（2026-09-07 dev 撞到：POST /payments 422 缺 session）
    assert '@router.post("/payments")\nasync def create_payment(' in src
    for fn in ("async def create_payment(", "async def update_payment("):
        assert "_assert_project_exists(session," in code_only(func_body(src, fn)), fn


def test_resettle_falls_back_to_current_vocabulary():
    body = code_only(func_body(repo_src("routers/crm/finance.py"), "async def resettle_payment_requests("))
    assert '"未付款"' not in body and 'ap.payment_status = "應付款"' in body


def test_boot_repairs_the_three_broken_rows():
    src = repo_src("db/migrations.py")
    assert "p.project_id = p.source_invoice_id AND i.id = p.source_invoice_id" in src
    assert "SET payment_status='應付款' WHERE payment_status IS NULL OR payment_status=''" in src


def test_modal_keeps_non_staff_payee():
    """代開單的收款人＝代開人（多半不是員工）：人員下拉沒有這個名字時要把目前值留著，否則一開編輯就清空、一存就洗掉。"""
    body = js_code_only(js_func_body(repo_src(JS), "function _populatePayeeSelect(selectedName)"))
    assert "!_staffList.some(s => s.name === selectedName)" in body and "selected>${_esc(selectedName)}</option>" in body
