"""scripts/seed_dev_website_data.py
---
把「正式官網資料」的一個子集從 mediaguard(prod) 複製到 mediaguard_dev(dev)，
讓 8001 的官網管理 Tab + 本機 Astro 預覽(4321) 有真實資料可用、可測試。

安全性：
  - **只讀** mediaguard（prod），**只寫** mediaguard_dev（dev）。對正式零風險。
  - 冪等：可重複執行（之後要刷新 dev 資料就再跑一次）。

複製內容：
  1. 全站設定（整表鏡像）：website_settings / services / categories / credit_roles /
     credit_templates / faqs / testimonials / quick_facts / awards
  2. 團隊：crm_staff WHERE show_on_website
  3. 樣本作品（預設 15 筆 public，featured 優先）+ 連帶 crm_clients / crm_project_showcase /
     website_project_categories / website_project_seo

用法：
    cd <repo root>
    python scripts/seed_dev_website_data.py            # 預設 15 筆
    python scripts/seed_dev_website_data.py --limit 10
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import asyncpg

_PG = dict(user="originsun", password="originsun2026", host="192.168.1.132", port=5432)
PROD_DB = "mediaguard"
DEV_DB = "mediaguard_dev"

# 全站設定表：dev 整表鏡像成 prod（先清空 dev 再灌）
CONFIG_TABLES = [
    "website_settings", "website_services", "website_categories",
    "website_credit_roles", "website_credit_templates",
    "website_faqs", "website_testimonials", "website_quick_facts", "website_awards",
]


async def _table_exists(conn, table: str) -> bool:
    return await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=$1)", table
    )


async def _cols(conn, table: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position", table
    )
    return [r["column_name"] for r in rows]


async def _copy(src, dst, table: str, where: str | None = None) -> int:
    """SELECT * (where) from src.table → INSERT into dst.table。ON CONFLICT DO NOTHING。
    回傳實際讀到的列數。呼叫前 dst 那批列應已清掉（冪等由呼叫端負責）。"""
    if not await _table_exists(src, table) or not await _table_exists(dst, table):
        print(f"  [SKIP] {table}（來源或目標無此表）")
        return 0
    cols = await _cols(src, table)
    cl = ", ".join(f'"{c}"' for c in cols)
    q = f"SELECT {cl} FROM {table}" + (f" WHERE {where}" if where else "")
    rows = await src.fetch(q)
    if not rows:
        return 0
    ph = ", ".join(f"${i+1}" for i in range(len(cols)))
    ins = f"INSERT INTO {table} ({cl}) VALUES ({ph}) ON CONFLICT DO NOTHING"
    for r in rows:
        await dst.execute(ins, *[r[c] for c in cols])
    return len(rows)


async def _del(dst, table: str, where: str | None = None) -> None:
    if await _table_exists(dst, table):
        await dst.execute(f"DELETE FROM {table}" + (f" WHERE {where}" if where else ""))


async def main(limit: int) -> None:
    prod = await asyncpg.connect(database=PROD_DB, **_PG)
    dev = await asyncpg.connect(database=DEV_DB, **_PG)
    try:
        # 1) 挑樣本作品 id（featured 優先，再依 public_number）
        proj_rows = await prod.fetch(
            "SELECT id, client_id FROM crm_projects WHERE public = true "
            "ORDER BY public_featured DESC NULLS LAST, public_number ASC NULLS LAST "
            f"LIMIT {int(limit)}"
        )
        pids = [r["id"] for r in proj_rows]
        cids = sorted({r["client_id"] for r in proj_rows if r["client_id"]})
        if not pids:
            print("⚠ 正式庫沒有 public=true 的作品，無樣本可複製。"); return
        print(f"=== 樣本作品 {len(pids)} 筆、關聯客戶 {len(cids)} 筆 ===")
        ids_sql = "(" + ",".join("'" + str(p).replace("'", "''") + "'" for p in pids) + ")"
        cids_sql = "(" + ",".join("'" + str(c).replace("'", "''") + "'" for c in cids) + ")" if cids else "('')"

        # 2) 清 dev 端要重灌的列（反向外鍵順序）
        print("=== 清 dev 端舊資料（依外鍵反向）===")
        await _del(dev, "website_project_categories")          # 參照 website_categories + crm_projects
        await _del(dev, "website_project_seo", f"project_id IN {ids_sql}")
        await _del(dev, "crm_project_showcase", f"id IN {ids_sql}")
        for t in CONFIG_TABLES:                                # 設定表整表清
            await _del(dev, t)
        await _del(dev, "crm_projects", f"id IN {ids_sql}")    # 只清樣本作品，不動其他 dev CRM 資料

        # 3) 正向順序灌資料
        print("=== 複製設定表（整表鏡像 prod）===")
        for t in CONFIG_TABLES:
            n = await _copy(prod, dev, t)
            print(f"  [OK] {t}: {n}")

        print("=== 複製客戶 / 團隊（UPSERT，不動其他 dev 資料）===")
        n_cli = await _copy(prod, dev, "clients", f"id IN {cids_sql}")
        print(f"  [OK] clients: {n_cli}")
        n_staff = await _copy(prod, dev, "crm_staff", "show_on_website = true")
        print(f"  [OK] crm_staff(show_on_website): {n_staff}")

        print("=== 複製樣本作品 + 連帶 ===")
        n_proj = await _copy(prod, dev, "crm_projects", f"id IN {ids_sql}")
        print(f"  [OK] crm_projects: {n_proj}")
        n_show = await _copy(prod, dev, "crm_project_showcase", f"id IN {ids_sql}")
        print(f"  [OK] crm_project_showcase: {n_show}")
        n_pc = await _copy(prod, dev, "website_project_categories", f"project_id IN {ids_sql}")
        print(f"  [OK] website_project_categories: {n_pc}")
        n_seo = await _copy(prod, dev, "website_project_seo", f"project_id IN {ids_sql}")
        print(f"  [OK] website_project_seo: {n_seo}")

        # 4) 摘要 + featured 檢查
        feat = await dev.fetchval("SELECT count(*) FROM crm_projects WHERE public=true AND public_featured=true")
        pub = await dev.fetchval("SELECT count(*) FROM crm_projects WHERE public=true")
        print(f"\n✅ 完成：dev 現有 public 作品 {pub} 筆，其中 featured {feat} 筆"
              + ("" if feat else "  ⚠ 無 featured — Astro 首頁 build 可能空，建議在正式挑幾筆設精選再重跑"))
    finally:
        await prod.close()
        await dev.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=15, help="樣本作品數（預設 15）")
    args = ap.parse_args()
    try:
        asyncio.run(main(args.limit))
    except Exception as e:
        print(f"❌ 失敗：{type(e).__name__}: {e}")
        sys.exit(1)
