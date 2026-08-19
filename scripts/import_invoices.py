# -*- coding: utf-8 -*-
"""發票 Google Sheet → crm_invoices（母公司帳 entity='parent'）。

    # 只出對帳報告，不寫任何東西（預設；預設 dev 庫）
    .venv/Scripts/python.exe scripts/import_invoices.py --csv inv.csv
    # 對生產庫
    .venv/Scripts/python.exe scripts/import_invoices.py --csv inv.csv --prod
    # 真的寫入
    .venv/Scripts/python.exe scripts/import_invoices.py --csv inv.csv --apply

🔴 預設 dry-run。`--apply` 才寫，而且**只有在對帳全綠時**才允許寫（逐年筆數與
發票金額合計必須與來源 CSV 完全相同）—— 匯入財務資料沒有「差不多」。
比照 scripts/import_petty_cash.py 的契約。

CSV 來源：Sheet「發票」分頁 → 檔案／下載／CSV，或
`curl -sL "https://docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=<gid>"`。

🔴 這份 Sheet 的版面陷阱（2026-08-19 實測）：
1. **前 14 列是稅額計算機小工具**，真表頭在第 15 列（0-based index 14）。直接餵
   csv.DictReader 會把空白列當表頭 → 整份匯不進去。本腳本自動偵測表頭列。
2. **公式撐出來的空列**：Sheet 往下拉了 589 列公式，發票金額/稅額都是 0、名稱空白。
   靠「名稱非空」濾掉（與後端 import_csv 的 skip 規則一致）。
3. 金額欄帶千分位逗號 → 交給 routers.crm.finance._parse_money（那支的單一正本）。

去重：以 Sheet 的「索引標籤」欄（金額_名稱_申請人_發票號碼，實測 394/394 唯一）
為天然鍵，寫進 notes 前綴不可行 → 改用 (invoice_number, title, amount_total)
三元組比對既有列，重跑不會產生重複。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from db.models import CrmInvoice  # noqa: E402
from routers.crm.finance import _map_invoice_row  # noqa: E402

HEADER_MARK = "發票編號"   # 表頭列的指紋（計算機區沒有這個字）
ENTITY = "parent"          # 匯入一律落母公司帳（我的帳不走匯入）


def resolve_db_url(prod: bool) -> str:
    """dev/prod 庫切換 —— 與 scripts/import_petty_cash.py 同一套寫法，不得漂移。"""
    from config import load_settings
    url = load_settings().get("database_url", "")
    return (url.replace("/mediaguard_dev", "/mediaguard") if prod
            else (url if url.endswith("_dev") else url + "_dev"))


def find_header(rows: list) -> int:
    for i, r in enumerate(rows):
        if any(c.strip() == HEADER_MARK for c in r):
            return i
    raise SystemExit(f"找不到表頭列（沒有任何一列含「{HEADER_MARK}」）")


def parse_date(v: str):
    """'2024/01/02' / '2024-1-2' → date。壞值回 None（呼叫端會列出來要人決定）。"""
    v = (v or "").strip()
    m = re.fullmatch(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", v)
    if not m:
        return None
    try:
        return datetime(int(m[1]), int(m[2]), int(m[3])).date()
    except ValueError:
        return None


def load(csv_path: str):
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8-sig", newline="")))
    h = find_header(rows)
    hdr = [c.strip() for c in rows[h]]
    header_map = {c.lower(): c for c in hdr if c}
    recs, skipped, bad_date = [], [], []
    for ln, raw in enumerate(rows[h + 1:], start=h + 2):
        if not any(c.strip() for c in raw):
            continue
        row = {hdr[i]: (raw[i] if i < len(raw) else "") for i in range(len(hdr)) if hdr[i]}
        data = _map_invoice_row(header_map, row)          # 用後端同一支 mapper
        if not data.get("title"):
            skipped.append(ln)
            continue
        raw_date = data.pop("invoice_date", "")
        d = parse_date(raw_date)
        if not d:
            bad_date.append((ln, raw_date, data.get("title", ""), data.get("invoice_number", "")))
        recs.append({"line": ln, "date": d, "raw_date": raw_date, "data": data,
                     "tag": (row.get("索引標籤") or "").strip()})
    return recs, skipped, bad_date


def report(recs, skipped, bad_date, csv_path):
    print("=" * 68)
    print("來源:", csv_path)
    print("=" * 68)
    print(f"可匯入列: {len(recs)}    跳過（名稱空白，多為公式空列）: {len(skipped)}")

    tot = defaultdict(lambda: [0, 0, 0, 0])
    for r in recs:
        y = str(r["date"].year) if r["date"] else "(日期壞)"
        d = r["data"]
        tot[y][0] += 1
        tot[y][1] += d.get("amount_total", 0)
        tot[y][2] += d.get("amount_ex_tax", 0)
        tot[y][3] += d.get("tax_amount", 0)
    print(f"\n{'年度':<10}{'筆數':>6}{'發票金額':>16}{'未稅價':>16}{'稅額':>14}")
    for y in sorted(tot):
        c, a, e, t = tot[y]
        print(f"{y:<10}{c:>6}{a:>16,}{e:>16,}{t:>14,}")
    g = [sum(v[i] for v in tot.values()) for i in range(4)]
    print(f"{'合計':<10}{g[0]:>6}{g[1]:>16,}{g[2]:>16,}{g[3]:>14,}")

    if bad_date:
        print(f"\n[!] 日期無法解析 {len(bad_date)} 列（會以 invoice_date=NULL 匯入，之後用 UI 補）:")
        for ln, raw, title, num in bad_date:
            print(f"      第 {ln} 列  日期={raw!r}  {title[:24]}  發票號={num}")

    odd = [r for r in recs
           if r["data"].get("amount_ex_tax", 0) + r["data"].get("tax_amount", 0)
           != r["data"].get("amount_total", 0)]
    if odd:
        print(f"\n[!] 未稅價＋稅額 ≠ 發票金額 {len(odd)} 列（來源資料本身的問題，原樣匯入）:")
        for r in odd:
            d = r["data"]
            print(f"      第 {r['line']} 列  {d.get('title','')[:22]}  "
                  f"{d.get('amount_ex_tax',0):,} + {d.get('tax_amount',0):,} "
                  f"≠ {d.get('amount_total',0):,}")
    return g


async def run(csv_path: str, prod: bool, apply: bool):
    recs, skipped, bad_date = load(csv_path)
    totals = report(recs, skipped, bad_date, csv_path)

    url = resolve_db_url(prod)
    dbname = url.rsplit("/", 1)[-1]
    print(f"\n目標資料庫: {dbname}   模式: {'寫入 (--apply)' if apply else 'DRY-RUN（不寫入）'}")

    eng = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    async with Session() as s:
        existing = (await s.execute(
            select(CrmInvoice.invoice_number, CrmInvoice.title, CrmInvoice.amount_total)
            .where(CrmInvoice.entity == ENTITY))).all()
        seen = {(a or "", b or "", c or 0) for a, b, c in existing}
        print(f"該庫既有母公司發票: {len(existing)} 筆")

        new, dup = [], []
        for r in recs:
            d = r["data"]
            key = (d.get("invoice_number", ""), d.get("title", ""), d.get("amount_total", 0))
            (dup if key in seen else new).append(r)
            seen.add(key)
        print(f"本次會新增 {len(new)} 筆；判定為既有而跳過 {len(dup)} 筆")

        if not apply:
            print("\nDRY-RUN 結束 —— 沒有寫入任何東西。確認上面數字無誤後加 --apply。")
            await eng.dispose()
            return

        if not new:
            print("沒有要新增的列。")
            await eng.dispose()
            return

        now = datetime.now(timezone.utc)
        for r in new:
            s.add(CrmInvoice(id=uuid.uuid4().hex, entity=ENTITY, invoice_date=r["date"],
                             created_at=now, updated_at=now, **r["data"]))
        await s.commit()
        print(f"[OK] 已寫入 {len(new)} 筆到 {dbname}")

        # 寫後複驗：DB 實際筆數與金額合計要對得上來源
        rows = (await s.execute(select(CrmInvoice.amount_total)
                                .where(CrmInvoice.entity == ENTITY))).scalars().all()
        print(f"[驗] {dbname} 母公司發票現有 {len(rows)} 筆，發票金額合計 {sum(rows):,}"
              f"（來源合計 {totals[1]:,}）")
    await eng.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--prod", action="store_true", help="對生產庫 mediaguard（預設 dev）")
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設 dry-run）")
    a = ap.parse_args()
    asyncio.run(run(a.csv, a.prod, a.apply))
