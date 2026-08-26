# -*- coding: utf-8 -*-
"""私帳 Sheet 的某一段重匯（owner 2026-08-27「重新匯入 4430 以後的資料，並把
相對應的舊資料刪除」）。

    .venv/Scripts/python.exe scripts/reimport_ledger_block.py --csv 私帳.csv \
        [--from-row 4430] [--apply] [--prod]

🔴 預設 dry-run。**先配對再刪**：對每一列 Sheet 資料，用
(日期, 金額量級, 摘要前 12 字) 去帳上找同一筆（消耗式 —— 同鍵有幾筆就配幾筆），
配到的舊列刪掉、Sheet 那列重新寫進去。配不到的直接新增。
**沒被配到的帳上列一律不動**（同期間還有別的帳戶/別的來源）。

為什麼不是「刪掉日期區間內全部再灌」：那個區間裡有 545 筆別的來源的列
（2026-08-27 實測），整段清掉等於拿這份卡單去炸掉銀行帳。

Sheet 欄（表頭在第 15 列）：
    0 日期 / 1 帳戶 / 2 支出 / 3 信用卡 / 4 存入 / 5 備註1(資訊) /
    6 備註2(其他資訊) / 7 類別 / 8 項目 / 9 子項目

寫入對映：
- 信用卡欄有值 → status='card'、expense=金額（退刷為負）、
  bank_account_id=**卡片帳戶**（帳戶欄：台新→台新信用卡、支出→富邦信用卡）
- 否則銀行列 → expense/deposit 照欄位，bank_account_id=同名銀行帳戶
- 類別＝core.cash_taxonomy.join_category(類別, 項目)（儲存仍是複合鍵）；
  item/sub_item 各自入欄。空的就留空 —— UI 會顯示紅點提醒補（owner 要的）
- 備註1→summary、備註2→bank_memo（銀行/卡單那側的原始資訊）
"""
import asyncio
import csv
import io
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.cash_taxonomy import join_category  # noqa: E402
from scripts._common import connect, db_target, print_dry_run_end, print_target  # noqa: E402

# 帳戶欄 → 系統帳戶（卡片帳戶用 kind='card'，其餘是銀行帳戶）
CARD_ACCOUNTS = {"台新": "台新信用卡", "支出": "富邦信用卡"}
BANK_ALIASES = {"永豐": "永豐（個人）", "台新銀行": "台新（個人）",
                "富邦": "富邦-收入戶", "匯豐": "匯豐（個人）"}


def _money(s):
    s = (s or "").replace("NT$", "").replace(",", "").replace(" ", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    try:
        v = int(float(s.strip("()")))
    except Exception:
        return 0
    return -v if neg else v


def _iso(s):
    p = re.split(r"[/\-]", (s or "").strip())
    if len(p) != 3:
        return None
    try:
        return datetime(int(p[0]), int(p[1]), int(p[2]), tzinfo=timezone.utc)
    except ValueError:
        return None


def load_block(csv_path, from_row):
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8")))
    out = []
    for r in rows[from_row - 1:]:
        r = (list(r) + [""] * 16)[:16]
        d = _iso(r[0])
        if not d:
            continue
        card, out_, in_ = _money(r[3]), _money(r[2]), _money(r[4])
        out.append({
            "date": d, "acct": r[1].strip(),
            "card": card, "out": out_, "in": in_,
            "summary": (r[5].strip() or "（無摘要）")[:250],
            "memo": r[6].strip(), "book": r[7].strip(),
            "item": r[8].strip(), "sub": r[9].strip(),
        })
    return out


def _key(day, amt, summary):
    return (day, abs(int(amt or 0)), (summary or "")[:12])


async def run(csv_path, from_row, apply, prod):
    dbname, dsn = db_target(prod)
    print_target(dbname, apply)
    blk = load_block(csv_path, from_row)
    if not blk:
        print("🔴 這一段沒有可用的列（日期解析不出來）")
        sys.exit(1)
    lo = min(x["date"] for x in blk).strftime("%Y-%m-%d")
    hi = max(x["date"] for x in blk).strftime("%Y-%m-%d")
    print(f"Sheet 第 {from_row} 列起：{len(blk)} 筆，{lo} → {hi}")
    n_card = sum(1 for x in blk if x["card"])
    n_blank = sum(1 for x in blk if not x["book"])
    print(f"  信用卡列 {n_card}／銀行列 {len(blk) - n_card}；類別空白 {n_blank}（UI 顯示紅點）")

    c = await connect(dsn)
    try:
        # 帳戶對映（缺的卡片帳戶就建 —— 卡別是這次匯入的重點）
        accts = {r["name"]: dict(r) for r in await c.fetch(
            "SELECT id, name, acct_kind FROM bank_accounts WHERE entity='mine'")}
        need_cards = {CARD_ACCOUNTS[a["acct"]] for a in blk
                      if a["card"] and a["acct"] in CARD_ACCOUNTS}
        make_cards = [n for n in sorted(need_cards) if n not in accts]
        for n in make_cards:
            print(f"  ＋建立卡片帳戶：{n}")
        unknown = sorted({a["acct"] for a in blk
                          if not a["card"] and a["acct"]
                          and BANK_ALIASES.get(a["acct"], a["acct"]) not in accts})
        if unknown:
            print(f"  🔴 對不到系統帳戶的帳戶欄：{unknown} —— 中止（別把列寫成沒帳戶）")
            sys.exit(1)

        db = [dict(r) for r in await c.fetch(
            "SELECT id, to_char(entry_date,'YYYY-MM-DD') d, summary, expense, deposit"
            " FROM crm_cash_entries WHERE entity='mine'")]
        pool = {}
        for x in db:
            if not x["d"] or not (lo <= x["d"] <= hi):
                continue
            pool.setdefault(_key(x["d"], x["expense"] or x["deposit"], x["summary"]),
                            []).append(x["id"])
        del_ids, matched = [], 0
        for b in blk:
            amt = b["card"] or b["out"] or b["in"]
            k = _key(b["date"].strftime("%Y-%m-%d"), amt, b["summary"])
            if pool.get(k):
                del_ids.append(pool[k].pop())
                matched += 1
        print(f"\n配對舊資料：{matched}／{len(blk)}（配不到的 {len(blk) - matched} 筆直接新增）")
        print(f"將刪除舊列 {len(del_ids)} 筆、寫入新列 {len(blk)} 筆")
        left = sum(len(v) for v in pool.values())
        print(f"同期間**不動**的帳上列：{left}")
        if not apply:
            for b in blk[:5]:
                print(f"   {b['date']:%Y-%m-%d} {b['acct']:<4} "
                      f"{(b['card'] or b['out'] or b['in']):>8} | {b['summary'][:22]:<22} | "
                      f"{join_category(b['book'], b['item']) or '（空）'}／{b['sub'] or '—'}")
            print_dry_run_end()
            return

        now = datetime.now(timezone.utc)
        for n in make_cards:
            cid = uuid.uuid4().hex
            await c.execute(
                "INSERT INTO bank_accounts (id, entity, name, acct_kind, opening_balance,"
                "        active, sort_order, note, created_at, updated_at)"
                " VALUES ($1,'mine',$2,'card',0,true,200,"
                "         '[2026-08-27 重匯建立] 卡別身分（刷卡列掛這裡）', now(), now())",
                cid, n)
            accts[n] = {"id": cid, "name": n, "acct_kind": "card"}
        if del_ids:
            await c.execute("DELETE FROM crm_cash_entries WHERE id = ANY($1::text[])", del_ids)
        made = 0
        for b in blk:
            if b["card"]:
                acct_name = CARD_ACCOUNTS.get(b["acct"], "")
                acct_id = (accts.get(acct_name) or {}).get("id")
                status, expense, deposit = "card", b["card"], None
            else:
                acct_id = (accts.get(BANK_ALIASES.get(b["acct"], b["acct"])) or {}).get("id")
                status, expense, deposit = None, (b["out"] or None), (b["in"] or None)
            await c.execute(
                "INSERT INTO crm_cash_entries (id, entity, entry_date, expense, deposit,"
                "        summary, bank_memo, category, item, sub_item, status,"
                "        bank_account_id, has_invoice, created_at, updated_at)"
                " VALUES ($1,'mine',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,0,$12,$12)",
                uuid.uuid4().hex, b["date"], expense, deposit, b["summary"],
                b["memo"] or None, join_category(b["book"], b["item"]) or None,
                b["item"] or None, b["sub"] or None, status, acct_id, now)
            made += 1
        print(f"\n刪除 {len(del_ids)}、寫入 {made}")
        # 驗證：這一段的列數與金額回頭比對
        got = await c.fetchrow(
            "SELECT count(*) n, sum(coalesce(expense,0)) ex, sum(coalesce(deposit,0)) dp"
            " FROM crm_cash_entries WHERE entity='mine' AND created_at = $1", now)
        want_ex = sum((b["card"] or b["out"] or 0) for b in blk)
        want_dp = sum(b["in"] or 0 for b in blk)
        print(f"驗證：寫入 {got['n']} 列（應 {len(blk)}）；支出合計 {got['ex']:,}（應 {want_ex:,}）；"
              f"存入合計 {got['dp']:,}（應 {want_dp:,}）")
        ok = got["n"] == len(blk) and got["ex"] == want_ex and got["dp"] == want_dp
        print("驗證通過 ✓" if ok else "🔴 對不上")
        if not ok:
            sys.exit(1)
    finally:
        await c.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="私帳 Sheet 匯出的 CSV（gid=1067034519）")
    ap.add_argument("--from-row", type=int, default=4430, help="從第幾列起（1-based，含）")
    ap.add_argument("--prod", action="store_true", help="對生產庫 mediaguard（預設 dev）")
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設 dry-run）")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(a.csv, a.from_row, a.apply, a.prod))
