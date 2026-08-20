# -*- coding: utf-8 -*-
"""收款↔發票分配的金額判讀（_alloc_verdict）— 純函式，不碰 DB。

判讀是**提示不是閘門**：實收比發票少幾十元（收款方代扣匯費）與分期只收一半
都是真實常態，硬擋會逼人亂填。所以這裡釘的是「話有沒有講對」。
"""
from types import SimpleNamespace


from routers.crm.finance import _alloc_verdict  # noqa: E402
from tests.unit._srcscan import code_only, func_body, repo_src  # noqa: E402


def _entry(deposit):
    return SimpleNamespace(deposit=deposit)


def test_exact_match():
    v = _alloc_verdict(_entry(42000), 42000)
    assert v["state"] == "ok" and v["diff"] == 0


def test_merged_transfer_three_invoices_sum_exactly():
    """合併匯款：客戶一次匯 3 張發票 —— 分配加總等於實收就該全綠。"""
    v = _alloc_verdict(_entry(126000), 42000 + 42000 + 42000)
    assert v["state"] == "ok"


def test_small_overage_is_called_a_fee_not_an_error():
    """發票 42,000、實收 41,990 —— 差 10 元是匯費，不能講成帳錯了。"""
    v = _alloc_verdict(_entry(41990), 42000)
    assert v["state"] == "fee"
    assert "匯費" in v["message"]


def test_partial_payment_reads_as_over_allocation():
    """分期：發票 100,000 這次只收 40,000 —— 提示「沒收齊」而不是報錯。"""
    v = _alloc_verdict(_entry(40000), 100000)
    assert v["state"] == "over"
    assert v["diff"] == 60000
    assert "分期" in v["message"]


def test_under_allocation_flags_missing_invoice():
    """實收 126,000 只掛了一張 42,000 —— 還有發票沒掛上。"""
    v = _alloc_verdict(_entry(126000), 42000)
    assert v["state"] == "under"
    assert v["diff"] == -84000


def test_no_allocation_is_empty_not_under():
    v = _alloc_verdict(_entry(42000), 0)
    assert v["state"] == "empty"


def test_expense_row_has_zero_received():
    """支出列 deposit 是 None —— 不可以炸，received 當 0。"""
    v = _alloc_verdict(SimpleNamespace(deposit=None), 0)
    assert v["received"] == 0 and v["state"] == "empty"


def test_boundary_50_is_still_fee_51_is_over():
    assert _alloc_verdict(_entry(1000), 1050)["state"] == "fee"
    assert _alloc_verdict(_entry(1000), 1051)["state"] == "over"


def test_delete_cash_entry_also_deletes_its_invoice_links():
    """刪收支必須連分配列一起刪 —— 連結是 soft FK，DB 不會自動清。

    留著的話那張發票的「已收金額」會一直把刪掉的錢算進去（2026-08-19 實測：
    刪掉三筆分期收款後，發票仍顯示已收滿額、尚欠 0）。這測掃原始碼確認刪除
    端點真的有那道 delete，比起完整跑一次 DB 便宜得多。
    """
    body = code_only(func_body(repo_src('routers/crm/finance.py'),
                               'async def delete_cash_entry('))
    # 收緊：只斷言出現 `CrmCashInvoiceLink` 的話，把 delete 換成 select（正是這條
    # 要防的退化）照樣通過。要求刪除語句本身與那個條件都在。
    assert '_sadelete(CrmCashInvoiceLink)' in body, '刪收支沒有清掉發票分配列'
    assert 'CrmCashInvoiceLink.cash_entry_id == entry_id' in body,         '清的不是這一筆收支的分配列'


def test_both_write_paths_sync_the_allocation_table():
    """建立與更新都要把 invoice_id 同步進分配表。

    🔴 只做更新路徑會這樣壞（2026-08-20 實測）：新開一筆掛了發票的收款，列表
    的「發票」欄看得到（讀 crm_cash_entries.invoice_id），但「關聯發票」面板是
    空的、那張發票的已收金額停在 0（兩者讀 crm_cash_invoice_links）。要有人多按
    一次編輯再存檔才會補上 —— 帳對不對取決於有沒有人多按那一下。
    """
    src = repo_src('routers/crm/finance.py')
    for fn in ('async def create_cash_entry(', 'async def update_cash_entry('):
        body = code_only(func_body(src, fn))
        assert '_sync_single_alloc' in body, f'{fn} 沒有同步發票分配表'


class TestInvoiceSettled:
    """「這張發票收齊了沒」的唯一正本（core.finance_logic.invoice_is_settled）。"""

    def test_exact_amount_is_settled(self):
        from core.finance_logic import invoice_is_settled
        assert invoice_is_settled(42000, 42000)

    def test_bank_fee_shortfall_still_counts_as_settled(self):
        """實收比面額少 30 元＝跨行匯費，不是欠款。

        394 張歷史發票裡有 42 張正好差 30 —— 如果這裡判成沒收齊，應收帳款會
        憑空長出 42 筆三十元的鬼債。
        """
        from core.finance_logic import invoice_is_settled
        assert invoice_is_settled(41970, 42000)
        assert invoice_is_settled(41950, 42000)      # 邊界：差 50 還算
        assert not invoice_is_settled(41949, 42000)  # 差 51 就不算

    def test_real_partial_payment_is_not_settled(self):
        """面額 144,900 只收 111,050（實際資料）—— 尚欠 33,850 必須留在應收帳款。

        🔴 舊規則「收到任何一毛就標已收款」讓這張整個從應收帳款消失，
        而同一畫面的發票列表照實顯示 outstanding 33,850。
        """
        from core.finance_logic import invoice_is_settled
        assert not invoice_is_settled(111050, 144900)

    def test_nothing_collected_is_not_settled(self):
        from core.finance_logic import invoice_is_settled
        assert not invoice_is_settled(0, 42000)


def test_settlement_rule_has_exactly_one_definition():
    """收款狀態的寫入全部走 _resettle_invoice，不准有人再自己判一次。

    🔴 這條同時決定發票狀態、它出不出現在應收帳款、分配面板的綠燈。
    2026-08-20 之前有三種寫法各自為政：編輯路徑「碰到就標已收款」、分配面板
    「完全不動」、刪除路徑「無條件打回未收款」。
    """
    src = repo_src('routers/crm/finance.py')
    for fn in ('async def create_cash_entry(', 'async def update_cash_entry(',
               'async def delete_cash_entry(', 'async def set_cash_entry_invoices('):
        body = code_only(func_body(src, fn))
        assert '_resettle_invoice' in body, f'{fn} 沒有走統一的收款狀態規則'


def test_edit_form_cannot_flatten_a_multi_invoice_allocation():
    """多張分配時，編輯視窗換發票要被擋 —— 不能靜默把其他幾張的已收清掉。"""
    body = code_only(func_body(repo_src('routers/crm/finance.py'),
                               'async def _sync_single_alloc('))
    assert 'len(existing) > 1' in body and '409' in body, \
        '_sync_single_alloc 又會把多張分配壓成一張了'
