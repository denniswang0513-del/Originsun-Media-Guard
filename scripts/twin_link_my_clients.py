# -*- coding: utf-8 -*-
"""私帳客戶 ↔ CRM 客戶：**兩邊各一筆＋連結**（owner 2026-08-26 定案）。

    .venv/Scripts/python.exe scripts/twin_link_my_clients.py [--csv 客戶清單.csv] [--apply] [--prod]

🔴 預設 dry-run。冪等（已連結的跳過）。

owner 的話：「crm 有一筆，私帳有一筆，中間做連結」「留下這個連結的清單好做
日後使用」「雙方客戶連結後以 crm 的客戶清單為主要清單」「日後私帳如果新增
客戶，就直接新增進去 crm」。

做四件事：
  ① 還原：先前誤併進 CRM 的私帳客戶（只有私帳案、無母公司案/提案引用）改回
     entity='mine' —— 私帳清單保持自有。
  ② 代稱唯一鍵：全域 → (entity, short_name)，同一家公司兩本帳各一筆才存得下
     （不改就得替第二筆亂改名）。
  ③ 建 CRM 分身：私帳客戶在**公司客戶清單 CSV** 裡有的（統編/名稱對得上），
     在 CRM 建同名一筆（抬頭/統編/匯款備註取自 CSV）。已存在對應的 CRM 客戶
     就直接用它，不重建。
  ④ 寫連結：mine.crm_link_id → CRM 那筆。

不在 CSV 裡的私帳客戶＝公司沒有往來，**不建、不連**（日後真要往來，客戶管理
頁一鍵連結）。錢完全不受影響：金額的門綁在專案的 entity，不在客戶。
"""
import asyncio
import csv
import io
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.crm_logic import client_tier, normalize_tax_id  # noqa: E402
from routers.crm._shared import _CLIENT_TIER_EXCLUDE_STATUSES  # noqa: E402
from routers.crm.clients import _norm_client_name  # noqa: E402
from scripts._common import connect, db_target, print_dry_run_end, print_target  # noqa: E402


def _nn(s):
    return re.sub(r"\s+", "", s or "")


def load_sheet(path):
    """公司客戶清單 CSV（欄位同 CRM 匯入：客戶代稱/抬頭/統編/匯款備註）。"""
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
        # ── ① 還原誤併的私帳客戶 ──────────────────────────────
        back = await c.fetch(
            "SELECT id, short_name, full_name, tax_id, crm_link_id FROM clients cl"
            " WHERE coalesce(cl.entity,'parent')='parent'"
            "   AND EXISTS (SELECT 1 FROM crm_projects p"
            "               WHERE p.client_id=cl.id AND p.entity='mine')"
            "   AND NOT EXISTS (SELECT 1 FROM crm_projects p"
            "                   WHERE p.client_id=cl.id"
            "                     AND coalesce(p.entity,'parent')='parent')"
            "   AND NOT EXISTS (SELECT 1 FROM preprod_proposals pp"
            "                   WHERE pp.client_id=cl.id)")
        print(f"① 還原為私帳客戶：{len(back)} 家")
        print("② 代稱唯一鍵 → (entity, short_name)")

        if apply:
            await c.execute("ALTER TABLE clients DROP CONSTRAINT IF EXISTS clients_short_name_key")
            await c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_client_entity_short_name"
                            " ON clients (entity, short_name)")
            if back:
                await c.executemany(
                    "UPDATE clients SET entity='mine', updated_at=now() WHERE id=$1",
                    [(r["id"],) for r in back])

        # ── ③④ 建分身 ＋ 寫連結 ───────────────────────────────
        mine = [dict(r) for r in await c.fetch(
            "SELECT id, short_name, full_name, tax_id, crm_link_id FROM clients"
            " WHERE entity='mine' ORDER BY short_name")]
        if not apply:
            # dry-run 時第①步還沒寫入 —— 拿「將被還原的那批」一起試算，
            # 否則後面幾步永遠顯示 0（看不出這支到底要做什麼）
            mine += [dict(r) for r in back]
        par = [dict(r) for r in await c.fetch(
            "SELECT id, short_name, full_name, tax_id FROM clients"
            " WHERE coalesce(entity,'parent')='parent'")]
        if not apply:
            # dry-run：把「將被還原的那批」自 CRM 側排除，否則它們會跟**自己**
            # 比對成功，預覽顯示「沿用既有 CRM 客戶」而不是「要建分身」
            _back_ids = {r["id"] for r in back}
            par = [d for d in par if d["id"] not in _back_ids]
        pidx = {}
        for d in par:
            t = normalize_tax_id(d["tax_id"])
            if t:
                pidx.setdefault("t:" + t, d)
            for nm in (d["short_name"], d["full_name"]):
                if nm:
                    pidx.setdefault("n:" + _norm_client_name(nm), d)
        sidx = {}
        for s_ in sheet:
            if s_["tax"]:
                sidx.setdefault("t:" + s_["tax"], s_)
            sidx.setdefault("n:" + _norm_client_name(s_["full"]), s_)

        make, reuse, skip = [], [], 0
        for m in mine:
            if m["crm_link_id"]:
                skip += 1
                continue
            t = normalize_tax_id(m["tax_id"])
            row = ((sidx.get("t:" + t) if t else None)
                   or sidx.get("n:" + _norm_client_name(m["short_name"]))
                   or (sidx.get("n:" + _norm_client_name(m["full_name"]))
                       if m["full_name"] else None))
            if not row:
                continue                     # 不在公司客戶清單 → 不建不連
            hit = ((pidx.get("t:" + row["tax"]) if row["tax"] else None)
                   or pidx.get("n:" + _norm_client_name(row["full"])))
            (reuse if hit else make).append((m, row, hit))
        none_n = len(mine) - skip - len(make) - len(reuse)
        print(f"③ 建 CRM 分身：{len(make)} 家／沿用既有 CRM 客戶：{len(reuse)} 家"
              f"／已連結跳過：{skip}／不在公司清單（不建不連）：{none_n}")
        for m, row, _h in make[:8]:
            print(f"   ＋{m['short_name'][:22]:<22} 統編 {row['tax']}")
        if len(make) > 8:
            print(f"   … 共 {len(make)} 家")
        for m, _r, h in reuse[:5]:
            print(f"   ↔{m['short_name'][:22]:<22} 連到既有 CRM「{h['short_name']}」")

        if not apply:
            print_dry_run_end()
            return

        made = 0
        for m, row, hit in make + reuse:
            target = hit["id"] if hit else None
            if target is None:
                target = uuid.uuid4().hex
                await c.execute(
                    "INSERT INTO clients (id, entity, short_name, full_name, tax_id,"
                    "        payment_note, status, notes, created_at, updated_at)"
                    " VALUES ($1,'parent',$2,$3,$4,$5,'潛在客戶',"
                    "         '[2026-08-26 私帳客戶對應] 與私帳同名客戶連結', now(), now())",
                    target, m["short_name"], row["full"] or m["full_name"] or "",
                    row["tax"] or m["tax_id"] or "", row["memo"] or None)
                made += 1
            await c.execute("UPDATE clients SET crm_link_id=$2, updated_at=now() WHERE id=$1",
                            m["id"], target)
        print(f"\n④ 建立 CRM 分身 {made} 家、寫入連結 {len(make) + len(reuse)} 筆")

        # 分級重算（CRM 客戶只算公司案；私帳客戶不套 CRM 分級）
        rows = await c.fetch(
            "SELECT cl.id, cl.status,"
            "       (SELECT count(*) FROM crm_projects p"
            "         WHERE p.client_id=cl.id"
            "           AND coalesce(p.entity,'parent')='parent'"
            "           AND coalesce(p.status,'') <> ALL($1::text[])) n"
            " FROM clients cl"
            " WHERE coalesce(cl.entity,'parent')='parent'"
            "   AND coalesce(cl.status,'') <> '暫停合作'",
            list(_CLIENT_TIER_EXCLUDE_STATUSES))
        changed = 0
        for r in rows:
            tier = client_tier(r["n"])
            if tier != (r["status"] or ""):
                await c.execute("UPDATE clients SET status=$2, updated_at=now() WHERE id=$1",
                                r["id"], tier)
                changed += 1
        print(f"分級重算（只算公司案）：{changed} 家變動")

        n_mine = await c.fetchval("SELECT count(*) FROM clients WHERE entity='mine'")
        n_par = await c.fetchval(
            "SELECT count(*) FROM clients WHERE coalesce(entity,'parent')='parent'")
        n_link = await c.fetchval(
            "SELECT count(*) FROM clients WHERE entity='mine' AND crm_link_id IS NOT NULL")
        bad = await c.fetchval(
            "SELECT count(*) FROM clients m"
            " WHERE m.entity='mine' AND m.crm_link_id IS NOT NULL"
            "   AND NOT EXISTS (SELECT 1 FROM clients p WHERE p.id=m.crm_link_id"
            "                     AND coalesce(p.entity,'parent')='parent')")
        print(f"驗證：私帳 {n_mine} 家（已連結 {n_link}）／CRM {n_par} 家"
              f"／連結指向非 CRM 客戶 {bad}")
        if bad:
            sys.exit(1)
        print("驗證通過 ✓")
    finally:
        await c.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="", help="公司客戶清單 CSV（客戶代稱/抬頭/統編/匯款備註）")
    ap.add_argument("--prod", action="store_true", help="對生產庫 mediaguard（預設 dev）")
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設 dry-run）")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(a.csv, a.apply, a.prod))
