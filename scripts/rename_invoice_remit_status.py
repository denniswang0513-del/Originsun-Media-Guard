# -*- coding: utf-8 -*-
"""代開發票的款項狀態改名（owner 2026-08-21）。

    未收款 ──客戶匯錢進來──▶ 待撥款 ──應付帳款把請款單付掉──▶ 已撥款

兩筆改名：
  1. 「已轉撥」→「已撥款」（08-20 才從「已付款」改成已轉撥，這次配對「待撥款」）
  2. 代開發票的「已收款」→「待撥款」—— 只動 category 含「代開」的，一般收款
     發票的已收款是正確用詞，絕不能一起改。

🔴 只改資料的字，不動任何金額、日期、連結。程式那邊的常數已經先改好，
所以跑之前資料與程式會短暫不一致（後端讀得懂舊字，見 _kai_invoice_of 與
_sync_passthrough_request 的相容處理）。

用法：python scripts/rename_invoice_remit_status.py [dev|prod] [--apply]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TARGET = sys.argv[1] if len(sys.argv) > 1 else 'dev'
APPLY = '--apply' in sys.argv


async def main():
    import config
    url = (config.load_settings().get('database_url') or '')
    if TARGET == 'prod':
        url = url.replace('mediaguard_dev', 'mediaguard')
    elif 'mediaguard_dev' not in url:
        url = url.replace('/mediaguard', '/mediaguard_dev')
    print('DB:', url.rsplit('@', 1)[-1], '| APPLY' if APPLY else '| dry-run')

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from db.models import CrmInvoice
    eng = create_async_engine(url)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    async with Session() as s:
        invs = (await s.execute(select(CrmInvoice))).scalars().all()
        remit = [i for i in invs if i.payment_status == '已轉撥']
        kai_recv = [i for i in invs
                    if i.payment_status == '已收款'
                    and (i.category or '') and '代開' in i.category]
        plain = [i for i in invs
                 if i.payment_status == '已收款'
                 and not ((i.category or '') and '代開' in i.category)]
        print(f"\n已轉撥 → 已撥款      : {len(remit)} 張")
        print(f"代開的已收款 → 待撥款 : {len(kai_recv)} 張")
        for i in kai_recv:
            print(f"    {i.invoice_number or '（無號）'} {(i.title or '')[:22]} "
                  f"{i.amount_total:,}")
        print(f"一般發票的已收款（不動）: {len(plain)} 張")

        if APPLY:
            for i in remit:
                i.payment_status = '已撥款'
            for i in kai_recv:
                i.payment_status = '待撥款'
            await s.commit()
            print(f"\n✅ 已改 {len(remit) + len(kai_recv)} 張")
        else:
            print("\n（dry-run，沒有寫入；要寫請加 --apply）")
    await eng.dispose()


asyncio.run(main())
