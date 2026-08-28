# -*- coding: utf-8 -*-
"""私帳分類樹的**第四、五層**回填（階段 4）。

    .venv/Scripts/python.exe scripts/backfill_cash_depth.py --csv 私帳.csv [--apply] [--prod]

## 為什麼要這一支

owner 的 Sheet 只有三個分類欄（類別／項目／子項目）。某一支要長第四、五層時，
他把值溢出到旁邊兩欄 —— 原話「那時候表沒地方擴充 只好先暫時放在專案的位置」：

    家用 ▸ 變動支出 ▸ 醫療保健 ▸ 乳癌治療 ▸ 台北馬偕
     H        I          J        L款別     M專案標籤   ← 後兩個是溢出
    公司 ▸ 專案 ▸ 已收帳款/已付稅款/委外已付
     H      I      L款別（J 是空的）

當初匯入只吃了 H/I/J，**款別整欄（615 筆）沒進系統**；醫院那 55 筆則躺在
`project_label` 純文字欄。分類樹（階段 1-3）已經把這些位置備好了，這支負責把
歷史值搬進去。

## 規則

- 配對走 `reconcile_ledger_sheet.match_sheet_to_db`（**同一份規則**，那邊已驗
  4,733/4,733 零差異）。
- 目標路徑 = `reconcile_ledger_sheet.sheet_path()`（H/I/J ＋ 款別 ＋ 醫院），
  **從 Sheet 推不是從現況推** —— 從現況往下走的話重跑第二次就不冪等。
- 🔴 **只往下加，不橫向改**：Sheet 的 H/I/J 跟帳上現在的前三層對不起來就跳過並
  列出來 —— 那是分類本身有歧見，不是「深度不夠」，該由 reconcile 那支處理。
- 🔴 節點**必須已經存在**（種子灌過）。查無就跳過並列出來，不自己造節點 ——
  Sheet 上一個打錯的款別會默默長出一個新分類，比留白難發現得多。
- 醫院搬進樹之後 `project_label` 清空：那一欄要留給真專案。
- **冪等**：目標路徑從 Sheet 推、不從「現在掛在哪」往下走，所以重跑只會報
  「已經掛好了」。

2026-08-27 dev 實跑：670 筆（352 已收帳款／158 已付稅款／100 委外已付／
51 台北馬偕／4 淡水馬偕／5 只到乳癌治療）＋清掉 55 筆 project_label；
5 筆拒絕（款別「已收帳款」出現在 `公司_專案` 以外的項目底下，樹上沒那個位置
—— 要 owner 判斷是 Sheet 的項目填錯還是那幾個位置也該有已收帳款）。

預設 dry-run。
"""
import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.cash_taxonomy import mirror_from_path  # noqa: E402
from scripts._common import (connect, db_target, load_taxonomy_tree,  # noqa: E402
                             node_for_path, print_dry_run_end, print_target)
from scripts.reconcile_ledger_sheet import (HOSPITALS, db_dir,  # noqa: E402
                                            load_sheet, make_db_acct,
                                            match_sheet_to_db, sheet_acct,
                                            sheet_path)

ENTITY = "mine"


async def run(csv_path, apply, prod, limit):
    dbname, dsn = db_target(prod)
    print_target(dbname, apply)
    sheet, bad = load_sheet(csv_path)
    print(f"Sheet 可用列 {len(sheet)}；日期壞掉的 {len(bad)}")

    c = await connect(dsn)
    try:
        accts = {r["name"]: dict(r) for r in await c.fetch(
            "SELECT id, name FROM bank_accounts WHERE entity=$1", ENTITY)}
        by_id = {a["id"]: a for a in accts.values()}
        # 🔴 一定要 ORDER BY：沒有排序時 Postgres 回的實體順序會隨更新改變，
        # 而 L4 那層（只靠日期/方向/金額）遇到「同日同額無摘要」的雙胞胎時
        # 是任配一隻 —— 於是重跑一次那兩筆就對調（2026-08-27 實測踩到）。
        db = [dict(r) for r in await c.fetch(
            "SELECT id, to_char(entry_date,'YYYY-MM-DD') d, summary, expense, deposit,"
            "       category, item, sub_item, status, bank_account_id,"
            "       taxonomy_node_id, project_label"
            "  FROM crm_cash_entries WHERE entity=$1"
            " ORDER BY entry_date, id", ENTITY)]
        print(f"系統私帳列 {len(db)}")

        db_acct = make_db_acct(by_id)

        pairs, _loose, no_db, no_sheet, _typo = match_sheet_to_db(
            sheet, db, db_dir, db_acct, sheet_acct)
        print(f"配對成功 {len(pairs)}；Sheet 有帳上沒有 {len(no_db)}；"
              f"帳上有 Sheet 沒有 {len(no_sheet)}")

        child, path = await load_taxonomy_tree(c)
        todo, skip_shallow, skip_nonode, skip_mismatch, already = [], [], [], [], []
        for s, e in pairs:
            full = sheet_path(s)           # 完整路徑（規則與 reconcile 同一份）
            want3 = [x for x in (s["book"], s["item"], s["sub"]) if x]
            if len(full) <= len(want3):
                continue                   # 沒有溢出到第四、五層，這支不管
            cur = path.get(e["taxonomy_node_id"] or "", [])
            if not cur:
                skip_shallow.append((s, e, "帳上這一列還沒掛節點"))
                continue
            # 🔴 只往下加：Sheet 的前三層要跟帳上現在的對得起來
            if cur[:len(want3)] != want3:
                skip_mismatch.append((s, e, " ▸ ".join(cur), " ▸ ".join(want3)))
                continue
            # 🔴 目標路徑從 **Sheet** 推（前三層 ＋ 溢出的那幾層），不是從「這一列
            # 現在掛在哪」往下走 —— 那樣重跑第二次就不冪等了：第一次跑完它已經
            # 掛在深處，再從那裡往下找同一個名字當然找不到，675 筆會全部被誤報成
            # 「樹上沒有那個節點」（2026-08-27 實測踩到）。
            nid = node_for_path(child, full)
            if not nid:
                skip_nonode.append((s, e, " ▸ ".join(full)))
                continue
            if nid == e["taxonomy_node_id"]:
                already.append((s, e))
                continue
            todo.append((s, e, nid, path[nid]))

        drop_label = [t for t in todo if t[1]["project_label"]
                      and t[1]["project_label"] in HOSPITALS]

        print("\n── 回填盤點 ──")
        print(f"要往下掛的         {len(todo):>5}")
        print(f"  順便清 project_label {len(drop_label):>3}（醫院搬進樹了，那欄留給真專案）")
        print(f"已經掛好了         {len(already):>5}")
        print(f"🔴 前三層對不起來  {len(skip_mismatch):>5}（分類有歧見，歸 reconcile 那支管）")
        print(f"🔴 樹上沒有那個節點 {len(skip_nonode):>5}（不自己造 —— 打錯的款別會默默長出新分類）")
        print(f"🔴 帳上還沒掛節點  {len(skip_shallow):>5}")

        if todo:
            print("\n── 會變成這樣（前幾筆）──")
            for s, e, _nid, p in todo[:limit]:
                print(f"  {s['date']} {s['amt']:>9,} {s['summary'][:22]:<22} "
                      f"→ {' ▸ '.join(p)}")
            print("\n── 目標路徑分佈 ──")
            for p, n in Counter(" ▸ ".join(t[3]) for t in todo).most_common():
                print(f"  {n:>5}  {p}")
        for label, rows in (("前三層對不起來", skip_mismatch), ("樹上沒有那個節點", skip_nonode)):
            if rows:
                print(f"\n── 🔴 {label}（前 {limit}）──")
                for r in rows[:limit]:
                    print("  " + " | ".join(str(x) for x in r[2:])
                          + f"   [{r[0]['date']} {r[0]['summary'][:18]}]")

        if not apply:
            print_dry_run_end()
            return

        # 🔴 三欄鏡射照樣重算（第四、五層不進那三欄，所以絕大多數不會變）——
        # 規則走 core.cash_taxonomy.mirror_from_path，不在這裡自己拼字串。
        changed = 0
        async with c.transaction():
            for _s, e, nid, p in todo:
                cat, item, sub = mirror_from_path(p)
                await c.execute(
                    "UPDATE crm_cash_entries SET taxonomy_node_id=$1, category=$2,"
                    " item=$3, sub_item=$4, updated_at=NOW() WHERE id=$5",
                    nid, cat, item, sub, e["id"])
                changed += 1
            n_label = 0
            for _s, e, _nid, _p in drop_label:
                await c.execute("UPDATE crm_cash_entries SET project_label=NULL"
                                " WHERE id=$1", e["id"])
                n_label += 1
        print(f"\n已回填 {changed} 筆；清掉 {n_label} 筆的 project_label（醫院已進樹）")
    finally:
        await c.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--limit", type=int, default=15, help="每一類最多列幾筆")
    ap.add_argument("--prod", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(a.csv, a.apply, a.prod, a.limit))
