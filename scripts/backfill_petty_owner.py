# -*- coding: utf-8 -*-
"""回填「費用歸屬人」—— owner 2026-08-17：「後期雜支是要王士源付款的」。

    .venv/Scripts/python.exe scripts/backfill_petty_owner.py --owner 王士源
    .venv/Scripts/python.exe scripts/backfill_petty_owner.py --owner 王士源 --prod --apply

做兩件事：
  1. `item='後期雜支'` 的列 → `owner_staff_id` = 指定的人
  2. 決定哪幾筆「還沒還」：拿 Sheet 表頭那個負數當目標，從這批列裡找**唯一**
     湊得出來的組合，其餘視為已結清。

🔴 第 2 步不是猜：實測 10 筆後期雜支裡，1023 種組合只有**一組**加起來等於 647
（240 + 67 + 340）。找不到唯一解就 abort —— 財務資料寧可不寫也不要寫錯。
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select, update as sa_update  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from config import load_settings  # noqa: E402
from db.models import CrmProjectExpense, CrmStaff  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--item", default="後期雜支")
    ap.add_argument("--owner", required=True, help="費用歸屬人姓名")
    ap.add_argument("--outstanding", type=int, default=647,
                    help="Sheet 表頭的未結金額（正數）")
    ap.add_argument("--prod", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    url = load_settings().get("database_url", "")
    url = (url.replace("/mediaguard_dev", "/mediaguard") if args.prod
           else (url if url.endswith("_dev") else url + "_dev"))
    print(f"DB: {url.split('@')[-1]}   模式: {'寫入' if args.apply else 'DRY-RUN'}")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        staff = (await session.execute(
            select(CrmStaff.id, CrmStaff.name)
            .where(CrmStaff.name == args.owner))).all()
        if len(staff) != 1:
            print(f"[ERROR] 人員「{args.owner}」找到 {len(staff)} 筆，需要剛好 1 筆")
            return 1
        owner_id, owner_name = staff[0]

        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.item == args.item,
                   CrmProjectExpense.claim_id.isnot(None))
            .order_by(CrmProjectExpense.expense_date))).scalars().all()
        print(f"\n{args.item} 共 {len(rows)} 列，合計 {sum(r.actual for r in rows):,}")
        for r in rows:
            print(f"   {str(r.expense_date)[:10]}  {r.actual:>6,}  {r.sub_item or ''}")

        if len(rows) > 24:      # 2^24 組合已經是上限，超過就別暴力解
            print("[ERROR] 列數過多，無法窮舉組合"); return 1
        hits = [c for n in range(1, len(rows) + 1)
                for c in itertools.combinations(rows, n)
                if sum(x.actual for x in c) == args.outstanding]
        print(f"\n湊出 {args.outstanding} 的組合：{len(hits)} 組")
        if len(hits) != 1:
            print("[ERROR] 不是唯一解 —— 不寫入。請 owner 指定哪幾筆未結。")
            await engine.dispose()
            return 1
        open_ids = {r.id for r in hits[0]}
        print("  唯一解（＝未結清）：")
        for r in hits[0]:
            print(f"     {str(r.expense_date)[:10]}  {r.actual:>6,}  {r.sub_item or ''}")

        if not args.apply:
            print(f"\n[DRY-RUN] 會把 {len(rows)} 列的歸屬人設為 {owner_name}，"
                  f"其中 {len(open_ids)} 列標為未結、{len(rows) - len(open_ids)} 列已結。")
            await engine.dispose()
            return 0

        for r in rows:
            await session.execute(
                sa_update(CrmProjectExpense)
                .where(CrmProjectExpense.id == r.id)
                .values(owner_staff_id=owner_id,
                        owner_settled=0 if r.id in open_ids else 1))
        await session.commit()
        print(f"\n[OK] {len(rows)} 列歸屬 {owner_name}；未結 {len(open_ids)} 列 "
              f"＝ {args.outstanding:,}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
