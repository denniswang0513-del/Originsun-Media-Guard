# -*- coding: utf-8 -*-
"""應收帳款只算「收款方向」的發票。

🔴 2026-08-19 匯 394 筆歷史發票後才看得出來的缺陷（以前系統裡 0 筆發票）：

`/receivables/summary` 原本只用 `payment_status NOT IN ('已收款','作廢')` 過濾。
發票的 payment_type 有 收款／付款 兩種，「付款」是代開發票 —— 錢是我們要付出去的。
「已付款」不在那個排除清單裡，於是 183 張代開付款發票被算成別人欠我們的錢：
應收 13,554,350 中有 10,656,093（79%）是假的，虛增 4.7 倍。

三表引擎（core/finance_logic.ar_open_invoices / iter_revenue_invoices）本來就有
這層過濾 —— 這條測試同時釘住那兩支，避免哪天有人「統一」時把它拿掉。
"""
import re

from core.finance_logic import ar_open_invoices, iter_revenue_invoices


def _inv(**kw):
    base = dict(id="i1", issue_status="已開立", payment_status="未收款",
                payment_type="收款", category="專案", amount_total=1000,
                invoice_date="2026-08-01")
    base.update(kw)
    return base


def _src(rel):
    import io
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return io.open(os.path.join(root, rel), encoding="utf-8").read()


def test_receivables_query_filters_payment_direction():
    """CRM 應收帳款子視圖的查詢要把 payment_type 釘成收款（NULL 視為收款）。"""
    src = _src("routers/crm/finance.py")
    i = src.index("async def receivables_summary(")
    body = src[i:i + 3000]
    assert 'CrmInvoice.payment_type == "收款"' in body, \
        "應收帳款沒有過濾收款方向 —— 代開的付款發票會被算成應收"
    assert "CrmInvoice.payment_type.is_(None)" in body, \
        "payment_type 為 NULL 的舊列要視為收款（與 _to_invoice_dict 的 `or 收款` 一致）"


def test_ar_open_invoices_excludes_payment_direction():
    """三表的 BS 應收：付款方向不計。"""
    assert len(ar_open_invoices([_inv()])) == 1
    assert ar_open_invoices([_inv(payment_type="付款")]) == []


def test_ar_open_invoices_treats_missing_direction_as_收款():
    assert len(ar_open_invoices([_inv(payment_type=None)])) == 1


def test_revenue_excludes_payment_direction():
    """代開發票的手續費屬業外，本體不認列營業收入。"""
    mset = {"2026-08"}
    assert len(list(iter_revenue_invoices([_inv()], mset))) == 1
    assert list(iter_revenue_invoices([_inv(payment_type="付款")], mset)) == []


def test_voided_and_collected_are_still_excluded():
    """原本就有的兩條排除不能因為加了方向過濾而掉了。"""
    assert ar_open_invoices([_inv(issue_status="作廢")]) == []
    assert ar_open_invoices([_inv(payment_status="作廢")]) == []


def test_no_other_receivables_query_forgot_the_filter():
    """整份 crm/finance.py 只有一處用 `payment_status NOT IN (收現狀態, 作廢)` 當
    應收條件；哪天多一處，這條會逼它一起帶方向過濾。

    排除清單用常數 INVOICE_COLLECTED 而不是逐字列 —— 代開改名之後「收到錢」
    有三種說法（已收款／待撥款／已撥款），各處自己寫字串遲早漏掉一個，漏掉的
    那個就會被算回應收（「已轉撥」漏掉那次把應收虛增成 4.7 倍）。
    """
    src = _src("routers/crm/finance.py")
    hits = re.findall(r'payment_status\.notin_\(\[\*INVOICE_COLLECTED, "作廢"\]\)', src)
    assert len(hits) == 1, f"應收條件出現 {len(hits)} 處，新增的那處也要過濾 payment_type"
