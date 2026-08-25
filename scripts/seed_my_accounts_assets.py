# -*- coding: utf-8 -*-
"""私帳帳戶與資產補齊（owner 2026-08-26 拍板：0 元帳戶也建、保險/外幣逐列）。

    .venv/Scripts/python.exe scripts/seed_my_accounts_assets.py [--apply] [--prod]

🔴 預設 dry-run。冪等：帳戶/持股都以 (entity='mine', name) 為鍵，存在即跳過
（絕不覆寫 owner 之後在 UI 改過的值）。唯一的既有列變更＝停用「盈透證券（整戶）」
手填彙總（拆成 VWRA 自動報價＋美元活存後，留著會重複計價）。

數字來源：owner 私帳 Sheet 資產清單（2026-08-26 截圖）。
- 新銀行帳戶期初＝Sheet 現值（無歷史流水，之後有動再記）。
- VWRA 掛 yahoo:VWRA.L（LSE 美元計價；quote_fetcher 以 USDTWD=X 換匯）。
- 保險只看得到 南山/安聯 兩列，塊總額 765,935 的殘餘以「保險-其他（待拆列）」
  一列代表 —— 與盈透整戶當年同一招，owner 之後在 UI 細分。
"""
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import connect, db_target, print_dry_run_end, print_target  # noqa: E402

OPENING_DATE = datetime(2026, 8, 26, tzinfo=timezone.utc)   # 期初基準＝Sheet 快照日

# (name, opening_balance)；acct_kind 一律 bank
ACCOUNTS = [
    ("匯豐（個人）", 29_228),
    ("星展（個人）", 35_853),
    ("臺銀（個人）", 1_789),
    ("玉山（個人）", 0),
    ("樂天（個人）", 0),
]

# (broker, symbol, name, shares, currency, quote_symbol, manual_value_twd, note)
HOLDINGS = [
    ("匯豐銀行", "", "複委託", None, "TWD", "", 3_414_328, "[sheet-2026-08-26]"),
    ("匯豐銀行", "", "匯豐-美元活存", 1000.19, "USD", "", 31_882, "[sheet-2026-08-26]"),
    ("保險", "", "南山人壽", None, "TWD", "", 22_172, "[sheet-2026-08-26] 緊急備用"),
    ("保險", "", "安聯人壽", None, "TWD", "", 59_178, "[sheet-2026-08-26] 緊急備用"),
    ("保險", "", "保險-其他（待拆列）", None, "TWD", "", 684_585,
     "[sheet-2026-08-26] 緊急備用-保險塊總額 765,935 減 南山/安聯 的殘餘，請在 UI 拆成逐張保單"),
    ("美國匯豐", "", "美國匯豐-美元活存", 784.15, "USD", "", 24_996, "[sheet-2026-08-26] 緊急備用"),
    ("盈透證券", "VWRA", "Vanguard FTSE All-World", 3230.9473, "USD",
     "yahoo:VWRA.L", None, "[sheet-2026-08-26] 拆自整戶手填"),
    ("盈透證券", "", "盈透-美元活存", 640.49, "USD", "", 20_417, "[sheet-2026-08-26] 拆自整戶手填"),
    # 外幣現金（Sheet「其他-外幣現金」逐幣別；name 帶「現金」避免與活存撞名）
    ("外幣現金", "", "日圓現金", 21_439, "JPY", "", 4_292, "[sheet-2026-08-26]"),
    ("外幣現金", "", "港幣現金", 845.7, "HKD", "", 3_439, "[sheet-2026-08-26]"),
    ("外幣現金", "", "歐元現金", 169.2, "EUR", "", 6_295, "[sheet-2026-08-26]"),
    ("外幣現金", "", "美元現金", 100, "USD", "", 3_188, "[sheet-2026-08-26]"),
    ("外幣現金", "", "人民幣現金", 302.1, "CNY", "", 1_433, "[sheet-2026-08-26]"),
    ("外幣現金", "", "韓元現金", 16_600, "KRW", "", 382, "[sheet-2026-08-26]"),
    ("外幣現金", "", "盧布現金", 179, "RUB", "", 68, "[sheet-2026-08-26]"),
    ("外幣現金", "", "泰銖現金", 41, "THB", "", 40, "[sheet-2026-08-26]"),
    ("外幣現金", "", "澳門幣現金", 5, "MOP", "", 20, "[sheet-2026-08-26]"),
    ("外幣現金", "", "里拉現金", 19.1, "TRY", "", 13, "[sheet-2026-08-26]"),
    ("外幣現金", "", "里爾現金", 100, "KHR", "", 1, "[sheet-2026-08-26]"),
    ("外幣活存", "", "歐元活存（永豐）", 296.06, "EUR", "", 11_015, "[sheet-2026-08-26]"),
]

RETIRE_HOLDING = "盈透證券（整戶）"       # 拆明細後停用（不刪，保歷史）


async def run(apply: bool, prod: bool):
    dbname, dsn = db_target(prod)
    print_target(dbname, apply)
    c = await connect(dsn)
    made_a = kept_a = made_h = kept_h = 0
    try:
        for name, opening in ACCOUNTS:
            exist = await c.fetchval(
                "SELECT id FROM bank_accounts WHERE entity='mine' AND name=$1", name)
            if exist:
                kept_a += 1
                print(f"  帳戶已存在，跳過: {name}")
                continue
            made_a += 1
            print(f"  ＋帳戶 {name}  期初 {opening:,}")
            if apply:
                await c.execute(
                    """INSERT INTO bank_accounts
                           (id, entity, name, acct_kind, opening_balance, opening_date,
                            active, sort_order, note, created_at, updated_at)
                       VALUES ($1,'mine',$2,'bank',$3,$4,true,100,
                               '[sheet-2026-08-26] 期初=Sheet 現值', now(), now())""",
                    uuid.uuid4().hex, name, opening, OPENING_DATE)

        for i, (broker, sym, name, shares, cur, qs, mv, note) in enumerate(HOLDINGS):
            exist = await c.fetchval(
                "SELECT id FROM finance_holdings WHERE entity='mine' AND name=$1", name)
            if exist:
                kept_h += 1
                print(f"  持股已存在，跳過: {name}")
                continue
            made_h += 1
            print(f"  ＋持股 {broker}/{name}  {shares or ''} {cur}  "
                  f"{'報價 ' + qs if qs else f'手填 {mv:,}'}")
            if apply:
                await c.execute(
                    """INSERT INTO finance_holdings
                           (id, entity, broker, symbol, name, shares, currency,
                            quote_symbol, manual_value, sort_order, active,
                            note, created_at, updated_at)
                       VALUES ($1,'mine',$2,$3,$4,$5,$6,$7,$8,$9,true,$10,now(),now())""",
                    uuid.uuid4().hex, broker, sym, name,
                    float(shares) if shares is not None else None,
                    cur, qs, mv, 100 + i, note)

        row = await c.fetchrow(
            "SELECT id, active FROM finance_holdings WHERE entity='mine' AND name=$1",
            RETIRE_HOLDING)
        if row and row["active"]:
            print(f"  −停用 {RETIRE_HOLDING}（已拆成 VWRA＋美元活存，留著會重複計價）")
            if apply:
                await c.execute(
                    """UPDATE finance_holdings SET active=false, updated_at=now(),
                           note = coalesce(note,'') || ' [2026-08-26 拆明細後停用]'
                       WHERE id=$1""", row["id"])
        elif row:
            print(f"  {RETIRE_HOLDING} 已是停用，跳過")

        print(f"\n帳戶：新增 {made_a}、既有 {kept_a}；持股：新增 {made_h}、既有 {kept_h}")
        if not apply:
            print_dry_run_end()
    finally:
        await c.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", action="store_true", help="對生產庫 mediaguard（預設 dev）")
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設 dry-run）")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(a.apply, a.prod))
