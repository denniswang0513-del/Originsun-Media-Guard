# -*- coding: utf-8 -*-
"""私帳客戶分家（owner 2026-08-26「我的客戶先不要混到 crm 系統」）。

    .venv/Scripts/python.exe scripts/split_my_clients.py [--apply] [--prod]

🔴 預設 dry-run。冪等（重跑無事可做）。只**改 entity 欄**，不動任何列的其他
資料、不搬任何案子 —— 對應確認後的「併回去」是之後 owner 主導的另一步。

分家規則（保守：有任何母公司引用就留在 parent）：
  entity='mine' ⟸ 該客戶 有 ≥1 私帳案 且 0 母公司案 且 0 提案庫引用。
兩邊都有案的（典藏/文心/域創…15 個）留 parent —— 私帳的客戶下拉會一併列出
它們（list_clients entity=mine 的「已被私帳案引用」分支），不會逼 owner 建
重複客戶。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import connect, db_target, print_dry_run_end, print_target  # noqa: E402


async def run(apply: bool, prod: bool):
    dbname, dsn = db_target(prod)
    print_target(dbname, apply)
    c = await connect(dsn)
    try:
        # schema 先行（等價 main.py startup 的 ALTER；冪等、預設 parent＝零語意變更）
        await c.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS entity"
                        " VARCHAR(16) NOT NULL DEFAULT 'parent'")
        rows = await c.fetch("""
            SELECT c.id, c.short_name, coalesce(c.entity,'parent') entity,
                   count(p.id) FILTER (WHERE p.entity='mine') n_mine,
                   count(p.id) FILTER (WHERE coalesce(p.entity,'parent')='parent') n_parent,
                   (SELECT count(*) FROM preprod_proposals pp WHERE pp.client_id=c.id) n_prop
            FROM clients c JOIN crm_projects p ON p.client_id=c.id
            WHERE EXISTS (SELECT 1 FROM crm_projects x
                          WHERE x.client_id=c.id AND x.entity='mine')
            GROUP BY c.id, c.short_name, c.entity ORDER BY c.short_name""")
        move, keep, done = [], [], []
        for r in rows:
            if r["entity"] == "mine":
                done.append(r)
            elif r["n_parent"] == 0 and r["n_prop"] == 0:
                move.append(r)
            else:
                keep.append(r)
        print(f"私帳案引用的客戶 {len(rows)} 個：搬 mine {len(move)}、"
              f"留 parent {len(keep)}（有母公司引用）、已是 mine {len(done)}")
        for r in keep:
            print(f"  留 parent: {r['short_name']:<18} 母案 {r['n_parent']} 提案 {r['n_prop']}")
        for r in move[:10]:
            print(f"  搬 mine  : {r['short_name']}")
        if len(move) > 10:
            print(f"  … 共 {len(move)} 個")
        if not apply:
            print_dry_run_end()
            return
        await c.executemany(
            "UPDATE clients SET entity='mine', updated_at=now() WHERE id=$1",
            [(r["id"],) for r in move])
        n = await c.fetchval("SELECT count(*) FROM clients WHERE entity='mine'")
        print(f"\nmine 客戶合計: {n}")
        print("驗證通過 ✓" if n == len(move) + len(done) else "🔴 筆數不符")
        if n != len(move) + len(done):
            sys.exit(1)
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
