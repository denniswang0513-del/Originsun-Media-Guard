# -*- coding: utf-8 -*-
"""既有的單張 `crm_cash_entries.invoice_id` → 補進 crm_cash_invoice_links。

    .venv/Scripts/python.exe scripts/backfill_cash_invoice_links.py           # dry-run
    .venv/Scripts/python.exe scripts/backfill_cash_invoice_links.py --apply
    .venv/Scripts/python.exe scripts/backfill_cash_invoice_links.py --prod --apply

多對多的分配表上線前，一筆收款只能掛一張發票（invoice_id 欄）。這支把那些既有
連結原樣搬進分配表，分配金額＝**該筆收款的實收金額**（單張的情況下這就是全部）。

冪等：已經有分配列的收款直接跳過（不覆蓋人工調整過的分配）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from db.models import CrmCashEntry, CrmCashInvoiceLink, CrmInvoice  # noqa: E402
from scripts.import_cashbook import resolve_db_url  # noqa: E402

ENTITY = "parent"


async def run(prod: bool, apply: bool):
    url = resolve_db_url(prod)
    dbname = url.rsplit("/", 1)[-1]
    eng = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    async with Session() as s:
        have = set((await s.execute(
            select(CrmCashInvoiceLink.cash_entry_id))).scalars().all())
        inv_ids = set((await s.execute(select(CrmInvoice.id))).scalars().all())
        entries = (await s.execute(
            select(CrmCashEntry).where(CrmCashEntry.entity == ENTITY,
                                       CrmCashEntry.invoice_id.isnot(None)))).scalars().all()

        todo, skip_have, skip_orphan, skip_noamt = [], 0, [], 0
        for e in entries:
            if e.id in have:
                skip_have += 1
            elif e.invoice_id not in inv_ids:
                skip_orphan.append(e)
            elif not (e.deposit or 0):
                # 支出列連著發票（代開付出去那側）—— 分配表講的是收款，不搬
                skip_noamt += 1
            else:
                todo.append(e)

        print("=" * 68)
        print(f"目標資料庫: {dbname}   模式: {'寫入 (--apply)' if apply else 'DRY-RUN'}")
        print("=" * 68)
        print(f"有 invoice_id 的收支: {len(entries)}")
        print(f"  已有分配列，跳過: {skip_have}")
        print(f"  發票已不存在（孤兒連結）: {len(skip_orphan)}")
        print(f"  非收款列（無實收金額）: {skip_noamt}")
        print(f"  **要補分配列: {len(todo)}**")
        for e in skip_orphan[:8]:
            print(f"     [孤兒] {e.entry_date} {e.summary[:24]} invoice_id={e.invoice_id}")

        if not apply:
            print("\nDRY-RUN 結束 —— 沒有寫入任何東西。")
            await eng.dispose()
            return
        for e in todo:
            s.add(CrmCashInvoiceLink(id=uuid.uuid4().hex, cash_entry_id=e.id,
                                     invoice_id=e.invoice_id,
                                     amount=int(e.deposit or 0)))
        await s.commit()
        print(f"\n[OK] 已補 {len(todo)} 筆分配列")
        n = len((await s.execute(select(CrmCashInvoiceLink.id))).scalars().all())
        print(f"[驗] 分配表現有 {n} 列")
    await eng.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.prod, a.apply))
