# -*- coding: utf-8 -*-
"""收支雙備註遷移（owner 2026-08-26「兩個備註欄：一個銀行帳戶本來的資訊、
一個我的附註」）：私帳既有 note 拆進 bank_memo（銀行原始資訊）與 note（手寫）。

    .venv/Scripts/python.exe scripts/split_cash_notes.py [--apply] [--prod]

🔴 預設 dry-run。冪等：只處理「bank_memo 還是空、note 帶匯入標記」的列 ——
跑過一次之後這批列的 note 已不帶標記，重跑無事可做。母公司列不動（那邊的
note 本來就是人寫的）。

拆法：去掉 [sheet-import]/[卡單匯入] 標記後，剩餘文字按**來源特徵**分類：
  銀行側（→ bank_memo）：分期餘額（本筆分期/年百分率）、國外交易服務費、
  跨轉摘要、純拉丁商家串、行尾大寫城市/商家碼（TAIPEI/HIROSH…）、
  長數字帳號開頭。
  其餘（→ note 保留）＝人寫的（「256G 記憶卡」「昱涵牙刷牙膏」這種）。
分錯的列 owner 在清單上點一下就能改（本批同時上的行內編輯），所以規則
取「大宗正確」而不是完美。
"""
import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import connect, db_target, print_dry_run_end, print_target  # noqa: E402

MARK_RE = re.compile(r"\s*\[(sheet-import|卡單匯入)\]\s*")

_BANK_PATTERNS = [
    re.compile(r"本筆分期|年百分率"),
    re.compile(r"^國外交易服務費"),
    re.compile(r"^(網路|行動)(跨轉|自轉|換匯)"),
    # 對帳單摘要欄的通路詞（不是人寫的）
    re.compile(r"^(企網跨收|跨行轉帳|匯入款$|沖正|街口支付扣款|信用卡轉|ＣＤ提款|ATM|JCB|總行$)"),
    re.compile(r"^[A-Z0-9 .\-*/&'()]+$"),          # 純拉丁商家串
    re.compile(r"[A-Z]{4,}$"),                     # 行尾大寫城市/商家碼
    re.compile(r"^\d{10,}"),                       # 帳號開頭（跨轉備註）
    # 跨轉對方行資訊（對帳單的分行別欄）：中國信託/中山分行/營業部/信用卡處…
    re.compile(r"銀行|分行|信託|營業部|郵局|合作社|信用卡處|農會|商銀"),
]


def split_note(note: str):
    """(bank_memo, note) —— 沒有標記的列回 (None, None)＝不動。"""
    if not note or "[sheet-import]" not in note and "[卡單匯入]" not in note:
        return None, None
    body = MARK_RE.sub(" ", note).strip()
    if not body:
        return "", ""                              # 純標記 → 兩欄都清空
    if any(p.search(body) for p in _BANK_PATTERNS):
        return body, ""
    return "", body                                # 人寫的留在附註


async def run(apply: bool, prod: bool):
    dbname, dsn = db_target(prod)
    print_target(dbname, apply)
    c = await connect(dsn)
    try:
        await c.execute("ALTER TABLE crm_cash_entries ADD COLUMN IF NOT EXISTS bank_memo TEXT")
        rows = await c.fetch("""
            SELECT id, note FROM crm_cash_entries
            WHERE entity='mine' AND (bank_memo IS NULL OR bank_memo='')
              AND (note LIKE '%[sheet-import]%' OR note LIKE '%[卡單匯入]%')""")
        upd, n_bank, n_note, n_blank = [], 0, 0, 0
        samples_bank, samples_note = [], []
        for r in rows:
            bm, nt = split_note(r["note"])
            if bm is None:
                continue
            upd.append((r["id"], bm or None, nt or None))
            if bm:
                n_bank += 1
                if len(samples_bank) < 8:
                    samples_bank.append(bm)
            elif nt:
                n_note += 1
                if len(samples_note) < 8:
                    samples_note.append(nt)
            else:
                n_blank += 1
        print(f"待拆 {len(upd)} 列：銀行資訊 {n_bank}／保留附註 {n_note}／純標記清空 {n_blank}")
        print("\n-- 銀行資訊樣本 --")
        for s in samples_bank:
            print(f"  {s[:64]!r}")
        print("-- 保留附註樣本 --")
        for s in samples_note:
            print(f"  {s[:64]!r}")
        if not apply:
            print_dry_run_end()
            return
        await c.executemany(
            "UPDATE crm_cash_entries SET bank_memo=$2, note=$3, updated_at=now() WHERE id=$1",
            upd)
        left = await c.fetchval("""
            SELECT count(*) FROM crm_cash_entries
            WHERE entity='mine' AND (note LIKE '%[sheet-import]%' OR note LIKE '%[卡單匯入]%')""")
        print(f"\n已拆 {len(upd)} 列；殘留帶標記: {left}")
        print("驗證通過 ✓" if left == 0 else "🔴 還有帶標記的列")
        if left:
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
