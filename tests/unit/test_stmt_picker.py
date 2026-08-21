# -*- coding: utf-8 -*-
"""對帳單預覽的「挑專案／挑發票」第二層視窗（owner 2026-08-21）。

原本兩格是 <select>：一格塞不下客戶/狀態/尚欠這些判斷用的資訊、沒有搜尋、
而且天生只能挑一張（合併匯款拆不開）。改成按鈕 → 第二層視窗。
"""
from tests.unit._srcscan import repo_src  # noqa: E402

SRC = repo_src('frontend/tabs/finance/subviews/banking.js')


def test_the_two_cells_are_buttons_not_selects():
    assert 'data-stmt-proj' not in SRC, '專案格還是下拉'
    assert 'data-stmt-inv' not in SRC, '發票格還是下拉'
    assert 'stmtPickProj(' in SRC and 'stmtPickInv(' in SRC


def test_picker_has_its_own_overlay():
    """🔴 不能借 _wbModal 的容器 —— 那支是換掉同一個 body 的內容，
    第二層一開就把底下的預覽表洗掉，關掉後回不去。"""
    i = SRC.index('function _pickModal(')
    body = SRC[i:i + 1400]
    assert "finbank-pick-modal" in body
    assert "finbank-wb-modal" not in body, '第二層用到了第一層的容器'


def test_frontend_does_not_re_derive_the_primary_invoice():
    """🔴 「主要發票＝金額最大那張」只在後端算（replace_invoice_allocs）。

    前端本來也算一份寫進 invoice_id —— 同一條規則兩種語言各一份，而且沒有人讀
    前端那份（後端一律從分配表重推）。/simplify 第 2 輪拿掉。
    """
    assert 'b.amount > a.amount' not in SRC, '前端又自己推了一次主要發票'
    assert 'r.invoices = allocs' in SRC
    # 只有 invoices 一種表示法，連 invoice_id 都不再寫
    i = SRC.index('_fb.stmtPickInv')
    assert 'r.invoice_id =' not in SRC[i:SRC.index('_fb.pickInvToggle')]


def test_save_and_apply_send_the_same_row_shape():
    """🔴 存草稿與匯入共用一支投影 _stmtRowPayload。

    各寫一份的下場已經發生過（2026-08-21）：匯入那份把「沒掛發票」寫成
    `invoices: null`，而後端那欄是 List 不收 null —— 只要對帳單裡有一列沒掛發票
    （幾乎每一份都有），整批匯入就 422；而存草稿那份寫 `|| []`，存得起來、匯不
    進去，兩條路各講各的。
    """
    assert 'const _stmtRowPayload = (x) => ({' in SRC
    for fn, what in (('_fb.stmtSaveDraft', '存草稿'), ('_fb.stmtApply', '匯入')):
        i = SRC.index(fn)
        body = SRC[i:i + 1600]
        assert '_stmtRowPayload' in body, f'{what}沒有走共用的投影'
        assert 'invoices: null' not in body
    # 空陣列，不是 null —— 後端那欄是 List
    i = SRC.index('const _stmtRowPayload')
    assert 'invoices: x.invoices || []' in SRC[i:i + 400]


def test_invoice_picker_can_multi_select():
    i = SRC.index('_fb.stmtPickInv')
    body = SRC[i:SRC.index('_fb.pickInvToggle')]
    assert 'type="checkbox"' in body, '發票挑選不是複選'
    # 金額接近的排前面：一筆入帳最可能就是「尚欠剛好等於這個數」的那張
    assert 'a.outstanding || 0) - target' in body


def test_picker_reads_the_field_names_the_backend_actually_sends():
    """🔴 欄名對不上不會報錯 —— 只會在畫面上顯示 $0。

    實測撞到過：後端送 amount_total / company，前端讀 v.amount / v.client，
    每張發票的「面額」全都是 $0（而「已收」「尚欠」是對的，更難看出不對勁）。
    """
    import re
    api = repo_src('routers/api_finance.py')
    i = api.index('open_invs.append({')
    sent = set(re.findall(r'"(\w+)":', api[i:api.index('open_invs.sort')]))
    # collection_fields 攤平進來的四欄（collected/outstanding/settled/last_paid_date）
    from routers.crm.finance import collection_fields
    sent |= set(collection_fields(0, None, with_detail=True))

    j = SRC.index('_fb.stmtPickInv')
    picker = SRC[j:SRC.index('_fb.pickInvToggle')]
    used = set(re.findall(r'\bv\.(\w+)\b', picker))
    assert used <= sent, f"前端讀了後端沒送的欄位：{sorted(used - sent)}（後端送 {sorted(sent)}）"


def test_project_picker_reads_the_right_fields():
    import re
    api = repo_src('routers/api_finance.py')
    i = api.index('projects = [{')
    sent = set(re.findall(r'"(\w+)":', api[i:i + 500]))

    j = SRC.index('_fb.stmtPickProj')
    picker = SRC[j:SRC.index('_fb.pickProjTake')]
    used = set(re.findall(r'\bp\.(\w+)\b', picker))
    assert used <= sent, f"前端讀了後端沒送的欄位：{sorted(used - sent)}（後端送 {sorted(sent)}）"


def test_editing_a_row_does_not_redraw_the_whole_table():
    """🔴 owner 2026-08-21：「編輯後不要跳到最上面，要在原地」。

    整表重畫會重建 modal body → 捲軸回到頂端。一份三十列的對帳單改到第 20 列，
    每改一次就被彈回第 1 列（實測 scrollTop 1443 → 0）。改分類、挑專案、挑發票
    影響到的都只有那一列，逐列換就夠。
    """
    for sig, what in (('_fb.stmtCatChanged', '改分類'),
                      ('_fb.stmtPickProj', '挑專案'),
                      ('_fb.stmtPickInv', '挑發票')):
        i = SRC.index(sig)
        body = SRC[i:i + 4200]
        assert '_stmtRenderPreview()' not in body, f'{what}還在整表重畫 —— 捲軸會跳回頂端'
    assert 'function _stmtRefreshRow(' in SRC


def test_only_the_initial_render_draws_the_whole_table():
    """整表重畫只該發生在「解析完」與「開啟草稿」兩個入口。"""
    n = SRC.count('_stmtRenderPreview();')
    assert n == 2, f'整表重畫的呼叫點有 {n} 個（應該只有解析完與開啟草稿）'


def test_workbench_keeps_its_scroll_across_redraws():
    """對帳工作台每配對／註記／補記一列就整塊重畫（那些動作真的改了伺服器狀態，
    不能像匯入預覽那樣只換一列）→ 只能重畫前記位置、重畫後放回去。"""
    i = SRC.index('function _wbRender()')
    body = SRC[i:SRC.index('\n_fb.', i + 10)]
    assert "querySelectorAll('[data-wb-scroll]')" in body
    assert 'x.scrollTop = keep[i]' in body, '工作台重畫後沒有把捲軸放回去'
