# -*- coding: utf-8 -*-
"""其他金融資產科目＋轉匯類別改掛（owner 2026-08-29）。

    .venv/Scripts/python.exe scripts/seed_financial_asset_account.py [--apply] [--prod]

🔴 預設 dry-run。冪等：科目以 code=1400 為鍵、對映只搬「目前**不是**掛在
1400」的那幾個類別（重跑無事可做）。

語意：`轉匯與定存` 這個類別裡混了三種東西 ——
  ① 富邦幾個戶頭之間互轉（50 組，一出一進都在系統裡）
  ② 只有轉出：行動換匯 264 萬、定期定額買台股、定存、匯到沒追蹤的銀行…
  ③ 只有轉入：源日薪資、股款交割、股利、其他銀行匯入…
①的兩腳同類別同科目，位置互相抵銷；②③ 的淨額才是重點 —— 那些錢**沒有被
花掉也不是業主提取**，只是換了個地方放（證券戶、定存、沒追蹤的銀行）。

搬之前它們全落在「業主往來」（權益），帳上等於說 owner 提走了 6,105,017。
搬完後 BS 兩側同幅上移（權益少扣、資產多一條「其他金融資產」），差額不變 ——
與 2026-08-26 家用往來那次同一個手法（scripts/seed_household_account.py）。

**刻意不搬**的三個同族類別（它們不是金融資產）：
  轉匯與定存_代收款（別人的錢暫放）、_代支款（代人付）、_公司信用卡（公司卡往來）
它們留在業主往來 —— 那三個本來就是「往來」語意，淨額 +940,537。

⚠️ finance_category_map 是全域表（無 entity 欄）—— 這兩個類別只存在於私帳
資料，母公司帳零筆，搬了對母公司是空操作。
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import connect, db_target, print_dry_run_end, print_target  # noqa: E402

CODE = "1400"          # 1300/1310 往來、1500 器材之間的空號
NAME = "其他金融資產"
DESC = "換匯／定存／證券／沒被系統追蹤的銀行帳戶 —— 錢沒被花掉，只是換了個地方放"

#: 要改掛的類別（同族的代收/代支/公司信用卡刻意不動，見檔頭）
CATEGORIES = ("轉匯與定存", "轉匯與定存_證券")


async def run(apply: bool, prod: bool):
    dbname, dsn = db_target(prod)
    print_target(dbname, apply)
    c = await connect(dsn)
    try:
        acct_id = await c.fetchval(
            "SELECT id FROM finance_accounts WHERE code=$1", CODE)
        if acct_id:
            print(f"  科目 {CODE} {NAME} 已存在，沿用")
        else:
            acct_id = uuid.uuid4().hex
            print(f"  ＋科目 {CODE} {NAME}（asset/operating）")
            if apply:
                await c.execute(
                    """INSERT INTO finance_accounts
                           (id, code, name, name_plain, acct_type, cf_activity,
                            is_system, sort_order, active, created_at)
                       VALUES ($1,$2,$3,$4,'asset','operating',false,140,true,now())""",
                    acct_id, CODE, NAME, DESC)

        rows = await c.fetch(
            """SELECT m.id, m.category_text, a.name acct
                 FROM finance_category_map m
                 LEFT JOIN finance_accounts a ON a.id = m.account_id
                WHERE m.source='cash' AND m.category_text = ANY($1::text[])
                ORDER BY m.category_text""", list(CATEGORIES))
        move = [r for r in rows if r["acct"] != NAME]
        for r in rows:
            mark = "→ 搬" if r["acct"] != NAME else "已掛，跳過"
            print(f"  {r['category_text']:<14} 現掛 {r['acct'] or '(無)':<12} {mark}")
        missing = set(CATEGORIES) - {r["category_text"] for r in rows}
        for k in sorted(missing):
            print(f"  {k:<14} 帳上沒有這個對映 —— 跳過（不無中生有）")

        # 影響數字：這幾個類別的收支淨額（支出−存入＝搬進資產的金額）
        eff = await c.fetchrow(
            """SELECT count(*) n, coalesce(sum(expense),0) - coalesce(sum(deposit),0) net
                 FROM crm_cash_entries
                WHERE entity='mine' AND category = ANY($1::text[])""",
            list(CATEGORIES))
        print(f"\n  影響 {eff['n']} 筆收支：權益的『業主往來』少扣 {eff['net']:,}、"
              f"資產多一條『{NAME}』{eff['net']:,} —— 兩側同幅，勾稽差額不變")

        if apply and move:
            await c.executemany(
                "UPDATE finance_category_map SET account_id=$2 WHERE id=$1",
                [(r["id"], acct_id) for r in move])
            print(f"  ✅ 已改掛 {len(move)} 個類別")
        elif not move:
            print("  （沒有要搬的，冪等結束）")
        else:
            print_dry_run_end()
    finally:
        await c.close()


if __name__ == "__main__":
    asyncio.run(run("--apply" in sys.argv, "--prod" in sys.argv))
