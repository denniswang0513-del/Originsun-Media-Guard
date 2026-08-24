# -*- coding: utf-8 -*-
"""結案總表的工項拆分與費用欄 → crm_projects.ledger_detail（entity='mine'）。

    .venv/Scripts/python.exe scripts/backfill_my_ledger_detail.py --csv closed.csv [--apply] [--prod]

🔴 預設 dry-run。比對鍵＝**案碼**（匯入時寫進 notes 的 `[私帳匯入] 案碼:XXXX`），
不是專案名 —— 名字有重複（同客戶多集數），案碼唯一。

為什麼要有這支而不是重跑 import_my_projects：那支會刪掉重建，專案 id 一換，
612 筆收支回掛與 34 張請款單的關聯就得全部重來，owner 期間做的任何編輯也沒了。
這支只 UPDATE 一個欄位。

口徑（2026-08-24 對 402 案實測反推，見 CrmProject.ledger_detail 註解）：
  實收 = 營收(含稅) − 委外 − 發票代辦費 − 個人稅款 − 雜支 − 股東往來   （395/402 吻合）
  檢查 = 實收 − Σ工項（應為 0）                                    （397/402 吻合）
不吻合的那幾案是 Sheet 自己帳不平（台新展覽攝影／潮對流／畫我台灣／快樂學游泳／
親子形象片），不是解析錯 —— 系統照實存，讓「檢查」欄把它們顯示出來。
"""
import argparse
import asyncio
import csv
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import resolve_db_url  # noqa: E402

import asyncpg  # noqa: E402

# 工項欄（順序即 UI 顯示順序）。可由 settings my_ledger.income_items 覆寫。
DEPT_COLS = ["前期製作", "動態攝影", "剪輯", "調光", "動態效果",
             "平面攝影", "諮詢", "教學", "錄混音", "其他"]

# Sheet 欄名 → ledger_detail 鍵
COST_COLS = {
    "委外費用": "outsource",
    "稅金": "tax_fee",
    "買發票": "buy_invoice",
    "發票代辦費": "invoice_fee",
    "個人稅款": "personal_tax",
    "雜支": "misc",
    "股東往來": "shareholder",
}


def money(s: str) -> int:
    s = (s or "").replace("NT$", "").replace(",", "").strip()
    s = s.replace("（", "(").replace("）", ")")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    if not s:
        return 0
    try:
        v = float(s)
    except ValueError:
        return 0
    return -round(v) if neg else round(v)


def load_by_code(csv_path: str) -> dict:
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8")))
    hdr = [h.strip() for h in rows[0]]
    ix = {h: i for i, h in enumerate(hdr)}

    def col(r, name):
        i = ix.get(name)
        return (r[i] if i is not None and len(r) > i else "")

    out = {}
    for r in rows[1:]:
        if len(r) < 14 or not r[2].strip() or not r[3].strip():
            continue
        code = (r[-1] or "").strip()
        if not code:
            continue
        detail = {key: money(col(r, sheet)) for sheet, key in COST_COLS.items()}
        detail["split"] = {d: money(col(r, d)) for d in DEPT_COLS
                           if money(col(r, d))}
        out[code] = (detail, money(col(r, "實收")), money(col(r, "檢查")),
                     money(col(r, "營收(含稅)")))
    return out


def computed_net(contract: int, d: dict) -> int:
    return (contract - d["outsource"] - d["invoice_fee"]
            - d["personal_tax"] - d["misc"] - d["shareholder"])


async def run(csv_path: str, apply: bool, prod: bool):
    url = resolve_db_url(prod)
    dbname = url.rsplit("/", 1)[-1]
    dsn = url.replace("postgresql+asyncpg://", "postgresql://")
    by_code = load_by_code(csv_path)
    print(f"目標資料庫: {dbname}   模式: {'寫入 (--apply)' if apply else 'DRY-RUN（不寫入）'}")
    print(f"Sheet 有案碼的列: {len(by_code)}")

    c = await asyncio.wait_for(asyncpg.connect(dsn), 15)
    try:
        rows = await c.fetch(
            "SELECT id, name, notes, contract_amount FROM crm_projects WHERE entity='mine'")
        matched, missing, net_ok, chk_ok = [], [], 0, 0
        for r in rows:
            m = re.search(r"案碼:(\S+)", r["notes"] or "")
            code = m.group(1) if m else ""
            if code not in by_code:
                missing.append(r["name"])
                continue
            detail, sheet_net, sheet_chk, sheet_contract = by_code[code]
            contract = int(r["contract_amount"] or 0)
            net = computed_net(contract, detail)
            if net == sheet_net:
                net_ok += 1
            if (net - sum(detail["split"].values())) == sheet_chk:
                chk_ok += 1
            matched.append((r["id"], detail))
        print(f"對到案碼: {len(matched)}／未對到: {len(missing)} {missing[:3]}")
        print(f"實收算式與 Sheet 一致: {net_ok}/{len(matched)}")
        print(f"檢查欄與 Sheet 一致: {chk_ok}/{len(matched)}")
        n_split = sum(1 for _i, d in matched if d["split"])
        print(f"有工項拆分的案子: {n_split}")
        if not apply:
            print("\nDRY-RUN 結束 —— 沒有寫入任何東西。")
            return
        await c.executemany(
            "UPDATE crm_projects SET ledger_detail = $2::jsonb WHERE id = $1",
            [(pid, json.dumps(d, ensure_ascii=False)) for pid, d in matched])
        n = await c.fetchval(
            "SELECT count(*) FROM crm_projects WHERE entity='mine' AND ledger_detail IS NOT NULL")
        print(f"\n已寫入 {n} 案的 ledger_detail")
        if n != len(matched):
            print("🔴 寫入筆數與比對筆數不符")
            sys.exit(1)
        print("驗證通過 ✓")
    finally:
        await c.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="結案總表 CSV（gid=2098748516）")
    ap.add_argument("--prod", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(a.csv, a.apply, a.prod))
