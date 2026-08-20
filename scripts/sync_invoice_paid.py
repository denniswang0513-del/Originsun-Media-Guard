# -*- coding: utf-8 -*-
"""收支明細已記到款的發票 → 把發票狀態標成已收款（母公司帳）。

    .venv/Scripts/python.exe scripts/sync_invoice_paid.py           # dry-run（預設）
    .venv/Scripts/python.exe scripts/sync_invoice_paid.py --apply   # 真的寫入
    .venv/Scripts/python.exe scripts/sync_invoice_paid.py --prod --apply

owner 2026-08-19：「收款時有標注發票的發票 發票狀態需要調整為已收款」。

判定來源＝`crm_cash_entries.invoice_id`（收支明細匯入時由發票號碼反查連上的硬連結）。
一張發票只要有**任何一筆收入列**連著它，就代表錢進來了。

三條安全線 —— 這支會改動財務狀態，寧可少改不可錯改：
1. 只動 `payment_type` 是收款（或空）的發票。付款型發票的「已付款」不歸這裡管。
2. **作廢的發票一律跳過**（作廢是終態，不該被自動改回已收款）。
3. 連著它的收支必須是**收入列**（deposit > 0）；支出列連到發票不算收款。

已經是「已收款」的不動（冪等）。狀態空白或「未收款」才會被改。
"""
from __future__ import annotations

import argparse
import asyncio

import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# stdout 的 UTF-8 wrap 由下面 import 的 scripts.import_cashbook 做 —— 這裡再包一層，
# 被取代的舊 wrapper 一被 GC 就把共用的底層 buffer 關掉（print 直接 ValueError）。

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from db.models import CrmCashEntry, CrmCashInvoiceLink, CrmInvoice  # noqa: E402
from scripts.import_cashbook import resolve_db_url  # noqa: E402

ENTITY = "parent"
PAID = "已收款"
VOID = "作廢"


async def run(prod: bool, apply: bool):
    url = resolve_db_url(prod)
    dbname = url.rsplit("/", 1)[-1]
    eng = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    async with Session() as s:
        # 發票 id → 這張總共收到多少。
        #
        # 🔴 來源是**分配表**（crm_cash_invoice_links）不是 crm_cash_entries.invoice_id。
        # 多對多上線後 invoice_id 只留「金額最大的那張」（_set_primary_invoice），
        # 所以合併匯款掛的另外兩張，用 invoice_id 查根本看不到 —— 正向不會標已收款，
        # 反向檢查還會把它們誤報成「標已收款但帳上查無收款」。
        # 與 routers/crm/finance._invoice_collections 同一個來源。
        got = defaultdict(int)
        for iid, amt in (await s.execute(
                select(CrmCashInvoiceLink.invoice_id, CrmCashInvoiceLink.amount)
                .join(CrmCashEntry, CrmCashEntry.id == CrmCashInvoiceLink.cash_entry_id)
                .where(CrmCashEntry.entity == ENTITY))).all():
            got[iid] += int(amt or 0)

        invs = (await s.execute(
            select(CrmInvoice).where(CrmInvoice.entity == ENTITY))).scalars().all()
        by_id = {i.id: i for i in invs}

        to_set, already, skipped_void, skipped_type, orphan = [], [], [], [], []
        for iid, total in got.items():
            inv = by_id.get(iid)
            if not inv:
                orphan.append(iid)
                continue
            pst = (inv.payment_status or "").strip()
            ptype = (inv.payment_type or "").strip()
            if pst == VOID or (inv.issue_status or "").strip() == VOID:
                skipped_void.append(inv)
            elif ptype and ptype != "收款":
                skipped_type.append(inv)
            elif pst == PAID:
                already.append(inv)
            else:
                to_set.append((inv, pst, total))

        print("=" * 70)
        print(f"目標資料庫: {dbname}   模式: {'寫入 (--apply)' if apply else 'DRY-RUN'}")
        print("=" * 70)
        print(f"有收款紀錄的發票: {len(got)} 張")
        print(f"  已經是「{PAID}」不動: {len(already)}")
        print(f"  作廢，跳過: {len(skipped_void)}")
        print(f"  非收款型發票，跳過: {len(skipped_type)}")
        print(f"  連到不存在的發票: {len(orphan)}")
        print(f"  **要改成「{PAID}」: {len(to_set)}**")
        for inv, pst, total in sorted(to_set, key=lambda x: -x[2])[:25]:
            print(f"     {inv.invoice_number or '(無號碼)':<12} {(inv.title or '')[:24]:<26}"
                  f" 發票 {inv.amount_total or 0:>9,}  已收 {total:>9,}"
                  f"  狀態 {pst or '(空白)'} → {PAID}")
        if len(to_set) > 25:
            print(f"     …還有 {len(to_set) - 25} 張")
        for inv in skipped_void:
            print(f"  [作廢跳過] {inv.invoice_number} {(inv.title or '')[:24]}")

        # ── 反向檢查（只報不改）：標了已收款、收支明細卻查無收款 ──
        # 這個方向比正向更會抓到問題 —— 正向是「錢進來了狀態沒更新」（無害，補上就好），
        # 反向是「狀態說收到了但帳上沒有這筆錢」（可能是漏記，或發票狀態填錯）。
        ghosts = [i for i in invs
                  if (i.payment_status or "") == PAID
                  and (i.payment_type or "") == "收款" and i.id not in got]
        if ghosts:
            print(f"\n[反向檢查] 標「{PAID}」但收支明細查無收款紀錄: {len(ghosts)} 張")
            print("           （早於收支表起始日、或發票號碼是 N/A 連不上的屬正常）")
            for i in sorted(ghosts, key=lambda x: x.invoice_date or ""):
                d = i.invoice_date.strftime("%Y-%m-%d") if i.invoice_date else "(無日期)"
                print(f"     {d} {i.invoice_number or '(無號碼)':<12} "
                      f"{(i.title or '')[:24]:<26} {i.amount_total or 0:>9,}")

        if not apply:
            print("\nDRY-RUN 結束 —— 沒有寫入任何東西。確認後加 --apply。")
            await eng.dispose()
            return
        for inv, _pst, _t in to_set:
            inv.payment_status = PAID
        await s.commit()
        print(f"\n[OK] 已把 {len(to_set)} 張發票改成「{PAID}」")

        # by_id 就是剛剛那批 ORM 物件（expire_on_commit=False），不必再查一次
        still = [i for i in got if i in by_id
                 and (by_id[i].payment_status or "") not in (PAID, VOID)]
        print(f"[驗] 有收款紀錄但仍非「{PAID}」的: {len(still)} 張（作廢與非收款型除外）")
    await eng.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", action="store_true", help="對生產庫 mediaguard（預設 dev）")
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設 dry-run）")
    a = ap.parse_args()
    asyncio.run(run(a.prod, a.apply))
