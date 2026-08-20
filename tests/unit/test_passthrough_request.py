# -*- coding: utf-8 -*-
"""代開發票的收款 → 待請款自動化（owner 2026-08-20 拍板）。

業務流：公司幫人代開發票 → 客戶匯款進來、發票標「已收款」→ 自動出現一張
「應付款」請款單（金額 = 發票的代開應匯 commission = 面額 × 92%，8% 是稅與
手續費）→ 在應付帳款付掉 → 發票走到「已付款」，整條收尾。
歷史資料實測：92% 是主流（105 筆），發票庫 209 張有 commission。
"""
from tests.unit._srcscan import code_only, func_body, repo_src  # noqa: E402

SRC = 'routers/crm/finance.py'


def _body(fn):
    return code_only(func_body(repo_src(SRC), fn))


def test_every_status_change_path_triggers_the_sync():
    """三條會把發票標成/退出「已收款」的路，都要掛 _sync_passthrough_request。

    漏一條的後果：從那條路收款的代開發票不會進待請款區 —— 該匯給代開人的錢
    就靜靜躺著，直到對方來催。
    """
    for fn in ('async def update_invoice(',      # 手動改狀態（最常見的觸發點）
               'async def _resettle_invoice(',   # 收支連動（掛發票/分配面板/刪收支）
               'async def batch_receive('):      # 批次標已收款
        assert '_sync_passthrough_request' in _body(fn), \
            f'{fn} 沒有觸發代開待請款同步'


def test_pay_and_unpay_complete_the_invoice_lifecycle_symmetrically():
    """付掉 → 發票進「已付款」；取消付款 → 退回「已收款」。

    只做一半會留下「請款單應付款、發票卻已付款」的矛盾 —— 待請款區有一張，
    發票卻說錢已經匯了。
    """
    pay = _body('async def batch_pay(')
    unpay = _body('async def batch_unpay(')
    assert '"已付款"' in pay and 'CrmInvoice' in pay, 'batch_pay 沒有收尾發票狀態'
    assert '"已收款"' in unpay and 'CrmInvoice' in unpay, 'batch_unpay 沒有對稱反向'


def test_sync_guards_are_all_present():
    """核心函式的四道防線：

    1. 只認代開（category 含「代開」）—— 一般發票收款不該生請款單
    2. 沒有發票號碼不建 —— 號碼是冪等鍵，沒有它重複觸發就會重複建
    3. 已存在同號請款單就不建 —— 歷史匯入的 178 筆靠這條擋
    4. 反向只刪「自動產生且還是應付款」的 —— 人手建的與已付款的絕不動
    """
    body = _body('async def _sync_passthrough_request(')
    assert '"代開" in inv.category' in body
    assert 'if not no:' in body
    assert 'if existing:' in body and 'return' in body
    assert '_AUTO_KAI_NOTE in (p.notes or "")' in body
    assert '"應付款"' in body


def test_resettle_never_downgrades_a_remitted_passthrough_invoice():
    """收款重算不可以把代開發票從「已轉撥」降回「已收款」。

    已轉撥 = 錢已經匯給代開人，是比已收款更後面的階段 —— 有人動到那筆收款
    （改金額、重掛發票）觸發重算時，匯出去的事實不能被蓋掉。
    """
    from routers.crm.finance import INVOICE_REMITTED
    assert INVOICE_REMITTED == '已轉撥'
    body = _body('async def _resettle_invoice(')
    assert 'INVOICE_REMITTED' in body and '"代開" in inv.category' in body, \
        '_resettle_invoice 會把已匯出的代開發票降回已收款'


def test_commission_fallback_is_92_percent():
    """commission 欄空的時候退回面額 × 92% —— 這個比例是從 105 筆歷史資料
    反推出來的主流規則（43,050→39,606、100,000→92,000）。"""
    body = _body('async def _sync_passthrough_request(')
    assert '* 0.92' in body
