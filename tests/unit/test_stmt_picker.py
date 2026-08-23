# -*- coding: utf-8 -*-
"""對帳單預覽的「挑專案／挑發票／挑請款單」第二層視窗（owner 2026-08-21、08-23）。

原本兩格是 <select>：一格塞不下客戶/狀態/尚欠這些判斷用的資訊、沒有搜尋、
而且天生只能挑一張（合併匯款拆不開）。改成按鈕 → 第二層視窗。

2026-08-23 owner：「項目勾請款單時，可以讓我勾是哪一筆請款單的項目來對帳，
像是勾發票那樣」。收入列掛發票、支出列掛請款單 —— 方向互斥，所以兩者共用
**同一格、同一個挑選視窗**，差異收在 `_SIDES` 那張表裡。
"""
import re

from tests.unit._srcscan import repo_src  # noqa: E402

SRC = repo_src('frontend/tabs/finance/subviews/recon.js')   # 2026-08-22 從 banking.js 搬來
API = repo_src('routers/api_finance_stmt.py')

#: 挑選器本體（不含 _SIDES 設定與各 handler）
PICKER = SRC[SRC.index('_fr.stmtPickAlloc'):SRC.index('const _pickCand =')]


def _side_block(key):
    """`_SIDES` 裡某一側的設定區塊。"""
    s = SRC[SRC.index('const _SIDES = {'):SRC.index('const _sideOf =')]
    i = s.index(f'    {key}: {{')
    nxt = s.find('\n    pay: {', i + 1) if key == 'inv' else -1
    return s[i:nxt if nxt > 0 else len(s)]


def _sent_fields(anchor, end):
    """後端那一側實際送出去的欄名。"""
    i = API.index(anchor)
    return set(re.findall(r'"(\w+)":', API[i:API.index(end)]))


def test_the_two_cells_are_buttons_not_selects():
    assert 'data-stmt-proj' not in SRC, '專案格還是下拉'
    assert 'data-stmt-inv' not in SRC, '發票格還是下拉'
    assert 'stmtPickProj(' in SRC and 'stmtPickAlloc(' in SRC


def test_picker_has_its_own_overlay():
    """🔴 不能借 _wbModal 的容器 —— 那支是換掉同一個 body 的內容，
    第二層一開就把底下的預覽表洗掉，關掉後回不去。"""
    i = SRC.index('function _pickModal(')
    body = SRC[i:i + 1400]
    assert "finbank-pick-modal" in body
    assert "finbank-wb-modal" not in body, '第二層用到了第一層的容器'


# ── 兩側共用一份實作 ──────────────────────────────────────────

def test_both_directions_share_one_picker():
    """🔴 請款單那側**不可以**是抄一份發票挑選器。

    差異只有欄位、名詞與匯費形狀，其餘（搜尋、依金額接近度排序、複選、分配
    金額、footer 差額）完全一樣。抄一份的話，下次改排序規則就會只改到一邊。
    """
    assert SRC.count('_fr.stmtPickAlloc') >= 1
    assert 'stmtPickInv' not in SRC, '又出現了第二支只服務發票的挑選器'
    assert 'stmtPickPay' not in SRC, '又出現了第二支只服務請款單的挑選器'
    for key in ('inv', 'pay'):
        b = _side_block(key)
        for field in ('noun:', 'field:', 'idKey:', 'src:', 'grid:', 'perItemFee:'):
            assert field in b, f'_SIDES.{key} 少了 {field}'


def test_the_side_is_chosen_by_direction_only():
    """收入列掛發票、支出列掛請款單 —— 沒有第二個判準（分類不決定這件事）。"""
    i = SRC.index('const _sideOf =')
    seg = SRC[i:i + 200]
    assert 'r.amount > 0' in seg and 'r.amount < 0' in seg
    # 零元列兩者皆非 —— 要回 null 而不是硬歸一側
    assert 'null' in seg


def test_the_cell_renders_whichever_side_the_row_is():
    """一格兩用 —— 不是加第二欄（那會讓每一列都有一格永遠是空的）。"""
    assert 'function _stmtAllocCell(' in SRC
    assert '_stmtInvCell' not in SRC, '舊的單側格還在'
    i = SRC.index('function _stmtAllocCell(')
    body = SRC[i:i + 1400]
    assert '_sideOf(r)' in body, '沒有依方向決定要畫哪一側'
    assert 'S.field' in body and 'S.idKey' in body, '欄名寫死了某一側'


# ── 欄名要跟後端真的送的一致 ──────────────────────────────────

def test_each_side_reads_the_field_names_its_backend_actually_sends():
    """🔴 欄名對不上不會報錯 —— 只會在畫面上顯示 $0。

    實測撞到過：後端送 amount_total / company，前端讀 v.amount / v.client，
    每張發票的「面額」全都是 $0（而「已收」「尚欠」是對的，更難看出不對勁）。
    兩側各驗一次 —— 請款單那側是新的，最容易犯同一個錯。
    """
    from routers.crm.finance import collection_fields
    inv_sent = _sent_fields('open_invs.append({', 'open_invs.sort')
    inv_sent |= set(collection_fields(0, None, with_detail=True))
    pay_sent = _sent_fields('open_pays.append({', 'open_pays.sort')

    for key, sent in (('inv', inv_sent), ('pay', pay_sent)):
        used = set(re.findall(r'\bv\.(\w+)\b', _side_block(key)))
        assert used <= sent, (
            f'_SIDES.{key} 讀了後端沒送的欄位：{sorted(used - sent)}'
            f'（後端送 {sorted(sent)}）')

    # 共用那段只能碰**兩側都有**的欄位
    shared = set(re.findall(r'\bv\.(\w+)\b', PICKER))
    both = inv_sent & pay_sent
    assert shared <= both, (
        f'共用的挑選器讀了只有單側才有的欄位：{sorted(shared - both)}'
        f'（兩側都有的是 {sorted(both)}）')


def test_project_picker_reads_the_right_fields():
    i = API.index('projects = [{')
    sent = set(re.findall(r'"(\w+)":', API[i:i + 500]))
    j = SRC.index('_fr.stmtPickProj')
    picker = SRC[j:SRC.index('_fr.pickProjTake')]
    used = set(re.findall(r'\bp\.(\w+)\b', picker))
    assert used <= sent, f"前端讀了後端沒送的欄位：{sorted(used - sent)}（後端送 {sorted(sent)}）"


# ── 匯費：兩側形狀不同，這是刻意的 ────────────────────────────

def test_the_receipt_side_has_a_fee_per_row_and_the_payment_side_one_per_entry():
    """🔴 這不是還沒做完，是兩側的錢本來就不同：

    · 收款：匯出行對**每一張發票的匯款**各扣一次 → 逐張一格
    · 付款：跨行手續費對**這一筆匯出**收一次，涵蓋幾張請款單都一樣 → 整列一個

    做成一樣的話，付款側會變成「三張請款單各填一次手續費」，加總後從 expense
    多搬三倍出去 —— 而畫面上完全看不出來。
    """
    assert 'perItemFee: true' in _side_block('inv')
    assert 'perItemFee: false' in _side_block('pay')
    # 整列那個匯費存在 r.payment_fee（同 CashPaymentLinksPayload.fee 的形狀）
    assert 'r.payment_fee' in SRC
    assert '_fr.pickRowFee' in SRC, '付款側沒有整列的匯費輸入'


def test_the_payment_side_never_writes_a_per_item_fee():
    """請款單的分配項沒有 fee 欄（後端 CashPaymentLinkItem 也沒有）——
    送過去只會被 pydantic 丟掉，但前端會以為自己記住了。"""
    i = PICKER.index('take() {')
    body = PICKER[i:PICKER.index('_fr.pickClose();', i)]
    assert 'S2.perItemFee ? (a.fee || 0) : 0' in body, '付款側把逐項匯費也加進金額了'
    assert 'if (S2.perItemFee) one.fee' in body, '付款側也寫了 fee 欄'


def test_an_unlinked_row_sends_null_not_zero_for_the_entry_fee():
    """🔴 0 在關聯面板那條路是「把匯費清掉」的意思，null 才是「不認列」。
    這裡建的是新列，沒有舊值要清 —— 沒掛任何單就送 null。"""
    i = PICKER.index('r.payment_fee =')
    assert 'allocs.length && this.fee ? this.fee : null' in PICKER[i:i + 120]


# ── 既有的不變量 ──────────────────────────────────────────────

def test_frontend_does_not_re_derive_the_primary_record():
    """🔴 「主要那張＝金額最大」只在後端算（replace_invoice_allocs /
    replace_payment_allocs）。前端本來也算一份寫進 invoice_id —— 同一條規則兩種
    語言各一份，而且沒有人讀前端那份。/simplify 第 2 輪拿掉。"""
    assert 'b.amount > a.amount' not in SRC, '前端又自己推了一次主要那張'
    assert 'r[S2.field] = allocs' in SRC
    assert 'r.invoice_id =' not in PICKER
    assert 'r.payment_request_id =' not in PICKER


def test_save_and_apply_send_the_same_row_shape():
    """🔴 存草稿與匯入共用一支投影 _stmtRowPayload。

    各寫一份的下場已經發生過（2026-08-21）：匯入那份把「沒掛發票」寫成
    `invoices: null`，而後端那欄是 List 不收 null —— 只要對帳單裡有一列沒掛發票
    （幾乎每一份都有），整批匯入就 422；而存草稿那份寫 `|| []`，存得起來、匯不
    進去，兩條路各講各的。
    """
    assert 'const _stmtRowPayload = (x) => ({' in SRC
    for fn, what in (('_fr.stmtSaveDraft', '存草稿'), ('_fr.stmtApply', '匯入')):
        i = SRC.index(fn)
        body = SRC[i:i + 1600]
        assert '_stmtRowPayload' in body, f'{what}沒有走共用的投影'
        assert 'invoices: null' not in body
    # 兩欄都要是空陣列不是 null —— 後端那兩欄都是 List
    i = SRC.index('const _stmtRowPayload')
    seg = SRC[i:i + 700]
    assert 'invoices: x.invoices || []' in seg
    assert 'payments: x.payments || []' in seg, '請款單沒有進共用投影 —— 掛了會存不進去'


def test_the_draft_keeps_what_was_picked():
    """🔴 存草稿丟欄位＝重開後白做。原本 invoices 只存 id/amount，**fee 掉了**：
    填好匯費、存草稿、重開 → 那列又變成「還差 30」，人再填一次。"""
    i = API.index('"invoices": [{"invoice_id"')
    seg = API[i:i + 420]
    assert '"fee"' in seg, '草稿又把匯費丟掉了'
    assert '"payments"' in seg, '草稿沒存請款單'
    assert '"payment_fee"' in seg, '草稿沒存整列的匯費'
    # 讀回來那側
    j = API.index('r["invoices"] = x.get("invoices")')
    back = API[j:j + 260]
    assert 'r["payments"]' in back and 'r["payment_fee"]' in back, '草稿讀回來時漏了請款單'


def test_the_picker_can_multi_select_and_sorts_by_closeness():
    assert 'type="checkbox"' in PICKER, '挑選不是複選'
    # 金額接近的排前面：一筆入帳最可能就是「尚欠剛好等於這個數」的那張
    assert 'a.outstanding || 0) - target' in PICKER


def test_editing_a_row_does_not_redraw_the_whole_table():
    """🔴 owner 2026-08-21：「編輯後不要跳到最上面，要在原地」。

    整表重畫會重建 modal body → 捲軸回到頂端。一份三十列的對帳單改到第 20 列，
    每改一次就被彈回第 1 列（實測 scrollTop 1443 → 0）。改分類、挑專案、挑發票
    影響到的都只有那一列，逐列換就夠。
    """
    for sig, what in (('_fr.stmtCatChanged', '改分類'),
                      ('_fr.stmtPickProj', '挑專案'),
                      ('_fr.stmtPickAlloc', '挑發票／請款單')):
        i = SRC.index(sig)
        body = SRC[i:i + 6000]
        assert '_stmtRenderPreview()' not in body, f'{what}還在整表重畫 —— 捲軸會跳回頂端'
    assert 'function _stmtRefreshRow(' in SRC


def test_typing_in_the_entry_fee_does_not_lose_focus():
    """整列的匯費那格改值時只重畫 foot —— 重畫整個視窗會讓正在打字的那格失焦。"""
    i = SRC.index('_fr.pickRowFee')
    body = SRC[i:i + 400]
    assert 'finbank-pick-foot' in body
    assert 'this.render()' not in body and 'redraw()' not in body, \
        '改整列匯費時重畫了整份清單 —— 游標會跳掉'


def test_only_the_initial_render_draws_the_whole_table():
    """整表重畫只該發生在「解析完」與「開啟草稿」兩個入口。"""
    n = SRC.count('_stmtRenderPreview();')
    assert n == 2, f'整表重畫的呼叫點有 {n} 個（應該只有解析完與開啟草稿）'
