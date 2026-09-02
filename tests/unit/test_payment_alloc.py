# -*- coding: utf-8 -*-
"""一筆匯款掛多張請款單（owner 2026-08-22）。

背景：公司出納統一匯款，備註寫姓名。實測生產 322 筆「結清請款單」的收支，
硬連結 payment_request_id **一筆都沒有** —— 因為 `payment_request_id` 是一對一，
而實務上一個人的多張請款單常併成一筆匯出，掛不上去。於是「哪一筆匯款結清了
哪一張請款單」在系統裡是空的。

🔴 付款側的容差方向跟收款側**相反**：
   收款：分配比實收**多** ≤ 容差 ＝ 收款方代扣匯費
   付款：分配比實付**少** ≤ 容差 ＝ **我們**付了跨行手續費
   實測「陳良君英配」8,010 對 8,000 的單、「邱靜右日配」7,010 對 7,000。
   共用同一支會把手續費判成「還有單沒掛」。
"""
import pytest

from core.finance_logic import FEE_TOLERANCE, alloc_verdict
from tests.unit._srcscan import finance_src


def _pay(paid, allocated):
    return alloc_verdict(paid, allocated, side="payment")


def test_exact_match():
    r = _pay(8000, 8000)
    assert r["state"] == "ok" and r["gap"] == 0 and r["fee"] == 0


def test_nothing_allocated():
    assert _pay(8010, 0)["state"] == "empty"


@pytest.mark.parametrize("paid,alloc,fee", [
    (8010, 8000, 10),      # 陳良君英配（生產真資料）
    (7010, 7000, 10),      # 邱靜右日配
    (12010, 12000, 10),    # 蔡佳佑
    (8000 + FEE_TOLERANCE, 8000, FEE_TOLERANCE),   # 剛好在容差邊界
])
def test_small_shortfall_is_a_transfer_fee(paid, alloc, fee):
    """🔴 這就是 87% 配不到的原因 —— 差的是手續費不是漏掛。"""
    r = _pay(paid, alloc)
    assert r["state"] == "fee"
    assert r["fee"] == fee, "沒把手續費金額算出來（呼叫端要拿它寫 bank_fee）"
    assert "手續費" in r["message"]


def test_big_shortfall_means_another_request_is_unlinked():
    r = _pay(130109, 8000)
    assert r["state"] == "under"
    assert r["fee"] == 0, "差這麼多不能當成手續費吞掉"
    assert "122,109" in r["message"], "沒告訴人還差多少"


def test_over_allocation():
    r = _pay(8000, 9000)
    assert r["state"] == "over" and r["fee"] == 0
    assert "1,000" in r["message"]


def test_the_two_sides_put_the_tolerance_on_opposite_edges():
    """🔴 同一組數字，兩側的判讀必須相反 —— 這是 side 參數存在的**唯一**理由。"""
    # 付 8,010、掛 8,000 → 付款側是「手續費」（我們付了跨行費）
    assert _pay(8010, 8000)["state"] == "fee"
    # 收 8,010、掛 8,000 → 收款側不是 fee（是少掛了一張）
    assert alloc_verdict(8010, 8000)["state"] != "fee"
    # 收 8,000、掛 8,010 → 收款側才是 fee（收款方代扣）
    assert alloc_verdict(8000, 8010)["state"] == "fee"


def test_one_ladder_not_two():
    """🔴 兩側是**同一座階梯**，只換容差方向與措辭。

    本來是兩支各寫一遍五個分支，於是同一個 key（diff）在兩側正負號相反，
    前端得靠 `check.received != null ? … : check.paid != null ? …` 猜自己
    在哪一側。這裡釘住：形狀完全一致、gap 語意一致（分配 − 實際）。
    """
    a, p = alloc_verdict(8000, 8010), _pay(8010, 8000)
    assert set(a) == set(p), f"兩側 key 不一致：{set(a) ^ set(p)}"
    assert a["gap"] == 10 and p["gap"] == -10, "gap 的語意兩側必須同向"
    assert (a["actual_label"], p["actual_label"]) == ("實收", "實付")
    assert "msg" not in p, "又漂回 msg 了"
    # state 的語意兩側一致：over ＝ 分配比實際多
    assert alloc_verdict(8000, 9000)["state"] == "over"
    assert _pay(8000, 9000)["state"] == "over"


def test_tolerance_comes_from_the_shared_constant():
    """容差只有一個正本 —— 兩側各寫死一個數字，調的時候一定漏一邊。"""
    from tests.unit._srcscan import code_only, finance_logic_src, func_body
    src = finance_logic_src()
    body = code_only(func_body(src, "def alloc_verdict("))
    assert "FEE_TOLERANCE" in body
    assert "50" not in body, "又寫死了一個容差數字"
    assert "def payment_alloc_verdict(" not in src, "付款側又長回自己那支"


# ── 連結表 ────────────────────────────────────────────────────────

def test_link_table_shape_mirrors_the_invoice_one():
    """兩張連結表除了「指向誰」與匯費之外要一模一樣。

    🔴 `fee` 是**刻意只有收款側有**，不是漏做（owner 2026-08-24）：
       · 收款：匯出行對**每一張發票的匯款**各扣一次 → 逐張存得下歸屬
       · 付款：跨行手續費是對**那一筆匯出**收一次，涵蓋幾張請款單都一樣
         → 掛在 crm_cash_entries.bank_fee，逐張存反而會被填成三倍
       付款側若哪天真的要逐張，先想清楚那筆錢是怎麼被扣的，再改這條測試。
    """
    from db.models import CrmCashInvoiceLink, CrmCashPaymentLink
    a = set(CrmCashInvoiceLink.__table__.c.keys())
    b = set(CrmCashPaymentLink.__table__.c.keys())
    assert a - {"invoice_id", "fee"} == b - {"payment_request_id"}, \
        f"欄位形狀跟收款那張不一致：{a ^ b}"
    assert "fee" in a, "收款側的逐張匯費不見了 —— 面板重開會畫不出那格"
    assert "fee" not in b, "付款側長出了逐張匯費（那筆錢是整筆匯出收一次的）"


def test_one_allocation_per_pair():
    """同一筆匯款不會對同一張請款單分配兩次（要改金額就改那一列）。"""
    from db.models import CrmCashPaymentLink
    uq = [c for c in CrmCashPaymentLink.__table__.constraints
          if getattr(c, "name", "") == "uq_cashpay_entry_request"]
    assert uq, "少了 unique 兜底"
    assert {c.name for c in uq[0].columns} == {"cash_entry_id",
                                               "payment_request_id"}


# ── 接線 ──────────────────────────────────────────────────────────

from tests.unit._srcscan import code_only, func_body, repo_src   # noqa: E402

# 內容本身（不是路徑）—— finance.py 2026-08-30 拆成四個檔，
# finance_src() 把它們串起來，斷言釘的是「這支函式做了什麼」不是它在哪。
SRC = finance_src()
JS = "frontend/tabs/crm/crm-cashbook.js"


def _body(fn):
    return code_only(func_body(SRC, fn))


def test_write_path_is_single():
    """只有一個地方寫連結表 —— 兩個寫入點就會有兩套規則。"""
    src = SRC
    assert src.count("session.add(CrmCashPaymentLink(") == 1


def test_replace_syncs_the_primary_request():
    """🔴 payment_request_id 是 classify_cash_entry 的硬連結優先序來源，
    不同步的話這筆收支的分類會漂（明明結清了請款單卻被當成別的）。"""
    body = _body("async def replace_payment_allocs(")
    assert "entry.payment_request_id = (max(rows, key=lambda x: x[1])[0].id" in body
    assert "if rows else None)" in body, "清空時沒有把主要請款單一起清掉"


def test_removed_links_are_resettled_too():
    """🔴 被移除的那些也要重算 —— 否則錢退掉了卻永遠掛已付款
    （發票那側踩過同一個坑）。"""
    body = _body("async def replace_payment_allocs(")
    assert "list(prev | {ap.id for ap, _a in rows})" in body


def test_partial_payment_does_not_mark_paid():
    """分次支付不能一碰到就標已付款 —— 那條規則會讓尾款靜默消失。"""
    body = _body("async def resettle_payment_requests(")
    # 🔴 判準走 core 的唯一正本，不在 router 裡 inline 一份 —— inline 的那份
    #    落在沒有單元測試的地方，容差規則一改就會靜靜分岔。
    assert "amount_is_settled(got, ap.amount)" in body
    assert "FEE_TOLERANCE" not in body, "又在 router 裡自己算容差了"
    assert '"應付款"' in body, "付了一部分沒有中間狀態"
    assert "ap.payment_date = None" in body, "連結拿光了沒有清掉付款日"


def test_amount_mismatch_is_not_a_gate():
    """金額對不上不擋（出納合併匯款＋手續費，硬擋會逼人亂填）。
    擋的只有一定錯的四件事，而且兩側是**同一支**在擋。
    """
    body = _body("async def resolve_allocs(")
    for must in ("不存在", "不同帳本", "分配金額要大於 0", "不可為空"):
        assert must in body, f"少擋了：{must}"
    assert "重複出現" in _body("_ALLOC_KINDS = {")
    assert "expense" not in body and "deposit" not in body,         "把實際入帳金額拿來當閘門了"


def test_both_sides_answer_the_same_error_the_same_way():
    """🔴 本來兩支各寫一份，於是同一個錯誤在兩邊回不同的 HTTP 碼
    （跨帳本 409 vs 422），空 id 一邊擋一邊靜靜丟掉。"""
    src = finance_src()
    for wrapper in ("async def resolve_invoice_allocs(",
                    "async def resolve_payment_allocs("):
        body = code_only(func_body(src, wrapper))
        assert "resolve_allocs(" in body, f"{wrapper} 又自己驗了一遍"
        assert "HTTPException" not in body, f"{wrapper} 自己長出第二套錯誤碼"


def test_fee_is_written_to_bank_fee():
    """手續費要進 bank_fee —— 那條路本來就把匯費算成管理費用與現金流出。

    認列那一步住在共用的 _write_allocs 裡（收付兩側同一條寫入流程），端點只
    負責把 fee 傳下去。

    🔴 2026-08-23 起**兩側都有** fee：收款側加了逐張發票的匯費（客戶匯三張的錢，
    可能只有其中一張被扣 30）。所以 _write_allocs 不能再寫死付款側那支 ——
    寫死的話，關聯面板重存一次就會把收款側補回 deposit 的值抹掉，帳戶淨流悄悄
    變回含匯費的數字，那一列從此在對帳工作台配不上而且畫面上看不出來。
    """
    assert "fee=req.fee" in _body("async def set_cash_entry_payments(")
    src = SRC
    # 兩側的規則收在 _ALLOC_KINDS 的 fee 欄。值是**一支吃 entry 的函式**，不是
    # (欄名, 純函式) —— 寫回的動作留在呼叫端的話，兩個呼叫端就寫成了兩種樣子
    # （實測過：一處用回傳的 bank_fee、一處丟掉回傳值自己再推一次）。
    assert '"fee": apply_payment_fee' in src, "付款側沒走總流出不變那支"
    assert '"fee": apply_receipt_fee' in src, "收款側沒走淨流入不變那支"
    body = _body("async def _write_allocs(")
    assert 'k["fee"](e, fee)' in body, "認列又寫死成單一側了"
    # 收款側要把各列的 fee 加總送下去（單一欄位表達不了逐張的匯費）
    inv = _body("async def set_cash_entry_invoices(")
    assert "int(it.fee or 0) for it in" in inv, "收款側沒有把逐張的匯費加總"


def test_month_close_is_respected():
    """月結鎖帳要擋 —— 前言（取列＋驗帳本＋擋鎖月）四個端點共用一支。"""
    assert "month_guard=True" in _body("async def _write_allocs("), "沒有擋已鎖月的月份"
    assert "_assert_month_open" in _body("async def _entry_for_alloc(")


def test_the_two_put_endpoints_are_one_body():
    """🔴 兩支 PUT 本來各寫一遍十二行，於是「存檔後回什麼」要對兩處看。"""
    for fn in ("async def set_cash_entry_payments(",
               "async def set_cash_entry_invoices("):
        body = _body(fn)
        assert "_write_allocs(" in body, f"{fn} 又自己寫了一遍"
        assert "session" not in body, f"{fn} 自己開了 session"


def test_the_four_alloc_endpoints_share_one_prologue():
    """取列 → 404 → 驗帳本 → 擋鎖月，本來在四個端點各抄一遍。

    抄四遍的代價：同一個 404 在別處寫成「找不到此收支紀錄」、在這裡寫成
    「收支明細不存在」，而 level="full" 那個雙保險漏掉一處就是權限缺口。
    """
    src = code_only(finance_src())
    seg = src[src.index("async def _entry_for_alloc("):]
    assert seg.count("session.get(CrmCashEntry, entry_id)") == 1,         "分配端點又自己去撈收支列了"


def test_ui_has_the_payment_box_on_expense_rows_only():
    js = repo_src(JS)
    assert "loadCashPaymentAllocs" in js and "cash-pay-box" in js
    seg = func_body(js, "function renderDetail(")
    assert "e.expense" in seg, "收入列也載入了付款分配（那是發票那區的事）"
    # 發票那側只在收入列載入；私帳整個不載（不開發票，連結一律走專案 ——
    # owner 2026-09-01，詳見 test_stmt_link 的 mine_replaces_invoice_links）
    assert "if (e.deposit && ledgerHasInvoices()) loadCashInvoiceAllocs(e.id);" in seg, \
        "動到了發票那側"


def test_ui_does_not_fetch_allocs_for_rows_that_have_none():
    """🔴 沒掛過就不要打那一趟。

    實測生產 907 筆支出列、有硬連結的是 **0 筆** —— 沒有這個閘門的話，
    每點一列支出就是一次白往返 ＋ 兩個查詢（連線池只有 50，這個月已經被
    用光過一次）。payment_request_id 的不變量由 replace_payment_allocs
    維持（有連結才非空），所以它就是「這列有沒有分配」。
    """
    js = repo_src(JS)
    seg = func_body(js, "function renderDetail(")
    assert "e.payment_request_id" in seg, "沒有閘門，每列都會去要一次分配"
    assert "_renderEmptyPayBox" in seg, "沒掛過的列要就地畫空狀態，不是留著載入中"



def test_ui_has_one_alloc_panel_not_two():
    """🔴 兩側（掛發票／掛請款單）是同一個面板，差異收在 _ALLOC_SIDES 裡。

    本來是兩份 ~150 行的複本，在**同一次 commit 裡**就漂開三處：
    狀態列的 msg vs message、預帶面額 vs 尚欠、候選有沒有濾掉結清的。
    代價不是多打一次字，是同一個詳情面板裡的兩塊長得不一樣。
    """
    js = repo_src(JS)
    for gone in ("_renderCashPayAllocs", "_renderCashAllocs",
                 "function _paySearch(", "async function _paySave("):
        assert gone not in js, f"付款側又長回自己的 {gone}"
    assert js.count("function _renderAllocs(") == 1
    assert js.count("function _allocSearch(") == 1
    assert js.count("async function _allocSave(") == 1
    # 兩側都要在表裡，而且各自的端點路徑不同
    seg = js[js.index("const _ALLOC_SIDES = {"):js.index("const _CASH_PAY =")]
    assert "invoice: {" in seg and "payment: {" in seg
    assert "path: 'invoices'" in seg and "path: 'payments'" in seg


def test_ui_only_the_payment_side_books_a_fee():
    """收款側的 PUT payload 沒有 fee 欄 —— 兩側都畫按鈕的話，收款側會出現
    一顆按了什麼都不會發生的按鈕。"""
    js = repo_src(JS)
    seg = js[js.index("const _ALLOC_SIDES = {"):js.index("const _CASH_PAY =")]
    assert seg.count("canBookFee") == 1, "匯費按鈕的開關不是只有一側"
    assert "canBookFee" in js[js.index("payment: {"):js.index("const _CASH_PAY =")]


def test_ui_offers_to_book_the_fee():
    js = repo_src(JS)
    assert "認列成匯費" in js
    assert "check.state === 'fee'" in js, "沒有只在判為手續費時才出現"


def test_ui_shares_one_status_line():
    """🔴 兩側共用同一支狀態列。

    本來付款側有一支 `_payStatusLine`，是 `_allocStatusLine` 的複本，差別只在
    `check.msg` vs `check.message` —— 那個差別本身就是後端兩支 verdict 回傳
    形狀漂開造成的。形狀對齊之後，複本沒有存在的理由。
    """
    js = repo_src(JS)
    assert "_payStatusLine" not in js, "付款側又長出一份自己的狀態列"
    assert js.count("function _allocStatusLine(") == 1
    seg = js[js.index("function _allocStatusLine("):]
    seg = seg[:seg.index(chr(10) + "function ")]
    assert "FEE_TOLERANCE" not in seg, "前端重寫了容差規則"
    assert "check.message" in seg
