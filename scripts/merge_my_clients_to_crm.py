# -*- coding: utf-8 -*-
"""私帳客戶併入 CRM 主檔（owner 2026-08-26「把私帳的客戶都整合到 crm 系統裡面」）。

    .venv/Scripts/python.exe scripts/merge_my_clients_to_crm.py [--csv 客戶清單.csv] [--apply] [--prod]

🔴 預設 dry-run。冪等（沒有 entity='mine' 客戶時什麼都不做）。

做三件事：
  1. entity='mine' → 'parent'（客戶主檔只有一份，兩本帳共用；crm_link_id 清空
     —— 併完之後沒有「私帳客戶 → CRM 客戶」的對應可言）。
     🔴 併之前先查模糊重名：併過去會讓同一家公司在 CRM 出現兩筆的，整批中止
     （寧可先人工併，不要靜靜長出重複客戶）。
  2. --csv 給客戶清單（欄位同 CRM 匯入：客戶代稱/抬頭/統編/匯款備註）時，
     回補缺的抬頭/統編/匯款備註（不覆蓋既有值）。
  3. 重算全部客戶分級（規則正本 core.crm_logic.client_tier；併完之後私帳案也
     算進客戶關係，見 _auto_update_client_status 的說明）。

錢不受影響：金額的門綁在**專案**的 entity，不在客戶。
"""
import asyncio
import csv
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.crm_logic import client_tier, normalize_tax_id  # noqa: E402
from routers.crm._shared import _CLIENT_TIER_EXCLUDE_STATUSES  # noqa: E402
from routers.crm.clients import _norm_client_name  # noqa: E402
from scripts._common import connect, db_target, print_dry_run_end, print_target  # noqa: E402

_nn = lambda s: re.sub(r"\s+", "", s or "")      # noqa: E731


def load_sheet(path):
    if not path:
        return []
    rows = list(csv.reader(io.open(path, encoding="utf-8")))
    hdr = next(i for i, r in enumerate(rows) if "抬頭" in r)
    out, seen = [], set()
    for r in rows[hdr + 1:]:
        r = (r + ["", "", "", ""])[:4]
        short, full, tax, memo = (c.strip() for c in r)
        full, tax = _nn(full), normalize_tax_id(tax)
        if not full and not tax:
            continue
        k = tax or full
        if k not in seen:
            seen.add(k)
            out.append({"short": short, "full": full, "tax": tax, "memo": memo})
    return out


async def run(csv_path, apply, prod):
    dbname, dsn = db_target(prod)
    print_target(dbname, apply)
    sheet = load_sheet(csv_path)
    c = await connect(dsn)
    try:
        db = [dict(r) for r in await c.fetch(
            "SELECT id, short_name, full_name, tax_id, coalesce(entity,'parent') entity FROM clients")]
        mine = [d for d in db if d["entity"] == "mine"]
        par = [d for d in db if d["entity"] == "parent"]
        print(f"客戶：CRM {len(par)}／私帳 {len(mine)}")

        # ① 模糊重名檢查（併過去會變成 CRM 兩筆同一家）
        pidx = {}
        for d in par:
            t = normalize_tax_id(d["tax_id"])
            if t:
                pidx.setdefault("t:" + t, d)
            for nm in (d["short_name"], d["full_name"]):
                if nm:
                    pidx.setdefault("n:" + _norm_client_name(nm), d)
        clash = []
        for m in mine:
            t = normalize_tax_id(m["tax_id"])
            hit = (pidx.get("t:" + t) if t else None) \
                or pidx.get("n:" + _norm_client_name(m["short_name"])) \
                or (pidx.get("n:" + _norm_client_name(m["full_name"])) if m["full_name"] else None)
            if hit:
                clash.append((m, hit))
        if clash:
            print(f"\n🔴 {len(clash)} 家併過去會與既有 CRM 客戶重複 —— 整批中止，請先人工併：")
            for m, p in clash:
                print(f"   私帳「{m['short_name']}」↔ CRM「{p['short_name']}」")
            sys.exit(1)
        print("模糊重名檢查：0 家衝突 ✓")

        # ② 回補（來自客戶清單 CSV）
        fills = []
        if sheet:
            idx = {}
            for d in db:
                t = normalize_tax_id(d["tax_id"])
                if t:
                    idx.setdefault("t:" + t, d)
                for nm in (d["short_name"], d["full_name"]):
                    if nm:
                        idx.setdefault("n:" + _nn(nm), d)
                        idx.setdefault("f:" + _norm_client_name(nm), d)
            for s in sheet:
                m = (idx.get("t:" + s["tax"]) if s["tax"] else None) \
                    or idx.get("n:" + s["full"]) or idx.get("f:" + _norm_client_name(s["full"]))
                if not m:
                    continue
                need = {}
                if s["tax"] and not normalize_tax_id(m["tax_id"]):
                    need["tax_id"] = s["tax"]
                if s["full"] and not _nn(m["full_name"]):
                    need["full_name"] = s["full"]
                if need:
                    need["_memo"] = s["memo"]
                    fills.append((m, need))
        print(f"回補抬頭/統編：{len(fills)} 家")
        for m, need in fills[:8]:
            print(f"   {m['short_name'][:20]:<20} ← " +
                  "、".join(f"{k}={v}" for k, v in need.items() if not k.startswith("_")))
        if len(fills) > 8:
            print(f"   … 共 {len(fills)} 家")

        print(f"\n將併入 CRM 的私帳客戶：{len(mine)} 家")
        for m in mine[:6]:
            print(f"   {m['short_name']}")
        if len(mine) > 6:
            print(f"   … 共 {len(mine)} 家")
        if not apply:
            print_dry_run_end()
            return

        for m, need in fills:
            sets, args = [], [m["id"]]
            for k in ("tax_id", "full_name"):
                if need.get(k):
                    args.append(need[k])
                    sets.append(f"{k}=${len(args)}")
            if need.get("_memo"):
                args.append(need["_memo"])
                sets.append(f"payment_note=coalesce(nullif(payment_note,''), ${len(args)})")
            await c.execute(f"UPDATE clients SET {', '.join(sets)}, updated_at=now() WHERE id=$1", *args)
        n = await c.execute("""UPDATE clients SET entity='parent', crm_link_id=NULL,
                                   updated_at=now() WHERE entity='mine'""")
        print(f"\n併入 CRM：{n}；回補 {len(fills)} 家")

        # ③ 分級重算（規則正本 core.crm_logic.client_tier）
        rows = await c.fetch("""
            SELECT cl.id, cl.status,
                   (SELECT count(*) FROM crm_projects p
                     WHERE p.client_id=cl.id AND coalesce(p.status,'') <> ALL($1::text[])) n
            FROM clients cl WHERE coalesce(cl.status,'') <> '暫停合作'""",
            list(_CLIENT_TIER_EXCLUDE_STATUSES))
        changed = 0
        for r in rows:
            tier = client_tier(r["n"])
            if tier != (r["status"] or ""):
                await c.execute("UPDATE clients SET status=$2, updated_at=now() WHERE id=$1",
                                r["id"], tier)
                changed += 1
        print(f"分級重算：{changed} 家變動")
        left = await c.fetchval("SELECT count(*) FROM clients WHERE entity='mine'")
        tot = await c.fetchval("SELECT count(*) FROM clients")
        print(f"驗證：私帳客戶剩 {left}（應為 0）／CRM 主檔共 {tot} 家")
        if left:
            sys.exit(1)
        print("驗證通過 ✓")
    finally:
        await c.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="", help="客戶清單 CSV（客戶代稱/抬頭/統編/匯款備註）")
    ap.add_argument("--prod", action="store_true", help="對生產庫 mediaguard（預設 dev）")
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設 dry-run）")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(a.csv, a.apply, a.prod))
