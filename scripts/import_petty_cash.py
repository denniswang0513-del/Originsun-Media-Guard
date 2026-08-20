# -*- coding: utf-8 -*-
"""零用金 Google Sheet → CRM 支出單據 + 請款批次（docs/PETTY_CASH_PLAN.md §5）。

    # 只出對帳報告，不寫任何東西（預設）
    .venv/Scripts/python.exe scripts/import_petty_cash.py --csv petty.csv
    # 對生產庫（預設 dev 庫）
    .venv/Scripts/python.exe scripts/import_petty_cash.py --csv petty.csv --prod
    # 真的寫入
    .venv/Scripts/python.exe scripts/import_petty_cash.py --csv petty.csv --prod --apply

🔴 預設 dry-run。`--apply` 才寫，而且**只有在對帳全綠時**才允許寫
（每人合計 vs 表頭「應請款」對不上就 abort）—— 匯入財務資料沒有「差不多」。

CSV 來源：Sheet「零用金帳戶」分頁 → 檔案／下載／CSV，或
`curl -sL "https://docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=<gid>"`。
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

from sqlalchemy import select, update as sa_update  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from db.models import (CrmProject, CrmProjectExpense, CrmReimbursement,  # noqa: E402
                       CrmStaff)

# 明細表的欄位位置（Sheet 版面固定；欄位名那列本身也在資料區裡，靠 header 偵測跳過）
# deposit/kind 未用，僅記 Sheet 版面（實務只填「請款」欄，見 §0 實測）
COL = {"date": 0, "spend": 1, "claim": 2, "deposit": 3, "summary": 4,
       "note": 5, "kind": 6, "item": 7, "payee": 8, "project": 12}


# 正本搬到 scripts/_common.py（原本三支匯入腳本各有一份一字不差的複本）。
# 這裡保留 module 層級的名字 —— backfill_petty_dates 與 cleanup_placeholder_expenses
# 是用 importlib 依**檔案路徑**載入本模組再取 `.resolve_db_url`，拿掉會直接壞掉。
from scripts._common import resolve_db_url  # noqa: E402,F401


HEADER_MARK = "日期"

# 附註欄是自由文字：可能是發票號碼、可能是「收據/無發票」、也可能是整段商品說明
# （實測有「《MUSTA》4K高畫質 HDMI線 2.0版…」這種 50 字的）。所以**只認長得像
# 統一發票號碼的**（兩碼英文 + 8 碼數字，中間可有連字號），其餘一律當附註留在 notes。
# 反過來寫白名單（列舉「不是發票」的字）會被沒列到的自由文字撐爆 varchar(32)。
INVOICE_RE = re.compile(r"^[A-Za-z]{0,3}-?\d{6,10}$")


def money(s: str) -> int:
    """' NT$ 1,970 ' → 1970；' NT$ (647)' → -647；'-' / '' → 0。"""
    s = (s or "").replace("NT$", "").replace(",", "").replace("$", "").strip()
    if not s or s == "-":
        return 0
    neg = s.startswith("(") and s.endswith(")")
    if neg:
        s = s[1:-1]
    try:
        return -int(float(s)) if neg else int(float(s))
    except ValueError:
        return 0


def parse_person(cell: str) -> tuple[str, str]:
    """'蘇家弘_E123871879' → ('蘇家弘', 'E123871879')。沒底線就整串當姓名。"""
    cell = (cell or "").strip()
    name, _, pid = cell.partition("_")
    return name.strip(), pid.strip()


def parse_date(s: str):
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime((s or "").strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def read_sheet(path: str) -> tuple[list[dict], list[dict]]:
    """回 (帳戶總覽, 明細列)。兩段以明細表頭（A 欄 == '日期'）為界。"""
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh))

    split = next((i for i, r in enumerate(rows)
                  if r and r[0].strip() == HEADER_MARK), None)
    if split is None:
        raise SystemExit("[ERROR] 找不到明細表頭（A 欄 '日期'）—— 版面變了？")

    accounts = []
    for r in rows[:split]:
        if not r or not r[0].strip() or r[0].strip() == "零用金帳戶":
            continue
        name, pid = parse_person(r[0])
        if not pid:                     # 表頭裝飾列
            continue
        accounts.append({
            "name": name, "pid": pid,
            "opening": money(r[2] if len(r) > 2 else ""),
            "closing": money(r[4] if len(r) > 4 else ""),
            "claim": money(r[5] if len(r) > 5 else ""),
            "status": (r[7] if len(r) > 7 else "").strip(),
        })

    details = []
    last_date = None            # 空白日期沿用上一列 —— **只用於排序**，寫入仍是 NULL
    for n, r in enumerate(rows[split + 1:], start=split + 2):
        if len(r) <= COL["payee"]:
            continue
        amount = money(r[COL["claim"]]) or money(r[COL["spend"]])
        payee_cell = r[COL["payee"]].strip()
        if not amount and not payee_cell:
            continue
        name, pid = parse_person(payee_cell)
        note = r[COL["note"]].strip()
        d_date = parse_date(r[COL["date"]])
        if d_date:
            last_date = d_date
        details.append({
            "row": n,
            "date": d_date,
            "_order": d_date or last_date or datetime.min.replace(tzinfo=timezone.utc),
            "amount": amount,
            "summary": r[COL["summary"]].strip(),
            "note": note,
            "item": r[COL["item"]].strip(),
            "payee_name": name, "payee_pid": pid, "payee_raw": payee_cell,
            "project_label": (r[COL["project"]].strip()
                              if len(r) > COL["project"] else ""),
            "invoice_no": note if INVOICE_RE.match(note) else "",
        })
    return accounts, details


def norm(s: str) -> str:
    """專案名比對用：去空白與全形空白，其餘保留（不做模糊，寧可不綁）。"""
    return "".join((s or "").split()).replace("　", "")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--prod", action="store_true", help="打生產庫 mediaguard（預設 dev）")
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設只出報告）")
    args = ap.parse_args()

    accounts, details = read_sheet(args.csv)
    print(f"讀入：帳戶 {len(accounts)} 個、明細 {len(details)} 列、"
          f"合計 NT${sum(d['amount'] for d in details):,}\n")

    url = resolve_db_url(args.prod)
    print(f"DB: {url.split('@')[-1]}   模式: {'寫入' if args.apply else 'DRY-RUN'}\n")

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        # 只取需要的欄位，不 hydrate 整個 ORM 物件 —— dry-run 要能在
        # startup migration 還沒跑過的庫上執行（否則新欄位不存在就整支炸掉）。
        staff_rows = (await session.execute(
            select(CrmStaff.id, CrmStaff.name, CrmStaff.id_number))).all()
        by_pid = {r.id_number.strip(): r for r in staff_rows
                  if (r.id_number or "").strip()}
        by_name = defaultdict(list)
        for r in staff_rows:
            by_name[(r.name or "").strip()].append(r)
        proj_rows = (await session.execute(
            select(CrmProject.id, CrmProject.name))).all()
        by_proj = defaultdict(list)
        for p in proj_rows:
            by_proj[norm(p.name)].append(p)

        # ── 逐列解析 ──────────────────────────────────────────────
        per_person: dict[str, list] = defaultdict(list)
        unmatched_staff, unmatched_proj, no_date = set(), defaultdict(int), 0
        for d in details:
            s = by_pid.get(d["payee_pid"])
            if s is None:
                cands = by_name.get(d["payee_name"], [])
                s = cands[0] if len(cands) == 1 else None
            if s is None:
                unmatched_staff.add(d["payee_raw"])
            d["staff"] = s
            label = d["project_label"]
            if label:
                cands = by_proj.get(norm(label), [])
                d["project"] = cands[0] if len(cands) == 1 else None
                if d["project"] is None:
                    unmatched_proj[label] += 1
            else:
                d["project"] = None
            if d["date"] is None:
                no_date += 1
            per_person[d["payee_raw"]].append(d)

        # ── 切開「歷史已結清」與「本期未請款」────────────────────────
        # 明細沒有逐列的結清狀態，表頭的「應請款」也**只涵蓋未結清**的部分
        # （實測：蔡念栩 明細合計 5,040、表頭 862）。可推的只有一件事 ——
        # 未結清的是**最後那幾列**。所以從尾端往前累加，看能不能剛好湊出
        # 「應請款」：湊得到 → 那就是本期；湊不到 → 不猜，整批當歷史並標記。
        print("── 切帳：歷史已結清 vs 本期未請款 " + "─" * 26)
        sheet_claim = {f"{a['name']}_{a['pid']}": a for a in accounts}
        unresolved = []
        for raw, rows_ in sorted(per_person.items(),
                                 key=lambda kv: -sum(d["amount"] for d in kv[1])):
            rows_.sort(key=lambda d: (d["_order"], d["row"]))
            total = sum(d["amount"] for d in rows_)
            acct = sheet_claim.get(raw)
            bound = "✓" if rows_[0]["staff"] else "✗未對到人員"
            target = acct["claim"] if acct and acct["status"] == "待請款" else 0

            open_rows: list = []
            if target:
                acc = 0
                for d in reversed(rows_):
                    acc += d["amount"]
                    open_rows.append(d)
                    if acc == target:
                        break
                else:
                    open_rows = []      # 尾端湊不出來 → 不猜
            for d in rows_:
                d["open"] = d in open_rows

            if target and not open_rows:
                unresolved.append((raw, target, total))
                tail = f"✗ 應請款 {target:,} 湊不出來 → 全部當歷史、另行處理"
            elif open_rows:
                tail = (f"本期 {len(open_rows)} 列 = {target:,} ✓ ／ "
                        f"歷史 {len(rows_) - len(open_rows)} 列")
            elif acct is None:
                tail = "不在帳戶總覽 → 全歷史"
            else:
                tail = f"{acct['status']} → 全歷史"
            print(f"  {raw:24s} 合計{total:>9,}  {len(rows_):>3}列  {bound}  {tail}")

        print("\n── 需要人工處理 " + "─" * 44)
        print(f"  未對到人員：{len(unmatched_staff)} 個 → {sorted(unmatched_staff)}")
        print(f"  未對到專案標籤：{len(unmatched_proj)} 種 / "
              f"{sum(unmatched_proj.values())} 列")
        for label, n in sorted(unmatched_proj.items(), key=lambda kv: -kv[1])[:10]:
            print(f"     {n:>3} 列  {label}")
        print(f"  沒有日期：{no_date} 列（匯入後標記待補，不猜）")
        matched_proj = sum(1 for d in details if d["project"])
        print(f"  已對到專案：{matched_proj} 列 / 有標籤 "
              f"{sum(1 for d in details if d['project_label'])} 列")

        if unresolved:
            print("\n[!] 以下帳戶的「應請款」無法從明細尾端還原 —— 匯入後**要手動補一筆調整**：")
            for raw, target, total in unresolved:
                print(f"   {raw}: 表頭應請款 {target:,}（明細合計 {total:,}）"
                      f"{' ← 負數＝公司多付，應向本人收回' if target < 0 else ''}")

        if not args.apply:
            print("\n[DRY-RUN] 沒有寫入任何資料。確認以上報告後加 --apply。")
            await engine.dispose()
            return 0

        # ── 寫入：每人最多兩批（歷史封存 + 本期未請款）──────────────
        now = datetime.now(timezone.utc)
        created_claims = created_rows = 0
        for raw, rows_ in per_person.items():
            acct = sheet_claim.get(raw)
            name, _pid = parse_person(raw)
            staff = rows_[0]["staff"]
            for is_open in (False, True):
                bucket = [d for d in rows_ if bool(d["open"]) is is_open]
                if not bucket:
                    continue
                dates = [d["date"] for d in bucket if d["date"]]
                claim = CrmReimbursement(
                    id=uuid.uuid4().hex[:16],
                    staff_id=staff.id if staff else None,
                    staff_name=name or raw,
                    period_start=min(dates) if dates else None,
                    period_end=max(dates) if dates else None,
                    # 備用金只掛在「本期」那批；歷史批的期初/期末沒有意義
                    opening_float=(acct["opening"] if acct and is_open else 0),
                    closing_float=(acct["closing"] if acct and is_open else 0),
                    total_claim=sum(d["amount"] for d in bucket),
                    status="待審" if is_open else "已付款",
                    submitted_at=now,
                    paid_at=None if is_open else now,
                    notes=("Google Sheet 零用金帳戶匯入（2026-08-17）"
                           + ("；本期未請款" if is_open
                              else "；歷史已結清，封存不再付款")),
                )
                session.add(claim)
                created_claims += 1
                for d in bucket:
                    # 摘要欄 sub_item 是 VARCHAR(128)，實測有一列 134 字。
                    # 截斷但**不丟**：完整原文接到 notes 後面，帳面上查得回來。
                    summary, note = d["summary"], d["note"]
                    if len(summary) > 128:
                        note = (note + " / " if note else "") + summary
                        summary = summary[:127] + "…"
                    session.add(CrmProjectExpense(
                        id=uuid.uuid4().hex[:16],
                        project_id=d["project"].id if d["project"] else None,
                        cost_group_id=None,
                        category="其他",
                        estimated=0, actual=d["amount"],
                        # 摘要（「兩廳院拍攝午餐」）是這批資料裡最可讀的一欄，
                        # 存進 sub_item —— 頁面上的「摘要」讀的就是它
                        sub_item=summary or None,
                        # 🔴 只存姓名，不存 Sheet 上的「姓名_身分證」整串 ——
                        # 身分證只能活在 crm_staff（PETTY_CASH_PLAN §4）。這欄會
                        # 出現在專案支出分頁的「誰墊的」，等於印在畫面上。
                        payee=None if d["staff"] else (d["payee_name"] or d["payee_raw"]),
                        notes=note or None,
                        created_at=now,
                        expense_date=d["date"],
                        staff_id=d["staff"].id if d["staff"] else None,
                        item=d["item"] or "其他",
                        invoice_no=d["invoice_no"] or None,
                        has_invoice=1 if d["invoice_no"] else 0,
                        claim_id=claim.id,
                        status="待審" if is_open else "已付款",
                        # 沒對到專案就留原始標籤文字，等頁面上一次綁 18 個標籤
                        project_label=(d["project_label"]
                                       if d["project_label"] and not d["project"]
                                       else None),
                    ))
                    created_rows += 1

        # 備用金：表頭有期初金額的人回填 crm_staff.petty_float
        # （用 UPDATE 而非 ORM 屬性賦值 —— 上面只 select 了三個欄位，沒有實體物件）
        for a in accounts:
            s = by_pid.get(a["pid"])
            if s is not None and a["opening"]:
                await session.execute(
                    sa_update(CrmStaff).where(CrmStaff.id == s.id)
                    .values(petty_float=a["opening"]))

        await session.commit()
        print(f"\n[OK] 寫入 {created_claims} 張請款批次、{created_rows} 列支出單據。")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
