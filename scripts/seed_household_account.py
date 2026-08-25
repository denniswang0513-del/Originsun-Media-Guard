# -*- coding: utf-8 -*-
"""家用往來科目＋家用類別改掛（owner 2026-08-26「代墊家用需要有一個帳戶」）。

    .venv/Scripts/python.exe scripts/seed_household_account.py [--apply] [--prod]

🔴 預設 dry-run。冪等：科目以 code=1310 為鍵、對映只搬「目前掛在業主往來的
家用% 類別」（重跑無事可做）。

語意：家用支出＝owner 代墊（別人欠你的錢＝資產），家人還款＝沖銷 —— 不是
業主提取（權益）。搬完後 BS 兩側同幅上移（權益少扣、資產多一條「家用代墊」），
差額不變；餘額口徑＝owner Sheet「公司-富邦帳戶預付款/家用」那條公式，外加
銀行轉帳付的 4 筆 22,100（owner 2026-08-26 拍板：代墊不分支付方式）→ 651,997。

⚠️ finance_category_map 是全域表（無 entity 欄）—— 家用% 類別只存在於私帳
資料，母公司帳零筆，搬了對母公司照舊是空操作。
"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import connect, db_target, print_dry_run_end, print_target  # noqa: E402

CODE = "1310"          # 挨著 1300 員工往來-預支；同為「往來」資產
NAME = "家用往來"


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
                       VALUES ($1,$2,$3,'代墊家用的往來餘額 —— 支出＝墊出去、還款＝收回來',
                               'asset','operating',false,135,true,now())""",
                    acct_id, CODE, NAME)

        rows = await c.fetch(
            """SELECT m.id, m.category_text, a.name acct
               FROM finance_category_map m JOIN finance_accounts a ON a.id=m.account_id
               WHERE m.source='cash' AND m.category_text LIKE '家用%'
               ORDER BY m.category_text""")
        move = [r for r in rows if r["acct"] != NAME]
        for r in rows:
            mark = "→ 搬" if r["acct"] != NAME else "已掛，跳過"
            print(f"  {r['category_text']:<14} 現掛 {r['acct']:<12} {mark}")
        if apply and move:
            await c.executemany(
                "UPDATE finance_category_map SET account_id=$2 WHERE id=$1",
                [(r["id"], acct_id) for r in move])
        print(f"\n對映搬移 {len(move)} 條（treatment 維持 transfer）")

        # 驗證：搬完後家用往來的推導餘額（支−存，全歷史）
        bal = await c.fetchval(
            """SELECT sum(coalesce(e.expense,0) - coalesce(e.deposit,0))
               FROM crm_cash_entries e
               WHERE e.entity='mine' AND e.category IN (
                   SELECT category_text FROM finance_category_map
                   WHERE source='cash' AND account_id=$1)""",
            acct_id) if apply else None
        if apply:
            print(f"家用往來推導餘額: {bal or 0:,}（預期 651,997）")
            if (bal or 0) != 651_997:
                print("🔴 與預期不符 —— 檢查回填/類別是否又動過")
                sys.exit(1)
            print("驗證通過 ✓")
        else:
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
