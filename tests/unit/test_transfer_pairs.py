# -*- coding: utf-8 -*-
"""帳戶間轉存的配對與手續費拆分（owner 2026-08-24）。

一筆跨行轉存在帳上是兩列：轉出帳戶一列支出、轉入帳戶一列存入。本金是內部移動，
但銀行收的跨行手續費是真的離開公司了 —— 沒拆出來的話，現金流量表的
「期初＋淨流 ≠ 期末」就會差那幾十塊（實測 2026-08 差 30）。

🔴 這支存在的直接理由是一次差點造成的損害：第一版判準是「支出裡看起來含著
   手續費就減掉」，跑乾跑才發現那只對**剛被手動加過匯費**的兩列成立 ——
   歷史 37 筆早就是「支出＝本金、匯費分開記」，照那個判準會被各減 15
   （共 555 元，一路改到 2024 年）。判準一定要跟**配對的那一列**比。
"""
from datetime import datetime

from core.finance_logic import (FEE_TOLERANCE, TRANSFER_PAIR_WINDOW_DAYS,
                                transfer_pairs)


def _e(**kw):
    d = {"id": kw.get("id") or kw.get("summary") or "x", "deposit": 0, "expense": 0,
         "bank_fee": 0, "bank_account_id": None}
    d.update(kw)
    if isinstance(d.get("entry_date"), str):
        d["entry_date"] = datetime.strptime(d["entry_date"], "%Y-%m-%d")
    return d


def _out(amount, date, acct="A", fee=0, **kw):
    return _e(expense=amount, entry_date=date, bank_account_id=acct, bank_fee=fee, **kw)


def _in(amount, date, acct="B", **kw):
    return _e(deposit=amount, entry_date=date, bank_account_id=acct, **kw)


# ── 三種情況要分得開 ──────────────────────────────────────────

def test_already_split_is_not_flagged():
    """🔴 支出＝本金、匯費分開記 —— 這是**正確**的形狀，不可以再減一次。

    生產上有 37 筆長這樣（2024-02 起每月一筆）。把它們判成「要修」的話，
    每筆會被再減 15 元，而且完全沒有跡象。
    """
    pairs, un_o, un_i, _rev = transfer_pairs([
        _out(35000, "2026-07-10", fee=15), _in(35000, "2026-07-10"),
    ])
    assert len(pairs) == 1 and not un_o and not un_i
    assert pairs[0]["gap"] == 0
    assert pairs[0]["fee_inside"] is False, "已經拆好的又被判成要拆"


def test_fee_still_inside_the_expense_is_flagged():
    """支出 35,015 對上存入 35,000 → 那 15 還埋在支出裡，可以拆。"""
    pairs, un_o, un_i, _rev = transfer_pairs([
        _out(35015, "2026-08-09"), _in(35000, "2026-08-10"),
    ])
    assert len(pairs) == 1 and not un_o and not un_i
    assert pairs[0]["gap"] == 15
    assert pairs[0]["fee_inside"] is True


def test_the_real_august_case():
    """2026-08 生產實際：兩組跨行轉存，各差 15，合計 30 —— 就是現金流那 −30。"""
    pairs, un_o, un_i, _rev = transfer_pairs([
        _out(35015, "2026-08-09", acct="富邦"), _out(65389, "2026-08-09", acct="富邦"),
        _in(65374, "2026-08-10", acct="一銀"), _in(35000, "2026-08-10", acct="合庫"),
    ])
    assert len(pairs) == 2 and not un_o and not un_i
    assert sum(p["gap"] for p in pairs) == 30
    assert all(p["fee_inside"] for p in pairs)


# ── 配不到的要說配不到，不要硬湊 ──────────────────────────────

def test_a_gap_beyond_tolerance_is_not_a_fee():
    """差 30,000 是「還沒轉完」或根本不是一對 —— 硬當手續費會製造假帳。"""
    pairs, un_o, un_i, _rev = transfer_pairs([
        _out(65000, "2026-08-09"), _in(35000, "2026-08-09"),
    ])
    assert not pairs
    assert len(un_o) == 1 and len(un_i) == 1


def test_dates_too_far_apart_do_not_pair():
    far = TRANSFER_PAIR_WINDOW_DAYS + 5
    pairs, un_o, un_i, _rev = transfer_pairs([
        _out(35000, "2026-08-01"), _in(35000, f"2026-08-{1 + far:02d}"),
    ])
    assert not pairs and len(un_o) == 1 and len(un_i) == 1


def test_the_same_account_does_not_pair_with_itself():
    """🔴 同一個帳戶的一進一出不是轉存對。沒有這條的話，同額的收付會被
    亂配成一對，本金憑空互相抵銷。"""
    pairs, un_o, un_i, _rev = transfer_pairs([
        _out(35000, "2026-08-09", acct="富邦"), _in(35000, "2026-08-09", acct="富邦"),
    ])
    assert not pairs and len(un_o) == 1 and len(un_i) == 1


def test_missing_account_still_pairs():
    """舊資料很多沒填帳戶 —— 不該因此就配不到（帳戶只用來排除自己配自己）。"""
    pairs, _o, _i, _rev = transfer_pairs([
        _out(35000, "2026-08-09", acct=None), _in(35000, "2026-08-09", acct=None),
    ])
    assert len(pairs) == 1


# ── 一對一 ────────────────────────────────────────────────────

def test_each_inflow_is_used_at_most_once():
    """🔴 兩筆同額轉出只有一筆轉入 → 只能配一組，另一組是未成對。
    重複配的話，畫面會說兩組都好了，而帳上少了一整筆。"""
    pairs, un_o, un_i, _rev = transfer_pairs([
        _out(35000, "2026-08-09", id="o1"), _out(35000, "2026-08-09", id="o2"),
        _in(35000, "2026-08-09", id="i1"),
    ])
    assert len(pairs) == 1
    assert len(un_o) == 1 and not un_i


def test_the_closest_match_wins():
    """同一筆轉出有兩個候選 → 差額小的優先（差 0 的那個才是真的那一對）。"""
    pairs, _o, _i, _rev = transfer_pairs([
        _out(35000, "2026-08-09"),
        _in(34990, "2026-08-09", id="遠"), _in(35000, "2026-08-09", id="近"),
    ])
    assert len(pairs) == 1
    assert pairs[0]["in"]["id"] == "近"
    assert pairs[0]["gap"] == 0


def test_tolerance_is_the_shared_one():
    """容差用 FEE_TOLERANCE，不要再寫死一個 50。"""
    pairs, _o, _i, _rev = transfer_pairs([
        _out(35000 + FEE_TOLERANCE, "2026-08-09"), _in(35000, "2026-08-09"),
    ])
    assert len(pairs) == 1, "剛好等於容差要算得進去"
    pairs2, un_o, _i2, _r2 = transfer_pairs([
        _out(35000 + FEE_TOLERANCE + 1, "2026-08-09"), _in(35000, "2026-08-09"),
    ])
    assert not pairs2 and len(un_o) == 1, "超過容差就不算手續費"


# ── 沖正（銀行把匯款退回）────────────────────────────────────

def test_a_reversal_is_recognised_not_reported_as_unpaired():
    """🔴 生產實際（2025-06-16）：匯出 5,015、當天被退回 5,015，摘要寫「沖正網路」。

    兩筆在**同一個帳戶**，所以配不到轉出/轉入的對 —— 但它們互相抵銷，帳沒有錯。
    不認出來的話，這兩筆會永遠掛在「配不到」清單裡，讓人每次都以為有事沒處理。
    """
    pairs, un_o, un_i, rev = transfer_pairs([
        _out(5015, "2025-06-16", acct="富邦", summary="網路跨轉"),
        _in(5015, "2025-06-16", acct="富邦", summary="沖正網路"),
    ])
    assert len(rev) == 1, "沒認出沖正"
    assert not pairs and not un_o and not un_i, "沖正不該再出現在其他桶子裡"


def test_a_reversal_needs_the_marker_not_just_the_shape():
    """🔴 只看「同帳戶、同金額、方向相反」會出事：真實帳上同一天收一筆付一筆
    很常見（收了客戶 5,015、付了廠商 5,015），那兩筆毫無關係。
    要有沖正字樣才算，不然就是把兩筆無關的錢憑空抵掉。"""
    pairs, un_o, un_i, rev = transfer_pairs([
        _out(5015, "2025-06-16", acct="富邦", summary="網路跨轉"),
        _in(5015, "2025-06-16", acct="富邦", summary="客戶匯款"),
    ])
    assert not rev, "沒有沖正字樣卻被當成沖正"
    assert len(un_o) == 1 and len(un_i) == 1, "應該老實說配不到"


def test_a_reversal_does_not_eat_a_real_pair():
    """同一天既有沖正、也有真的轉存 → 沖正歸沖正，真的那組照樣配起來。"""
    pairs, un_o, un_i, rev = transfer_pairs([
        _out(5015, "2025-06-16", acct="富邦", summary="網路跨轉", id="被退回的"),
        _in(5015, "2025-06-16", acct="富邦", summary="沖正網路", id="沖正"),
        _out(5015, "2025-06-16", acct="富邦", summary="網路跨轉", id="真的"),
        _in(5000, "2025-06-16", acct="一銀", summary="跨行轉入", id="收到"),
    ])
    assert len(rev) == 1 and len(pairs) == 1
    assert pairs[0]["gap"] == 15 and pairs[0]["fee_inside"] is True
    assert not un_o and not un_i


def test_a_negative_gap_is_not_a_fee():
    """轉入比轉出多 → 那不是手續費（銀行不會倒貼）。配不到就說配不到。"""
    pairs, un_o, un_i, _rev = transfer_pairs([
        _out(35000, "2026-08-09"), _in(35015, "2026-08-09"),
    ])
    assert not pairs and len(un_o) == 1 and len(un_i) == 1
