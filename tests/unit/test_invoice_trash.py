"""發票垃圾桶（owner 2026-09-04「不小心刪掉的發票要救得回來」「30 天後自己清空」）。

不做軟刪除欄位：發票被十幾條查詢讀，漏一條刪掉的票就從報表冒回來。刪的瞬間整列＋被清掉的
收支分配連結序列化進 crm_invoice_trash，正本照舊硬刪；還原用原 id 建回、連結還在的補回。"""
import re

from tests.unit._srcscan import repo_src, func_body


def _fin():
    return repo_src("routers/crm/finance.py")


def test_trash_routes_are_registered_before_the_id_route():
    """FastAPI 照註冊順序配對：/invoices/trash 排在 /invoices/{invoice_id} 後面會被當成 id="trash"。"""
    src = _fin()
    assert src.index('@router.get("/invoices/trash"') < src.index('@router.get("/invoices/{invoice_id}"')
    assert '@router.post("/invoices/trash/{trash_id}/restore")' in src
    assert '@router.delete("/invoices/trash/{trash_id}")' in src


def test_delete_stashes_the_whole_row_and_its_links_before_hard_delete():
    body = func_body(_fin(), "async def delete_invoice(")
    assert "_invoice_row_snapshot(inv)" in body, "要整列快照，不是挑幾欄——之後加的欄位也要救得回來"
    assert '"links"' in body and '"primary_of"' in body
    assert "session.merge(CrmInvoiceTrash(" in body
    assert body.index("session.merge(CrmInvoiceTrash(") < body.index("session.delete(inv)")
    # 原本的清連結保護不能因為多了垃圾桶就掉（test_project_invoice_panel 也釘）
    assert re.search(r"_sadel\(\s*CrmCashInvoiceLink\s*\)", body)
    assert "_purge_expired_trash(session)" in body


def test_restore_keeps_the_id_and_does_not_steal_a_cash_entrys_new_invoice():
    body = func_body(_fin(), "async def restore_invoice_from_trash(")
    assert 'kwargs["id"] = trash_id' in body, "還原要用原 id，舊連結／分享碼才認得"
    assert "replace_invoice_allocs_bulk(session, pairs, fees=fees)" in body, "分配表只有一個寫入者（test_stmt_link 釘的）"
    assert "CrmCashInvoiceLink(" not in body
    assert "if e and not e.invoice_id:" in body, "直接指著它的舊資料：這段期間掛了別張就不搶"
    assert "_assert_month_open" in body and 'level="full"' in body
    assert "status_code=409" in body


def test_thirty_day_auto_purge():
    src = _fin()
    assert "TRASH_KEEP_DAYS = 30" in src
    body = func_body(src, "async def _purge_expired_trash(")
    assert "timedelta(days=TRASH_KEEP_DAYS)" in body and "CrmInvoiceTrash.deleted_at < cutoff" in body
    lst = func_body(src, "async def list_invoice_trash(")
    assert "_purge_expired_trash(session)" in lst, "沒有排程器：列垃圾桶時順手清"


def test_model_is_exported():
    assert "class CrmInvoiceTrash(Base)" in repo_src("db/models/_crm.py")
    assert "CrmInvoiceTrash" in repo_src("db/models/__init__.py")


def test_both_uis_can_restore():
    desk = repo_src("frontend/tabs/crm/crm-invoices.js")
    assert "/invoices/trash" in desk and "/restore'" in desk and "_invTrashRestore" in desk
    assert 'id="inv-btn-trash"' in repo_src("frontend/tabs/crm/crm-invoices.html")
    mob = repo_src("frontend/m/views/invoice.js")
    assert "/api/v1/crm/invoices/trash" in mob and "data-restore=" in mob
    assert "30 天" in mob and "30 天" in desk, "刪除確認要告訴人可以還原"
