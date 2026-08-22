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

from core.finance_logic import (FEE_TOLERANCE, alloc_verdict,
                                payment_alloc_verdict)


def test_exact_match():
    r = payment_alloc_verdict(8000, 8000)
    assert r["state"] == "ok" and r["diff"] == 0 and r["fee"] == 0


def test_nothing_allocated():
    r = payment_alloc_verdict(8010, 0)
    assert r["state"] == "empty"


@pytest.mark.parametrize("paid,alloc,fee", [
    (8010, 8000, 10),      # 陳良君英配（生產真資料）
    (7010, 7000, 10),      # 邱靜右日配
    (12010, 12000, 10),    # 蔡佳佑
    (8000 + FEE_TOLERANCE, 8000, FEE_TOLERANCE),   # 剛好在容差邊界
])
def test_small_shortfall_is_a_transfer_fee(paid, alloc, fee):
    """🔴 這就是 87% 配不到的原因 —— 差的是手續費不是漏掛。"""
    r = payment_alloc_verdict(paid, alloc)
    assert r["state"] == "fee"
    assert r["fee"] == fee, "沒把手續費金額算出來（呼叫端要拿它寫 bank_fee）"
    assert "手續費" in r["msg"]


def test_big_shortfall_means_another_request_is_unlinked():
    r = payment_alloc_verdict(130109, 8000)
    assert r["state"] == "under"
    assert r["fee"] == 0, "差這麼多不能當成手續費吞掉"
    assert "122,109" in r["msg"], "沒告訴人還差多少"


def test_over_allocation():
    r = payment_alloc_verdict(8000, 9000)
    assert r["state"] == "over" and r["fee"] == 0
    assert "1,000" in r["msg"]


def test_direction_is_opposite_to_the_receipt_side():
    """🔴 兩支不能共用。同一組數字，兩側的判讀必須相反。"""
    # 付 8,010、掛 8,000 → 付款側是「手續費」
    assert payment_alloc_verdict(8010, 8000)["state"] == "fee"
    # 收 8,010、掛 8,000 → 收款側不是 fee（是少掛了）
    assert alloc_verdict(8010, 8000)["state"] != "fee"
    # 收 8,000、掛 8,010 → 收款側才是 fee
    assert alloc_verdict(8000, 8010)["state"] == "fee"


def test_tolerance_comes_from_the_shared_constant():
    """容差只有一個正本 —— 兩側各寫死一個數字，調的時候一定漏一邊。"""
    from tests.unit._srcscan import code_only, func_body, repo_src
    body = code_only(func_body(repo_src("core/finance_logic.py"),
                               "def payment_alloc_verdict("))
    assert "FEE_TOLERANCE" in body
    assert "50" not in body, "又寫死了一個容差數字"


# ── 連結表 ────────────────────────────────────────────────────────

def test_link_table_shape_mirrors_the_invoice_one():
    from db.models import CrmCashInvoiceLink, CrmCashPaymentLink
    a = set(CrmCashInvoiceLink.__table__.c.keys())
    b = set(CrmCashPaymentLink.__table__.c.keys())
    assert a - {"invoice_id"} == b - {"payment_request_id"}, \
        f"欄位形狀跟收款那張不一致：{a ^ b}"


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

SRC = "routers/crm/finance.py"
JS = "frontend/tabs/crm/crm-cashbook.js"


def _body(fn):
    return code_only(func_body(repo_src(SRC), fn))


def test_write_path_is_single():
    """只有一個地方寫連結表 —— 兩個寫入點就會有兩套規則。"""
    src = repo_src(SRC)
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
    assert "got + FEE_TOLERANCE >= int(ap.amount or 0) and got > 0" in body
    assert '"應付款"' in body, "付了一部分沒有中間狀態"
    assert "ap.payment_date = None" in body, "連結拿光了沒有清掉付款日"


def test_amount_mismatch_is_not_a_gate():
    """金額對不上不擋（出納合併匯款＋手續費，硬擋會逼人亂填）。
    擋的只有一定錯的三件事。"""
    body = _body("async def resolve_payment_allocs(")
    for must in ("請款單不存在", "屬於另一本帳", "分配金額要大於 0", "重複出現"):
        assert must in body, f"少擋了：{must}"
    assert "expense" not in body, "把實付金額拿來當閘門了"


def test_fee_is_written_to_bank_fee():
    """手續費要進 bank_fee —— 那條路本來就把匯費算成管理費用與現金流出。"""
    body = _body("async def set_cash_entry_payments(")
    assert "e.bank_fee = fee or None" in body
    assert "匯費不能是負的" in body


def test_month_close_is_respected():
    body = _body("async def set_cash_entry_payments(")
    assert "_assert_month_open" in body


def test_ui_has_the_payment_box_on_expense_rows_only():
    js = repo_src(JS)
    assert "loadCashPaymentAllocs" in js and "cash-pay-box" in js
    assert "if (e.expense) loadCashPaymentAllocs(e.id);" in js, \
        "收入列也載入了付款分配（那是發票那區的事）"
    assert "if (e.deposit) loadCashInvoiceAllocs(e.id);" in js, "動到了發票那側"


def test_ui_offers_to_book_the_fee():
    js = repo_src(JS)
    assert "認列成匯費" in js
    assert "check.state === 'fee'" in js, "沒有只在判為手續費時才出現"


def test_ui_does_not_reimplement_the_verdict():
    """判讀規則的正本在後端 —— 前端重寫一份就會有兩個答案。"""
    js = repo_src(JS)
    i = js.index("function _payStatusLine(")
    seg = js[i:js.index("\nfunction ", i + 10)]
    assert "FEE_TOLERANCE" not in seg and "check.msg" in seg


def test_fee_is_moved_out_of_expense_not_added_on_top():
    """🔴 我第一版寫錯的地方（2.4.143 上線一小時後才發現）。

    bank_fee 是**外加**在 expense 之上的：
        cash_entry_flow = deposit − expense − bank_fee − claim
        列表的支出欄也是顯示 expense + bank_fee
    只寫 bank_fee=10 而不動 expense=8,010，帳上就變成流出 8,020 ——
    銀行只少了 8,010，當場多一筆 10 元的勾稽差額（正好是我這幾天在追的那種）。
    正解是從 expense 裡**搬**出來，用「總流出不變」當不變量（順便冪等）。
    """
    body = _body("async def set_cash_entry_payments(")
    assert "total_out = int(e.expense or 0) + int(e.bank_fee or 0)" in body
    assert "e.expense = total_out - fee" in body, "只寫了 bank_fee 沒有從 expense 扣掉"
    assert "比這筆的總流出" in body, "匯費比總流出還大時沒有擋"
