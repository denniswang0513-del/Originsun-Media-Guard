# -*- coding: utf-8 -*-
"""合庫貸款專戶（源日 0070717559515）流水帳 → crm_cash_entries（entity='parent'）。

    .venv/Scripts/python.exe scripts/import_cashbook_coop.py --csv coop.csv          # dry-run
    .venv/Scripts/python.exe scripts/import_cashbook_coop.py --csv coop.csv --apply  # 寫入
    （--prod 對生產庫；預設 dev。契約比照 scripts/import_cashbook.py。）

CSV 來源：Sheet「記帳表_合庫」分頁（gid=279883383）→ export?format=csv。

兩段資料來源（2026-08-19 定案）：
1. **Sheet 93 列**（2024/02/28 ~ 2025/12/21，期初 56,129 → 期末 239,602）。
2. **對帳單補記 30 列**（STATEMENT_ROWS，2025/12/28 ~ 2026/08/10）——Sheet 從
   2025/12/21 之後就沒人記了，這段唯一的紀錄是合庫網銀對帳單（owner 2026-08-19
   提供，查詢區間 2025/08/19~2026/08/19）。對帳單與 Sheet 重疊區間逐筆核對相符，
   期末 155,078 已用「期初 56,129 ＋ 全部流水」驗算吻合。

類別正規化（Sheet 用語 / 對帳單摘要 → 系統 category，треatment 見 finance_category_map）：
    銀行還款・攤還本息 → 貸款繳款（loan：不進損益、現金流走籌資）
    轉帳・跨行轉入     → 轉存（transfer：富邦月轉 35,000 的對側，互抵）
    貸款補貼・中心轉存 → 貸款補貼（direct_income：文創補貼息，2026-08-19 新增對映）
    銀行利息・利息     → 銀行利息（direct_income：存款利息）
    無摺轉支           → 其他（條件變更費 6,600，一次性手續費）

去重：多重集比對（同 import_cashbook.py），existing 只數**掛在合庫帳戶**的列。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# stdout 的 UTF-8 wrap 交給下面 import 的 scripts.import_cashbook 做 —— 這裡再包一層
# 的話，被取代的舊 wrapper 一進 GC 就把共用的底層 buffer 關掉（print 直接炸）。

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from db.models import CrmCashEntry  # noqa: E402
from routers.crm.finance import _parse_money  # noqa: E402
from scripts.import_cashbook import (  # noqa: E402
    parse_date, resolve_bank_account, resolve_db_url)

ENTITY = "parent"
OPENING = 56_129          # Sheet 期初（2024-02）
FINAL_BALANCE = 155_078   # 對帳單 2026-08-10 期末 —— 匯入前的硬驗算

CATEGORY_MAP = {
    "銀行還款": "貸款繳款", "攤還本息": "貸款繳款",
    "轉帳": "轉存", "跨行轉入": "轉存",
    "貸款補貼": "貸款補貼", "中心轉存": "貸款補貼",
    "銀行利息": "銀行利息", "利息": "銀行利息",
    "無摺轉支": "其他",
}

# 對帳單補記（合庫網銀 2025/08/19~2026/08/19 明細，Sheet 未涵蓋的 2025/12/28 起）。
# (日期, 支出, 存入, 摘要, 附註)
STATEMENT_ROWS = [
    ("2025/12/28", 0, 35000, "跨行轉入", "0082120000062728"),
    ("2025/12/31", 0, 2607, "中心轉存", "文創補貼息"),
    ("2026/01/07", 6600, 0, "無摺轉支", "條件變更費查詢費"),
    ("2026/01/08", 4470, 0, "攤還本息", "01-08 315614"),
    ("2026/01/08", 25290, 0, "攤還本息", "01-08 315611"),
    ("2026/01/28", 0, 35000, "跨行轉入", "0082120000062728"),
    ("2026/01/29", 0, 2555, "中心轉存", "文創補貼息"),
    ("2026/02/09", 4470, 0, "攤還本息", "02-08 315614"),
    ("2026/02/09", 25290, 0, "攤還本息", "02-08 315611"),
    ("2026/02/25", 0, 2503, "中心轉存", "文創補貼息"),
    ("2026/03/09", 4470, 0, "攤還本息", "03-08 315614"),
    ("2026/03/09", 25290, 0, "攤還本息", "03-08 315611"),
    ("2026/03/24", 0, 2450, "中心轉存", "文創補貼息"),
    ("2026/04/08", 4470, 0, "攤還本息", "04-08 315614"),
    ("2026/04/08", 25290, 0, "攤還本息", "04-08 315611"),
    ("2026/04/28", 0, 2399, "中心轉存", "文創補貼息"),
    ("2026/05/08", 4470, 0, "攤還本息", "05-08 315614"),
    ("2026/05/08", 25290, 0, "攤還本息", "05-08 315611"),
    ("2026/05/22", 0, 2346, "中心轉存", "文創補貼息"),
    ("2026/06/08", 4470, 0, "攤還本息", "06-08 315614"),
    ("2026/06/08", 25290, 0, "攤還本息", "06-08 315611"),
    ("2026/06/21", 0, 761, "利息", "存款利息"),
    ("2026/06/24", 0, 2294, "中心轉存", "文創補貼息"),
    ("2026/07/08", 4470, 0, "攤還本息", "07-08 315614"),
    ("2026/07/08", 25290, 0, "攤還本息", "07-08 315611"),
    ("2026/07/10", 0, 35000, "跨行轉入", "0082120000062728"),
    ("2026/07/23", 0, 2241, "中心轉存", "文創補貼息"),
    ("2026/08/10", 4470, 0, "攤還本息", "08-08 315614"),
    ("2026/08/10", 25290, 0, "攤還本息", "08-08 315611"),
    ("2026/08/10", 0, 35000, "跨行轉入", "0082120000062728"),
]


def norm_category(raw: str) -> str:
    c = CATEGORY_MAP.get((raw or "").strip())
    if not c:
        raise SystemExit(f"[ABORT] 未知項目/摘要「{raw}」—— 先補 CATEGORY_MAP 再跑。")
    return c


def load(csv_path: str):
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8-sig", newline="")))
    h = next(i for i, r in enumerate(rows) if r and r[0].strip() == "日期")
    hdr = [c.strip() for c in rows[h]]

    def col(r, name):
        i = hdr.index(name)
        return (r[i] if i < len(r) else "").strip()

    recs = []
    for ln, raw in enumerate(rows[h + 1:], start=h + 2):
        if not any(c.strip() for c in raw):
            continue
        d = parse_date(col(raw, "日期"))
        if not d:
            raise SystemExit(f"[ABORT] 第 {ln} 列日期壞（{col(raw, '日期')!r}）")
        expense = _parse_money(col(raw, "支出"))
        deposit = _parse_money(col(raw, "存入"))
        note_parts = [p for p in (col(raw, "附註"), col(raw, "資料來源")) if p]
        recs.append(dict(
            entry_date=d, expense=expense or None, deposit=deposit or None,
            summary=(col(raw, "摘要") or col(raw, "項目") or "（無摘要）")[:255],
            note="\n".join(note_parts) or None,
            category=norm_category(col(raw, "項目")),
        ))
    n_sheet = len(recs)

    for day, expense, deposit, summary, note in STATEMENT_ROWS:
        recs.append(dict(
            entry_date=parse_date(day), expense=expense or None,
            deposit=deposit or None, summary=summary,
            note=f"{note}\n對帳單補記（Sheet 2025/12/21 後未記帳）",
            category=norm_category(summary),
        ))
    return recs, n_sheet


def key(d: dict):
    return (d["entry_date"].strftime("%Y-%m-%d"), d.get("expense") or 0,
            d.get("deposit") or 0, d.get("summary") or "")


async def run(csv_path: str, prod: bool, apply: bool, bank_account: str):
    recs, n_sheet = load(csv_path)
    te = sum(r["expense"] or 0 for r in recs)
    td = sum(r["deposit"] or 0 for r in recs)
    print("=" * 68)
    print(f"來源: Sheet {n_sheet} 列 + 對帳單補記 {len(recs) - n_sheet} 列 = {len(recs)} 列")
    print(f"支出合計 {te:,}   存入合計 {td:,}")
    end = OPENING + td - te
    print(f"恆等式: 期初 {OPENING:,} + 存入 − 支出 = {end:,}（對帳單期末 {FINAL_BALANCE:,}）")
    if end != FINAL_BALANCE:
        raise SystemExit("[ABORT] 帳不平 —— 不寫入。")
    per_cat = Counter(r["category"] for r in recs)
    print(f"類別: {dict(per_cat)}")

    url = resolve_db_url(prod)
    dbname = url.rsplit("/", 1)[-1]
    print(f"\n目標資料庫: {dbname}   模式: {'寫入 (--apply)' if apply else 'DRY-RUN（不寫入）'}")
    eng = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    async with Session() as s:
        months = sorted({r["entry_date"].strftime("%Y-%m") for r in recs})
        locked = (await s.execute(text(
            "SELECT month FROM finance_month_close "
            "WHERE entity = :e AND reopened_at IS NULL AND month = ANY(:m)"
        ), {"e": ENTITY, "m": months})).scalars().all()
        if locked:
            raise SystemExit(f"[ABORT] 已鎖月: {locked}")

        bank_id = await resolve_bank_account(s, bank_account)

        existing = Counter()
        for row in (await s.execute(
                select(CrmCashEntry.entry_date, CrmCashEntry.expense, CrmCashEntry.deposit,
                       CrmCashEntry.summary)
                .where(CrmCashEntry.entity == ENTITY,
                       CrmCashEntry.bank_account_id == bank_id))).all():
            existing[(row[0].strftime("%Y-%m-%d") if row[0] else "",
                      row[1] or 0, row[2] or 0, row[3] or "")] += 1
        new, dup = [], 0
        for r in recs:
            k = key(r)
            if existing[k] > 0:
                existing[k] -= 1
                dup += 1
            else:
                new.append(r)
        print(f"該帳戶既有 {sum(existing.values()) + dup} 筆；本次新增 {len(new)}、跳過 {dup}")

        if not apply:
            print("\nDRY-RUN 結束 —— 沒有寫入任何東西。")
            await eng.dispose()
            return
        now = datetime.now(timezone.utc)
        for r in new:
            s.add(CrmCashEntry(id=uuid.uuid4().hex, entity=ENTITY,
                               bank_account_id=bank_id, has_invoice=0,
                               created_at=now, updated_at=now, **r))
        await s.commit()
        print(f"[OK] 已寫入 {len(new)} 筆到 {dbname}")

        rows = (await s.execute(
            select(CrmCashEntry.expense, CrmCashEntry.deposit)
            .where(CrmCashEntry.entity == ENTITY,
                   CrmCashEntry.bank_account_id == bank_id))).all()
        ve = sum(r[0] or 0 for r in rows)
        vd = sum(r[1] or 0 for r in rows)
        bal = OPENING + vd - ve
        print(f"[驗] 帳戶 {len(rows)} 筆  支出 {ve:,}  存入 {vd:,}  推導餘額 {bal:,}"
              f" → {'全綠' if bal == FINAL_BALANCE else '🔴 不一致！'}")
    await eng.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--prod", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--bank-account", default="合作金庫")
    a = ap.parse_args()
    asyncio.run(run(a.csv, a.prod, a.apply, a.bank_account))
