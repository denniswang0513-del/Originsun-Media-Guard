# -*- coding: utf-8 -*-
"""owner 私帳 Sheet「淨值快照時序」→ finance_net_snapshots（entity='mine'）
＋持股種子 → finance_holdings。

    .venv/Scripts/python.exe scripts/import_my_snapshots.py --csv snap.csv [--apply] [--prod]

🔴 預設 dry-run。契約比照 scripts/import_my_ledger.py。

CSV 來源：私帳 Sheet gid=1020655022（淨值快照，2021/3/14 起 ~117 列）。
版面：兩個區塊都是「統計日期」開頭的表；第二區塊（歷史時序）才是要匯的
（第一區塊是最新兩列的即時工具）。以「統計日期」表頭定位第二區塊。

桶名對映＝表頭原文（生活帳戶/公司資產(現金)/公司資產(應收帳款)/源日資本額/
預付帳款/備用金(Past)/備用金/財富自由(總額)/其他資產/信用卡/家用帳戶）——
原樣保留，儀表板照桶名渲染。#REF!（早年公式斷鏈）的格＝略過該桶；
total 由現存桶加總（Sheet 的總額欄若是 #REF! 就用加總）。

持股種子（idempotent upsert by (entity, name)）：dashboard 可辨識的持股 ——
0050/2330（TWSE 報價）、VTI/VEA/VWO（Yahoo）；盈透證券整戶與 Firstrade
現金等不可逐檔核實的，以 manual_value 一列代表，owner 之後在 UI 細分。
"""
import csv
import io
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import (cli, connect, db_target, money,  # noqa: E402
                             parse_date, print_dry_run_end, print_target)

BUCKET_COLS = ["生活帳戶", "公司資產(現金)", "公司資產(應收帳款)", "源日資本額",
               "預付帳款", "備用金(Past)", "備用金", "財富自由(總額)",
               "其他資產", "信用卡", "家用帳戶"]

# 持股種子：(broker, symbol, name, shares, currency, quote_symbol, manual_value)
HOLDINGS_SEED = [
    ("富邦證券", "0050", "元大台灣50", 71363, "TWD", "tse:0050", None),
    ("富邦證券", "2330", "台積電", 6000, "TWD", "tse:2330", None),
    ("Firstrade", "VTI", "Vanguard Total Stock Market", 57.73715, "USD", "yahoo:VTI", None),
    ("Firstrade", "VEA", "Vanguard FTSE Developed", 0.77, "USD", "yahoo:VEA", None),
    ("Firstrade", "VWO", "Vanguard FTSE Emerging", 0.88738, "USD", "yahoo:VWO", None),
    ("Firstrade", "", "美元活存", None, "USD", "", 39_000),      # $1,226.79 × ~31.8
    ("盈透證券", "", "盈透證券（整戶）", None, "TWD", "", 19_930_991),
]


# 這張表的空格與 #REF!（早年公式斷鏈）要跟「真的是 0」分得開 —— 唯一開
# none_on_bad 的呼叫端，旗標寫在這裡讓下一個人一眼看到差在哪（正本在 _common）。
def _money(s: str):
    return money(s, none_on_bad=True)


def load_snapshots(csv_path: str):
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8")))
    # 兩個表頭區塊：第一區塊（「日期」，最新即時列 —— owner 當天更新的那列在這）
    # ＋第二區塊（「統計日期」，2021 起歷史時序）。**兩塊都要**，各用各的欄位
    # 對映（同名桶、不同欄位偏移）；同一天重複時取後出現者。
    hdr_is = [i for i, r in enumerate(rows)
              if any(c.strip() in ("統計日期", "日期") for c in r)]
    out = []
    for bi, hdr_i in enumerate(hdr_is):
        end = hdr_is[bi + 1] if bi + 1 < len(hdr_is) else len(rows)
        hdr = [c.strip() for c in rows[hdr_i]]
        date_ix = next(i for i, c in enumerate(hdr) if c in ("統計日期", "日期"))
        bucket_ix = {b: i for i, c in enumerate(hdr) for b in BUCKET_COLS if c == b}
        for r in rows[hdr_i + 1:end]:
            d = parse_date(r[date_ix] if len(r) > date_ix else "")
            if not d:
                continue
            buckets = {}
            for b, i in bucket_ix.items():
                v = _money(r[i] if len(r) > i else "")
                if v is not None and v != 0:
                    buckets[b] = v
            if not buckets:
                continue
            out.append({"date": d, "buckets": buckets,
                        "total": sum(buckets.values())})
    dedup = {}
    for s_ in out:
        dedup[s_["date"]] = s_
    return sorted(dedup.values(), key=lambda x: x["date"])


async def run(csv_path: str, apply: bool, prod: bool, force: bool = False):
    dbname, dsn = db_target(prod)
    snaps = load_snapshots(csv_path)
    print_target(dbname, apply)
    print(f"快照：{len(snaps)} 列，{snaps[0]['date']:%Y-%m-%d} → {snaps[-1]['date']:%Y-%m-%d}")
    print(f"最新一列 total: {snaps[-1]['total']:,}（Sheet 2026/8/24 總額錨點 53,636,960 —— "
          "容許差=＃REF!略過桶）")
    print(f"持股種子：{len(HOLDINGS_SEED)} 列")
    if not apply:
        for s_ in snaps[-3:]:
            print(f"  {s_['date']:%Y-%m-%d} total={s_['total']:,} buckets={len(s_['buckets'])}")
        print_dry_run_end()
        return

    c = await connect(dsn)
    try:
        n0 = await c.fetchval("SELECT count(*) FROM finance_net_snapshots WHERE entity='mine'")
        print(f"\n清場：mine snapshots={n0} → 重建")
        await c.execute("DELETE FROM finance_net_snapshots WHERE entity='mine'")
        import json
        await c.executemany(
            """INSERT INTO finance_net_snapshots
                   (id, entity, snap_date, buckets, total, note, created_at)
               VALUES ($1,'mine',$2,$3::jsonb,$4,'[sheet-import]',now())""",
            [(uuid.uuid4().hex, s_["date"],
              json.dumps(s_["buckets"], ensure_ascii=False), s_["total"])
             for s_ in snaps])
        n = await c.fetchval("SELECT count(*) FROM finance_net_snapshots WHERE entity='mine'")
        print(f"快照寫入 {n} 列")

        # 持股 upsert by (entity, name)
        made = kept = 0
        for broker, sym, name, shares, cur, qs, mv in HOLDINGS_SEED:
            exist = await c.fetchval(
                "SELECT id FROM finance_holdings WHERE entity='mine' AND name=$1", name)
            if exist:
                kept += 1
                continue
            await c.execute(
                """INSERT INTO finance_holdings
                       (id, entity, broker, symbol, name, shares, currency,
                        quote_symbol, manual_value, sort_order, active,
                        note, created_at, updated_at)
                   VALUES ($1,'mine',$2,$3,$4,$5,$6,$7,$8,$9,true,
                           '[sheet-import]',now(),now())""",
                uuid.uuid4().hex, broker, sym, name, shares, cur, qs, mv, made)
            made += 1
        print(f"持股：新增 {made}、既有保留 {kept}")
        if n != len(snaps):
            print("🔴 快照筆數不符")
            sys.exit(1)
        print("驗證通過 ✓")
    finally:
        await c.close()


if __name__ == "__main__":
    cli(run, "淨值快照分頁 CSV（gid=1020655022）")
