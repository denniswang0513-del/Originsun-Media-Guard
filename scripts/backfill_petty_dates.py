# -*- coding: utf-8 -*-
"""回填 37 列沒有日期的零用金單據（owner 2026-08-17）。

    .venv/Scripts/python.exe scripts/backfill_petty_dates.py --csv petty.csv --prod
    .venv/Scripts/python.exe scripts/backfill_petty_dates.py --csv petty.csv --prod --apply

owner 說「直接取 Google Sheet 日期的中間任一日期填寫就可以」。比「全部塞同一天」
更準的是：**沿用該列在 Sheet 裡上方最近一列的日期** —— 那一列的位置本來就隱含
日期（人是照時序往下填的），而且匯入時就已經算過這個值（read_sheet 的
`_order`，當時只拿來排序、沒寫進 DB）。落在「中間」的要求自動滿足，而且每一列
落在它真正該在的區間。

配對靠 (收款人, 金額, 摘要) —— 三者相同的重複列會被跳過並列出來，不硬塞。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from config import load_settings  # noqa: E402
from db.models import CrmProjectExpense, CrmStaff  # noqa: E402
from import_petty_cash import read_sheet  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--prod", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    _, details = read_sheet(args.csv)
    # 沒填日期的列 → 它的 `_order`（＝上方最近一列的日期）就是要補的值
    blanks = [d for d in details if d["date"] is None]
    print(f"Sheet 沒有日期的列：{len(blanks)}")

    url = load_settings().get("database_url", "")
    url = (url.replace("/mediaguard_dev", "/mediaguard") if args.prod
           else (url if url.endswith("_dev") else url + "_dev"))
    print(f"DB: {url.split('@')[-1]}   模式: {'寫入' if args.apply else 'DRY-RUN'}\n")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        names = dict((await session.execute(
            select(CrmStaff.id, CrmStaff.name))).all())
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.expense_date.is_(None),
                   CrmProjectExpense.item.isnot(None)))).scalars().all()
        print(f"DB 沒有日期的零用金列：{len(rows)}")

        # key = (收款人名, 金額, 摘要前 40 字)
        def key_db(r):
            who = names.get(r.staff_id, "") or (r.payee or "")
            return (who, r.actual, (r.sub_item or "")[:40])

        def key_sheet(d):
            return (d["payee_name"], d["amount"], (d["summary"] or "")[:40])

        by_key = defaultdict(list)
        for d in blanks:
            by_key[key_sheet(d)].append(d)

        plan, dupes, misses = [], [], []
        for r in rows:
            k = key_db(r)
            cands = by_key.get(k, [])
            if not cands:
                misses.append(r)
                continue
            if len(cands) > 1:
                # 多筆候選但要補的日期都一樣 → 結果沒有歧義，照補
                # （實測那 2 列是互為重複的「蘇家弘 45 停車費」，同一段區間）
                whens = {c["_order"] for c in cands}
                if len(whens) > 1:
                    dupes.append((k, len(cands)))
                    continue
            plan.append((r, cands[0]["_order"]))

        print(f"\n可唯一對上：{len(plan)} 列")
        for r, when in plan[:8]:
            print(f"   {r.actual:>7,}  {(r.sub_item or '')[:26]:<28} → {when:%Y-%m-%d}")
        if len(plan) > 8:
            print(f"   …（其餘 {len(plan) - 8} 列）")
        if dupes:
            print(f"\n⚠ 同一組（收款人/金額/摘要）在 Sheet 裡有多列，跳過：{len(dupes)} 組")
            for k, n in dupes[:5]:
                print(f"   {k} ×{n}")
        if misses:
            print(f"\n⚠ DB 有、Sheet 對不上的：{len(misses)} 列（可能是系統裡新建的）")
            for r in misses[:5]:
                print(f"   {r.actual:,}  {(r.sub_item or '')[:30]}")

        if not args.apply:
            print("\n[DRY-RUN] 沒有寫入。確認後加 --apply。")
            await engine.dispose()
            return 0

        for r, when in plan:
            r.expense_date = when
        await session.commit()
        print(f"\n[OK] 已補上 {len(plan)} 列的日期。")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
