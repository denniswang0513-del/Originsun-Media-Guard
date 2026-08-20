# -*- coding: utf-8 -*-
"""請款單歷史匯入 — Google Sheet（gid=2108959549）→ crm_payment_requests。

用法（🔴 預設 dry-run，契約比照 scripts/import_cashbook.py）：
    python scripts/import_payment_requests.py --csv <path>            # dev 試算
    python scripts/import_payment_requests.py --csv <path> --apply    # dev 寫入
    python scripts/import_payment_requests.py --csv <path> --prod --apply

Sheet 結構（2026-08-20 實測）：表頭在「日期」那一列（上方是零用金餘額摘要與
「代開發票請款專案」待請款區，匯入時跳過）。806 筆、合計 22,719,333、
805 已付款＋1 應付款（唯一沒有付款日的那筆，自洽）。

欄位對映的兩個不直覺處：
- Sheet 的「類別」恆為「請款」（資料來源標記），真正的類別在「項目」欄 ——
  跟收支明細那次同一個坑（🔴 項目→category 不是 item）。
- 「收款人」是「姓名_身分證」黏在一起（774 筆標準格式、19 筆小寫/長度異常、
  13 筆純姓名），以**第一個** `_` 拆成 payee_name + payee_id。

代開發票列（178 筆）：請款金額＝發票金額 × 92%（owner 2026-08-20 說明的
「代開應匯」規則；105 筆主流，55 筆早期是 100% 全額，另有分期只匯部分的）。
發票庫的 commission 欄存的就是 92% 金額 —— 這裡只負責把號碼與金額搬進請款單，
「已收款自動進待請款區」是匯入後另做的功能。

🔴 Sheet 內有 8 組完全重複的列（同日同額同摘要同收款人）—— 可能是分期補繳也
可能是重複輸入。照多重集全部匯入（去重鍵一筆抵一筆），報告裡列出來給 owner 核對。
"""
import argparse
import asyncio
import csv
import io
import os
import re
import sys
import uuid
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from scripts._common import find_header, parse_date, resolve_db_url  # noqa: E402

HEADER_MARK = "日期"
ENTITY = "parent"

# 收款人「姓名_身分證」：只認第一個底線（附註型底線不常見但防著）
_PAYEE = re.compile(r"^([^_]+)_(.+)$")


def parse_amt(v: str):
    """'NT$ 1,842' → 1842；'NT$ (647)' → -647；'-'/空 → None。"""
    v = (v or "").replace("NT$", "").replace(",", "").replace("，", "").strip()
    if v in ("", "-", "—"):
        return None
    neg = v.startswith("(") and v.endswith(")")
    try:
        x = int(round(float(v.strip("()"))))
    except ValueError:
        return None
    return -x if neg else x


def load(csv_path: str):
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8-sig", newline="")))
    h = find_header(rows, HEADER_MARK)
    hdr = [c.strip() for c in rows[h]]

    def cell(r, name):
        i = hdr.index(name)
        return (r[i] if i < len(r) else "").strip()

    recs, bad, sheet_dups = [], [], []
    seen = Counter()
    for ln, raw in enumerate(rows[h + 1:], start=h + 2):
        date_s = cell(raw, "日期")
        if not date_s:
            continue                      # 表頭上方摘要區與尾端空白列
        d = parse_date(date_s)
        amount = parse_amt(cell(raw, "請款"))
        if d is None or amount is None:
            bad.append((ln, date_s, cell(raw, "請款"), cell(raw, "摘要")[:20]))
            continue

        payee_raw = cell(raw, "收款人")
        m = _PAYEE.match(payee_raw)
        payee_name, payee_id = (m.group(1), m.group(2)) if m else (payee_raw, "")

        # 代開發票欄：'52500_愛力根回顧影片_王士源_XN35847353' → 第一段是發票金額
        kai = cell(raw, "代開發票")
        inv_no = cell(raw, "發票號碼")
        inv_amt = None
        if kai:
            head = kai.split("_", 1)[0]
            if head.isdigit():
                inv_amt = int(head)

        status = cell(raw, "付款狀態") or "已付款"
        pay_d = parse_date(cell(raw, "付款日")) if cell(raw, "付款日") else None
        rec = {
            "request_date": d,
            "amount": amount,
            "summary": cell(raw, "摘要") or "（無摘要）",
            "notes": cell(raw, "附註") or None,
            # 🔴 Sheet 的「類別」恆為「請款」；真類別在「項目」
            "category": cell(raw, "項目") or None,
            "payee_name": payee_name or None,
            "payee_id": payee_id or None,
            "payee_type": cell(raw, "狀態") or None,   # 內部人員/勞報/現金/發票核銷
            "needs_invoice": 1 if (inv_no or kai) else 0,
            "invoice_number": inv_no or None,
            "invoice_amount": inv_amt,
            "project_label": cell(raw, "專案標籤") or None,
            "payment_date": pay_d,
            "payment_status": status,
            # 應付款要有 planned_month 才會出現在應付帳款的月份分組裡
            "planned_month": (str(d)[:7] if status == "應付款" else None),
        }
        key = (str(d)[:10], amount, rec["summary"], payee_name)
        seen[key] += 1
        if seen[key] == 2:
            sheet_dups.append(key)
        recs.append((ln, rec))
    return recs, bad, sheet_dups


async def run(csv_path: str, prod: bool, apply: bool):
    url = resolve_db_url(prod)
    dbname = url.rsplit("/", 1)[-1]
    recs, bad, sheet_dups = load(csv_path)

    eng = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    from db.models import CrmInvoice, CrmPaymentRequest, CrmProject
    from routers.crm._shared import _locked_month_set
    async with Session() as s:
        # F1 月結守衛：請款單的權責認列月 = request_date，落鎖定月整批不做
        # （鎖定月集合走 _shared 的正本 —— 自己 select 會漏掉「已重開」的判斷）
        locked = await _locked_month_set(s, entity=ENTITY)
        hit = sorted({str(r["request_date"])[:7] for _l, r in recs} & locked)
        if hit:
            print(f"[ABORT] 有資料落在已鎖帳月份 {hit} —— 先解鎖或排除那些列再來。")
            await eng.dispose()
            return

        # 專案標籤 → project_id（名稱完全一致才連，不猜）
        projects = {p.name: p.id for p in
                    (await s.execute(select(CrmProject))).scalars().all()}
        proj_hit = proj_miss = 0
        miss_labels = Counter()
        for _ln, r in recs:
            if r["project_label"]:
                pid = projects.get(r["project_label"])
                if pid:
                    r["project_id"] = pid
                    proj_hit += 1
                else:
                    proj_miss += 1
                    miss_labels[r["project_label"]] += 1

        # 發票號碼 → 發票庫存在性（只驗證回報，不擋）
        inv_nos = {(i.invoice_number or "").replace("-", "") for i in
                   (await s.execute(select(CrmInvoice))).scalars().all()}
        inv_have = [r for _l, r in recs if r["invoice_number"]]
        inv_miss = [r["invoice_number"] for r in inv_have
                    if r["invoice_number"].replace("-", "") not in inv_nos]

        # 多重集去重 vs DB（完全相同的兩列是合法資料，set 會誤殺 —— 同 cashbook）
        existing = Counter()
        for row in (await s.execute(
                select(CrmPaymentRequest.request_date, CrmPaymentRequest.amount,
                       CrmPaymentRequest.summary, CrmPaymentRequest.payee_name)
                .where(CrmPaymentRequest.entity == ENTITY))).all():
            existing[(str(row[0])[:10] if row[0] else "", row[1],
                      row[2] or "", row[3] or "")] += 1
        to_add, skipped = [], 0
        for _ln, r in recs:
            key = (str(r["request_date"])[:10], r["amount"],
                   r["summary"], r["payee_name"])
            if existing[key] > 0:
                existing[key] -= 1
                skipped += 1
                continue
            to_add.append(r)

        # ── 報告 ──
        total = sum(r["amount"] for _l, r in recs)
        cats = Counter(r["category"] for _l, r in recs)
        stats = Counter(r["payment_status"] for _l, r in recs)
        print("=" * 72)
        print(f"目標資料庫: {dbname}   模式: "
              f"{'APPLY（會寫入！）' if apply else 'DRY-RUN（不寫入）'}")
        print(f"資料列: {len(recs)}   金額合計: {total:,}   解析失敗: {len(bad)}")
        for ln, ds, am, sm in bad[:10]:
            print(f"    第 {ln} 列  {ds}  {am}  {sm}")
        print("類別分布:", dict(cats.most_common()))
        print("付款狀態:", dict(stats))
        print(f"專案標籤: 對上 {proj_hit}   對不上 {proj_miss} "
              f"{dict(miss_labels.most_common(6)) if miss_labels else ''}")
        print(f"代開發票: 有號碼 {len(inv_have)} 筆   發票庫查無: {inv_miss or '無'}")
        if sheet_dups:
            print(f"[!] Sheet 內完全重複的列 {len(sheet_dups)} 組（照多重集全部匯入，"
                  f"請 owner 核對是分期補繳還是重複輸入）:")
            for k in sheet_dups:
                print(f"    {k[0]}  {k[1]:>8,}  {k[2][:20]}  {k[3]}")
        print(f"該庫既有請款單: {sum(existing.values()) + skipped} 筆")
        print(f"本次會新增 {len(to_add)} 筆；判定為既有而跳過 {skipped} 筆")

        if not apply:
            print("\nDRY-RUN 結束 —— 沒有寫入任何東西。確認上面數字無誤後加 --apply。")
            await eng.dispose()
            return

        from routers.crm._shared import _now
        now = _now()
        for r in to_add:
            s.add(CrmPaymentRequest(id=uuid.uuid4().hex, entity=ENTITY,
                                    created_at=now, updated_at=now, **r))
        await s.commit()
        n = (await s.execute(select(CrmPaymentRequest.id)
             .where(CrmPaymentRequest.entity == ENTITY))).scalars().all()
        print(f"\n✅ 已寫入 {len(to_add)} 筆。該庫現有母公司請款單: {len(n)} 筆")
    await eng.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--prod", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    asyncio.run(run(a.csv, a.prod, a.apply))
