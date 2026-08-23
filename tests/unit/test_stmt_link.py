# -*- coding: utf-8 -*-
"""對帳單匯入時當場掛專案與發票（owner 2026-08-20）。

🔴 這條的重點是「發票要進**分配表**」：列表的發票欄讀 crm_cash_entries.invoice_id，
但「已收金額」與關聯面板讀 crm_cash_invoice_links —— 只寫一邊就會兩處各講各的
（這一輪已經在收支明細那邊修過同一個洞，不可以在匯入這條路上重犯）。
"""
from tests.unit._srcscan import code_only, func_body, repo_src  # noqa: E402


SRC = 'routers/crm/finance.py'


def _body(fn):
    return code_only(func_body(repo_src('routers/api_finance_stmt.py'), fn))


def test_schema_carries_project_and_invoice():
    from core.schemas import StatementImportRow
    d = StatementImportRow(date="2026-09-01", amount=100, project_id="p1",
                           invoices=[{"invoice_id": "i1", "amount": 100}]).model_dump()
    assert d["project_id"] == "p1"
    # fee＝被匯出行扣掉、沒進帳戶的部分（預設 0，見 CashInvoiceLink）
    assert d["invoices"] == [{"invoice_id": "i1", "amount": 100, "fee": 0}]
    # 沒給時：專案是 None 不是空字串（soft FK 用 '' 會讓 IS NULL 查詢漏列）；
    # 發票是空 list 不是 None（🔴 那欄是 List，送 null 會 422 —— 前端曾經這樣送，
    # 只要對帳單有一列沒掛發票就整批匯不進去）
    d2 = StatementImportRow(date="2026-09-01", amount=100).model_dump()
    assert d2["project_id"] is None and d2["invoices"] == []


def test_apply_reuses_the_canonical_helpers():
    """專案與發票的寫入規則只有一套正本（routers/crm/finance）—— 這裡借用，不另寫。

    自己寫一份的話，兩條路遲早會講出不同的話：例如這邊忘了過
    _enforce_cash_project_link，行政/薪資就能掛專案，專案毛利多算一筆不屬於它的錢。
    （2026-08-21 的 /simplify：分配表的寫入本來是抄過來的第三份，已收斂成
    resolve_invoice_allocs + replace_invoice_allocs 兩支共用。）
    """
    body = _body('async def apply_bank_statement(')
    for helper in ('_enforce_cash_project_link', 'resolve_invoice_allocs',
                   'replace_invoice_allocs_bulk'):
        assert helper in body, f'apply 沒有走 {helper}'


def test_invoice_goes_through_the_allocation_table():
    """🔴 只寫 invoice_id 不算數 —— 一定要進 crm_cash_invoice_links。

    而且分配表**只有一個寫入者**：replace_invoice_allocs。它一次動三樣東西
    （分配表、收支列的 invoice_id/invoice_number/has_invoice、相關發票的收款狀態），
    少做一樣就會有兩個畫面各講各的。誰再自己 session.add(CrmCashInvoiceLink(...))
    誰就會漏掉另外兩樣。
    """
    import re
    hits = []
    for path in ('routers/crm/finance.py', 'routers/crm/invoice_files.py',
                 'routers/api_finance.py', 'routers/api_finance_stmt.py'):
        body = code_only(repo_src(path))
        hits += [(path, m.start()) for m in re.finditer(r'CrmCashInvoiceLink\(', body)]
    assert len(hits) == 1, f'分配表有 {len(hits)} 個寫入點（應該只有 1 個）：{hits}'
    writer = code_only(func_body(repo_src(SRC),
                                 'async def replace_invoice_allocs_bulk('))
    assert 'CrmCashInvoiceLink(' in writer, '唯一的寫入點不是共用的那支'
    # 單筆版只是薄殼 —— 規則不能有第二份
    one = code_only(func_body(repo_src(SRC),
                              'async def replace_invoice_allocs(session, entry'))
    assert 'replace_invoice_allocs_bulk(' in one and 'CrmCashInvoiceLink(' not in one
    # 編輯視窗那條路也委派給它（差別只在「不要動主要發票」這個旋鈕）
    single = code_only(func_body(repo_src(SRC), 'async def _sync_single_alloc('))
    assert 'replace_invoice_allocs(' in single and 'set_primary=False' in single

    body = _body('async def apply_bank_statement(')
    assert '_sync_single_alloc' not in body,         '用了「一張全額」的 helper —— 合併匯款會被壓成一張'
    # 分配要在 flush 之後（分配表要有收支列的 id 才掛得上）
    i_flush = body.index('await session.flush()')
    i_link = body.index('replace_invoice_allocs_bulk(')
    assert i_flush < i_link, '分配表在 flush 之前做 —— 收支列還沒有 id'


def test_one_row_can_carry_many_invoices():
    """合併匯款：一筆入帳拆給多張發票（分配表本來就是多對多）。"""
    from core.schemas import StatementImportRow
    r = StatementImportRow(date="2026-09-01", amount=100,
                           invoices=[{"invoice_id": "a", "amount": 60},
                                     {"invoice_id": "b", "amount": 40}])
    assert [(x.invoice_id, x.amount) for x in r.invoices] == [("a", 60), ("b", 40)]
    body = _body('async def apply_bank_statement(')
    assert 'r.invoices' in body, 'apply 沒有讀多張分配'
    # 主要發票 = 金額最大那張。規則只有一份，在 replace_invoice_allocs 裡。
    writer = code_only(func_body(repo_src(SRC),
                                 'async def replace_invoice_allocs_bulk('))
    assert 'max(rows, key=lambda x: x[1])' in writer


def test_invoice_only_for_income_rows():
    """支出列掛發票是代開付出去那側，語意不同，不在這條路管。"""
    body = _body('async def apply_bank_statement(')
    assert 'allocs and amt > 0' in body, '沒有限制成收入列'
    # 只有 invoices 一種表示法（invoice_id 簡寫在 /simplify 第 2 輪拿掉了 ——
    # 沒有任何產生端不同時送 invoices，留著只是讓同一件事有兩種寫法）
    from core.schemas import StatementImportRow
    assert 'invoice_id' not in StatementImportRow.model_fields


def test_preview_returns_the_option_lists():
    """選項跟預覽一起回 —— 那兩支在不同的 prefix 下，前端不該再多打兩次。"""
    # 預覽的內容由 _build_statement_preview 產（上傳與開啟草稿共用同一支）
    body = _body('async def _build_statement_preview(')
    assert '"projects": projects' in body
    assert '"invoices": open_invs' in body
    assert '"project_categories"' in body
    # 發票只列還沒收齊的：已經收完的列出來只會讓人選錯。
    # 🔴 判準走 collection_fields 的 settled（含匯費容差），不是自己算
    # outstanding > 0 —— 兩個挑選器對「還沒收齊」的定義要一樣
    assert 'collection_fields(' in body and 'c["settled"]' in body
    assert 'outstanding > 0' not in body
