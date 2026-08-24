# -*- coding: utf-8 -*-
"""代開：請款單付掉了沒 ↔ 發票撥款了沒，必須是同一件事。

owner 2026-08-24 指著一張內部代開發票說「這一張要已撥款才對」—— 那張的請款單
早就是已付款（付款日 2026-05-14），發票卻停在待撥款。兩個畫面各自看起來都正常。

🔴 根因不只是那一列資料。「付掉 → 已撥款」這條規則原本 inline 寫在 batch_pay 與
   batch_unpay 裡，而**改請款單付款狀態的路徑不只那兩條**：
   resettle_payment_requests（依帳上實付重算，收支明細把匯款配到請款單時走它）
   也會標已付款／退回應付款，卻沒有那一段。實測生產目前 0 張走過那條路 ——
   那是「還沒有人這樣操作」，不是「不會發生」。

   同一天又補上第四條：update_payment 既能改 payment_status、也能改「指向哪張
   發票」。owner 有一張已付款的代開請款單標錯成台科大（真正匯出去的是古典魔力
   那筆）—— 改指的時候，原本那張也必須退回，否則兩張票會同時說錢已經匯出去了。
   規則全部收進 sync_remit_status，四條路徑共用。
"""
import asyncio

import pytest

from core.finance_logic import (INVOICE_PENDING_REMIT, INVOICE_RECEIVED,
                                INVOICE_REMITTED)
from routers.crm.finance import sync_remit_status
from tests.unit._srcscan import func_body, repo_src

SRC = "routers/crm/finance.py"


class _Inv:
    def __init__(self, status):
        self.id = "inv1"
        self.payment_status = status
        self.updated_at = None


class _Req:
    def __init__(self, status, category="發票代開"):
        self.category = category
        self.payment_status = status
        self.source_invoice_id = "inv1"
        self.invoice_number = ""


class _Session:
    def __init__(self, inv):
        self.inv = inv

    async def get(self, _model, ident):
        return self.inv if self.inv and ident == self.inv.id else None


def _run(req_status, inv_status, category="發票代開"):
    inv = _Inv(inv_status)
    asyncio.run(sync_remit_status(_Session(inv), _Req(req_status, category)))
    return inv.payment_status


# ── 正向 ──────────────────────────────────────────────────────

def test_paying_the_request_finishes_the_invoice():
    assert _run("已付款", INVOICE_PENDING_REMIT) == INVOICE_REMITTED


def test_the_legacy_collected_wording_also_advances():
    """改名期間新舊用詞並存 —— 舊資料標的是「已收款」。"""
    assert _run("已付款", INVOICE_RECEIVED) == INVOICE_REMITTED


# ── 反向（少了它會留下相反的矛盾）────────────────────────────

def test_unpaying_the_request_takes_the_invoice_back():
    """🔴 只補正向的話：取消付款後待請款區有一張沒付的單，發票卻說錢已經匯了。"""
    assert _run("應付款", INVOICE_REMITTED) == INVOICE_PENDING_REMIT


@pytest.mark.parametrize("st", ["應付款", "未付款", ""])
def test_any_not_paid_state_takes_it_back(st):
    assert _run(st, INVOICE_REMITTED) == INVOICE_PENDING_REMIT


# ── 不該動的情況 ──────────────────────────────────────────────

def test_a_non_passthrough_request_never_touches_an_invoice():
    """一般外包請款單付掉，不可以去動任何發票的狀態。"""
    assert _run("已付款", INVOICE_PENDING_REMIT, category="專案外包") \
        == INVOICE_PENDING_REMIT


def test_an_invoice_not_in_the_expected_state_is_left_alone():
    """發票還沒收到錢（未收款）就把請款單標已付款 —— 那是資料有問題，
    不是可以直接跳到已撥款。硬推的話會蓋掉「這張根本還沒收到錢」的事實。"""
    assert _run("已付款", "未收款") == "未收款"


def test_an_already_remitted_invoice_is_not_rewritten():
    """已撥款 + 已付款＝一致，不該再寫一次（會白白動 updated_at）。"""
    inv = _Inv(INVOICE_REMITTED)
    asyncio.run(sync_remit_status(_Session(inv), _Req("已付款")))
    assert inv.payment_status == INVOICE_REMITTED
    assert inv.updated_at is None, "狀態沒變卻還是寫了一次"


# ── 三條路徑都要走同一份 ──────────────────────────────────────

def test_every_path_that_changes_paidness_goes_through_the_rule():
    """🔴 這條是這支測試真正的重點。規則只有一份沒有用，要**每一條改付款狀態
    的路徑都呼叫它** —— 漏掉的那條就是 owner 這次撞到的那種矛盾的來源。"""
    src = repo_src(SRC)
    for fn in ("async def batch_pay(",
               "async def batch_unpay(",
               "async def resettle_payment_requests(",
               # 這支也會改 payment_status，而且會改「指向哪張發票」
               "async def update_payment("):
        body = func_body(src, fn)
        assert "sync_remit_status(session," in body, \
            f"{fn} 改了請款單的付款狀態卻沒收尾發票"


def test_the_rule_is_not_duplicated_inline_anywhere():
    """收成一份之後，就不該再有人在別處手寫 `inv.payment_status = 已撥款`。"""
    src = repo_src(SRC)
    body = func_body(src, "async def sync_remit_status(")
    # 指派給發票撥款狀態的地方，全檔只有正本裡那一個迴圈
    assert "inv.payment_status = state" in body
    assert body.count("payment_status = state") == 1, "正本裡自己就有兩處指派"
    assert src.count("payment_status = INVOICE_REMITTED") == 0, \
        "又有人在別處 inline 寫了一份撥款規則"
    assert src.count("payment_status = INVOICE_PENDING_REMIT") == 0, \
        "又有人在別處 inline 寫了一份退回規則"


# ── 改指到別張票 ──────────────────────────────────────────────

def test_repointing_a_request_takes_the_old_invoice_back():
    """🔴 把一張已付款的請款單改指到別張發票時，原本那張要退回待撥款。

    不退的話兩張票會同時說「錢已經匯出去了」，而只有一筆錢 —— 而且兩邊畫面
    各自都很正常。2026-08-24 owner 撞到的就是這個形狀。
    """
    old_inv, new_inv = _Inv(INVOICE_REMITTED), _Inv(INVOICE_PENDING_REMIT)
    old_inv.id, new_inv.id = "old", "new"

    class _S:
        async def get(self, _m, ident):
            return {"old": old_inv, "new": new_inv}.get(ident)

    req = _Req("已付款")
    req.source_invoice_id = "new"
    asyncio.run(sync_remit_status(_S(), req, previous_invoice_id="old"))
    assert new_inv.payment_status == INVOICE_REMITTED, "新指的那張沒收尾"
    assert old_inv.payment_status == INVOICE_PENDING_REMIT,         "原本那張還停在已撥款 —— 兩張票同時說錢匯出去了"

    # 規則寫對了還要真的接上去 —— update_payment 少傳一個參數，改指就不會退回，
    # 而上面那段直接測 sync_remit_status 是看不出來的。
    body = func_body(repo_src(SRC), "async def update_payment(")
    assert "prev_src = p.source_invoice_id" in body, "沒記下原本指向哪張票"
    assert "previous_invoice_id=prev_src" in body,         "記了卻沒傳給規則 —— 改指時原本那張不會退回"


def test_repointing_to_the_same_invoice_changes_nothing():
    inv = _Inv(INVOICE_REMITTED)
    req = _Req("已付款")
    asyncio.run(sync_remit_status(_Session(inv), req, previous_invoice_id="inv1"))
    assert inv.payment_status == INVOICE_REMITTED
    assert inv.updated_at is None, "沒改指卻還是寫了一次"


def test_an_old_invoice_that_was_not_remitted_is_left_alone():
    """原本那張如果本來就不是已撥款（例如還在未收款），不該被硬推成待撥款。"""
    old_inv, new_inv = _Inv("未收款"), _Inv(INVOICE_PENDING_REMIT)
    old_inv.id, new_inv.id = "old", "new"

    class _S:
        async def get(self, _m, ident):
            return {"old": old_inv, "new": new_inv}.get(ident)

    req = _Req("已付款")
    req.source_invoice_id = "new"
    asyncio.run(sync_remit_status(_S(), req, previous_invoice_id="old"))
    assert old_inv.payment_status == "未收款"
