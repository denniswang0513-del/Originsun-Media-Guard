# -*- coding: utf-8 -*-
"""發票的款項狀態「已付款」→「已轉撥」（owner 2026-08-20）。

用法（🔴 預設 dry-run）：
    python scripts/rename_invoice_paid_status.py            # dev 試算
    python scripts/rename_invoice_paid_status.py --apply    # dev 寫入
    python scripts/rename_invoice_paid_status.py --prod --apply

為什麼改名：這條線上的錢是**過路錢** —— 客戶把款匯進公司、公司再轉撥給代開人。
叫「已付款」會跟**請款單**的已付款（我們真的付掉一筆自己的費用）混在一起，
那是兩張表、兩件事。實測生產 183 張這個狀態全是 payment_type=付款，其中 181 張
是代開類，語意一致，可以整批改。

⚠ 只動 crm_invoices。crm_payment_requests 與零用金批次的「已付款」語意正確，不碰。
"""
import argparse
import asyncio
import io
import os
import sys
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from scripts._common import resolve_db_url  # noqa: E402

OLD, NEW = "已付款", "已轉撥"


async def run(prod: bool, apply: bool):
    url = resolve_db_url(prod)
    print(f"目標資料庫: {url.rsplit('/', 1)[-1]}   模式: "
          f"{'APPLY（會寫入！）' if apply else 'DRY-RUN（不寫入）'}")
    eng = create_async_engine(url, pool_pre_ping=True)
    from db.models import CrmInvoice, CrmPaymentRequest
    async with async_sessionmaker(eng, expire_on_commit=False)() as s:
        hit = (await s.execute(
            select(CrmInvoice).where(CrmInvoice.payment_status == OLD))).scalars().all()
        print(f"\n發票要改名的: {len(hit)} 張")
        print("  payment_type 分布:", dict(Counter(i.payment_type or "(空)" for i in hit)))
        print("  代開類:", sum(1 for i in hit if i.category and "代開" in i.category))
        others = [i for i in hit if not (i.category and "代開" in i.category)]
        if others:
            print("  非代開（一併改，語意仍是「錢已付出去」）:")
            for i in others[:8]:
                print(f"    {i.invoice_number or '(無號)':<14} {(i.title or '')[:20]:<22}"
                      f" cat={i.category} type={i.payment_type}")
        already = (await s.execute(
            select(CrmInvoice).where(CrmInvoice.payment_status == NEW))).scalars().all()
        print(f"已經是「{NEW}」的: {len(already)} 張（重跑安全）")

        # ⚠ 對照組：請款單那邊不可以被動到
        pr = (await s.execute(
            select(CrmPaymentRequest)
            .where(CrmPaymentRequest.payment_status == OLD))).scalars().all()
        print(f"\n請款單的「{OLD}」: {len(pr)} 筆 —— 這批**不動**（語意正確）")

        if not apply:
            print("\nDRY-RUN 結束 —— 沒有寫入任何東西。")
            await eng.dispose()
            return
        res = await s.execute(update(CrmInvoice)
                              .where(CrmInvoice.payment_status == OLD)
                              .values(payment_status=NEW))
        await s.commit()
        print(f"\n✅ 已改名 {res.rowcount} 張")
        after = Counter(
            (i.payment_status or "(空)") for i in
            (await s.execute(select(CrmInvoice))).scalars().all())
        print("  發票狀態分布:", dict(after.most_common()))
        pr_after = (await s.execute(
            select(CrmPaymentRequest)
            .where(CrmPaymentRequest.payment_status == OLD))).scalars().all()
        print(f"  請款單「{OLD}」仍是 {len(pr_after)} 筆（沒被誤動）")
    await eng.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.prod, a.apply))
