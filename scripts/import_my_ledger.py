# -*- coding: utf-8 -*-
"""owner 私帳 Google Sheet「總資產」全明細 → crm_cash_entries（我的帳 entity='mine'）。

    # dry-run（預設；預設 dev 庫）— 只解析與報告，不寫
    .venv/Scripts/python.exe scripts/import_my_ledger.py --csv assets.csv
    # 真的寫入 / 對生產庫
    .venv/Scripts/python.exe scripts/import_my_ledger.py --csv assets.csv --apply [--prod]

🔴 預設 dry-run。契約比照 scripts/import_cashbook.py / import_invoices.py。

CSV 來源：私帳 Sheet「總資產」分頁（gid=1067034519）→ export?format=csv。
版面：前 14 列是資產 dashboard 小工具，第 15 列（index 14）是明細表頭
「日期,帳戶,支出,信用卡,存入,…」→ 用 _common.find_header('日期') 定位。

設計決策（2026-08-24 owner 拍板「一本帳全進、三表只吃公司切片」）：
1. **全明細都匯**（公司+個人+家用+轉匯+信用卡），類別三層原樣保留：
   DB category ＝「類別_項目」複合鍵（＝Sheet 的標籤欄格式，如 公司_專案／
   個人_生活）—— 與母公司的 cash 對映鍵（專案/薪水…）天然隔離，不互撞。
2. **公司_專案按方向拆鍵**：存入＝公司_專案（direct_income 4100）、支出＝
   公司_專案支出（direct_expense 5200）。三表引擎一個 category 只有一個
   treatment，混向會讓專案成本被當營收折讓。
3. **非公司切片一律 treatment='transfer'**（內部移動）→ 不進損益、銀行餘額
   照算 —— 「三表只吃公司」就是在對映層實現的，引擎不用改。
4. **信用卡消費列**（金額在「信用卡」欄）：expense 記費用、bank_account_id
   留空（刷卡當下不動銀行）、status='card'。月底「信用卡款」繳款列（類別=
   信用卡）才是銀行流出，對映 transfer（消費已逐筆記過，繳款是清償不是費用）。
5. **公司_器材 → transfer(1500)**：購置資產化，損益走折舊（器材庫，階段 4）。
   費用化會與折舊重複計。
6. 日期照 repo 慣例 **UTC 午夜**（_parse_shoot_date 的規矩；台北 UTC+8，兩個
   讀取端同一天）。民國年（0114/12/21）+1911。
7. **期初回推**：opening_balance ＝ 期末目標（Sheet dashboard 各帳戶「期末現金」）
   − Σ淨流。2026-08-24 dev 實測：回推出的期初（700,069／81,754／411,225）與
   Sheet 自己記的「期初現金」一字不差 —— 流水帳頭尾自洽的獨立證明。
8. 冪等：--apply 先清 entity='mine' 的 crm_cash_entries 與 bank_accounts 再整批
   重建（我的帳除本匯入外無資料；絕不碰 parent）。科目對映只補缺不覆蓋。
9. 類別與標籤欄皆空的列（2026-08 實測 244 筆：ATM/跨轉/利息/分期）category 留
   NULL → 進「未歸類」清單，是階段 2 AI 分類層的第一批工作，不硬猜。
"""
import csv
import io
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import (assert_safe_rebuild, cli, connect, db_target, find_header,  # noqa: E402
                             money, parse_date, print_dry_run_end, print_target)

# ── 帳戶（期末目標 = Sheet dashboard 各帳戶「期末現金」）─────────────────
ACCOUNTS = [
    # (id, 顯示名, 銀行, 帳號後碼, 期末目標, Sheet 帳戶欄別名, is_default)
    ("mine_fubon_in",   "富邦-收入戶", "台北富邦", "218604", 1_520_435, "收入", False),
    ("mine_fubon_out",  "富邦-支出戶", "台北富邦", "213467",     2_697, "支出", True),
    ("mine_fubon_save", "富邦-儲蓄戶", "台北富邦", "651214",   182_523, "儲蓄", False),
    ("mine_taishin",    "台新（個人）", "台新",     "",         246_075, "台新", False),
    ("mine_sinopac",    "永豐（個人）", "永豐",     "",          25_213, "永豐", False),
]
ACCT_ID = {alias: aid for aid, _, _, _, _, alias, _ in ACCOUNTS}

# ── 科目對映種子（source='cash'）────────────────────────────────────────
CAT_MAP = {
    "公司_專案":      ("direct_income",  "4100"),
    "公司_專案支出":  ("direct_expense", "5200"),
    "公司_薪水":      ("direct_expense", "6100"),
    "公司_加班":      ("direct_expense", "6100"),
    "公司_年終獎金":  ("direct_expense", "6120"),
    "公司_業績獎金":  ("direct_expense", "6120"),
    "公司_軟體與耗材": ("direct_expense", "6220"),
    "公司_教育訓練":  ("direct_expense", "6320"),
    "公司_餐敘":      ("direct_expense", "6300"),
    "公司_其他":      ("direct_expense", "6990"),
    "公司":           ("direct_income",  "4200"),
    "公司_股利":      ("transfer", "3200"),   # 盈餘分配（權益，不進損益）
    "公司_股東往來":  ("transfer", "3200"),
    "公司_代墊":      ("transfer", "1300"),   # 代墊往來（收支互沖）
    "公司_器材":      ("transfer", "1500"),   # 資產化 — 損益走折舊
    "公司_預收款":    ("transfer", "3200"),
    "信用卡":         ("transfer", "1100"),   # 卡費繳款＝清償
}


def _map_for(tag: str):
    if tag in CAT_MAP:
        return CAT_MAP[tag]
    if tag.startswith("公司"):
        return ("direct_expense", "6990")
    return ("transfer", "3200")   # 個人/家用/轉匯/雜項 → 業主往來（不進損益）


# 這張表混了外幣欄（US$／裸 $）且有民國年格 —— 兩個旗標開在這裡，
# 其他四支腳本不開，差異就寫在呼叫端看得見的地方（正本在 _common）。
def _money(s: str) -> int:
    return money(s, usd=True)


def _date(s: str):
    return parse_date(s, minguo=True)


def _cat_key(cat: str, item: str, is_out: bool) -> str:
    tag = f"{cat}_{item}" if item else cat
    if tag == "公司_專案" and is_out:
        return "公司_專案支出"
    return tag


def load_rows(csv_path: str):
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8")))
    h = find_header(rows, "日期")
    out, skipped = [], []
    for i, r in enumerate(rows[h + 1:], start=h + 2):
        def col(j):
            return (r[j] if len(r) > j else "").strip()
        if not col(0):
            continue
        dt = _date(col(0))
        if dt is None:
            skipped.append((i, col(0)))
            continue
        exp, card, dep = _money(col(2)), _money(col(3)), _money(col(4))
        if exp == 0 and card == 0 and dep == 0:
            skipped.append((i, "無金額"))
            continue
        cat, item, sub = col(7), col(8), col(9)
        if not cat and col(10):                        # 類別空 → 從標籤欄補
            tag = col(10).rstrip("_")
            cat = tag.split("_")[0]
            if "_" in tag and not item:
                item = tag.split("_", 1)[1]
        is_card = card != 0 and exp == 0
        key = _cat_key(cat, item, exp > 0 or card > 0) if cat else ""
        summary = re.split(r"[\n\t]", col(5), 1)[0][:250] or (item or cat or "（無摘要）")
        note = []
        rest = re.split(r"[\n\t]", col(5), 1)
        if len(rest) > 1 and rest[1].strip():
            note.append(rest[1].strip())
        if col(6):
            note.append(col(6))
        note.append("[sheet-import]")
        pcode, plabel = col(13), col(12)
        out.append({
            "date": dt,
            "expense": (exp or card) or None,
            "deposit": dep or None,
            "bank": None if is_card else ACCT_ID.get(col(1)),
            "category": key or None, "item": item or None,
            "sub_item": sub or (col(11) or None),      # 子項目，缺則收「款別」
            "summary": summary, "note": " ".join(note),
            "project_label": (f"{pcode}｜{plabel}" if pcode and plabel
                              else plabel or pcode) or None,
            "status": "card" if is_card else None,
        })
    return out, skipped


async def run(csv_path: str, apply: bool, prod: bool, force: bool = False):
    dbname, dsn = db_target(prod)
    entries, skipped = load_rows(csv_path)
    n_card = sum(1 for e in entries if e["status"] == "card")
    n_uncat = sum(1 for e in entries if not e["category"])
    print_target(dbname, apply)
    print(f"解析：{len(entries)} 筆（信用卡 {n_card}、未歸類 {n_uncat}），跳過 {len(skipped)}")
    for s in skipped[:10]:
        print("  skip:", s)
    if not apply:
        agg = {}
        for e in entries:
            k = e["category"] or "（未歸類）"
            a = agg.setdefault(k, [0, 0, 0])
            a[0] += e["deposit"] or 0
            a[1] += e["expense"] or 0
            a[2] += 1
        print(f"\n{'category':24}{'存入':>14}{'支出':>14}{'筆數':>6}")
        for k, (d, x, n) in sorted(agg.items(), key=lambda kv: -(kv[1][0] + kv[1][1])):
            print(f"{k:24}{d:>14,}{x:>14,}{n:>6}")
        print_dry_run_end(hint=True)
        return

    c = await connect(dsn)
    try:
        n1 = await c.fetchval("SELECT count(*) FROM crm_cash_entries WHERE entity='mine'")
        n2 = await c.fetchval("SELECT count(*) FROM bank_accounts WHERE entity='mine'")
        await assert_safe_rebuild(c, [
            ("卡單匯入的收支", "SELECT count(*) FROM crm_cash_entries"
             " WHERE entity='mine' AND note LIKE '%[卡單匯入]%'"),
            ("非 Sheet 匯入的 mine 收支", "SELECT count(*) FROM crm_cash_entries"
             " WHERE entity='mine' AND (note IS NULL OR note NOT LIKE '%[sheet-import]%')"),
        ], force)
        print(f"清場（冪等，只碰 mine）：entries={n1} accounts={n2} → 重建")
        await c.execute("DELETE FROM crm_cash_entries WHERE entity='mine'")
        await c.execute("DELETE FROM bank_accounts WHERE entity='mine'")

        tags = sorted({e["category"] for e in entries if e["category"]})
        added = 0
        for t in tags:
            treatment, acct = _map_for(t)
            r = await c.execute(
                """INSERT INTO finance_category_map
                       (id, source, category_text, account_id, treatment, active)
                   VALUES ($1,'cash',$2,$3,$4,true)
                   ON CONFLICT (source, category_text) DO NOTHING""",
                uuid.uuid4().hex, t, acct, treatment)
            added += r.endswith("1")
        print(f"科目對映：{len(tags)} key，新增 {added}（既有不覆蓋）")

        await c.executemany(
            """INSERT INTO crm_cash_entries
               (id, entity, entry_date, expense, deposit, summary, note,
                category, item, sub_item, bank_account_id, project_label, status,
                has_invoice, created_at)
               VALUES ($1,'mine',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,0,now())""",
            [(uuid.uuid4().hex, e["date"], e["expense"], e["deposit"],
              e["summary"], e["note"], e["category"], e["item"], e["sub_item"],
              e["bank"], e["project_label"], e["status"]) for e in entries])
        print(f"匯入明細：{len(entries)} 筆")

        first_date = min(e["date"] for e in entries)
        print("\n=== 帳戶期初回推 + 餘額驗證 ===")
        all_ok = True
        for aid, name, bank, acct_no, target_end, _alias, is_default in ACCOUNTS:
            flow = await c.fetchval(
                """SELECT COALESCE(SUM(COALESCE(deposit,0)-COALESCE(expense,0)
                                       -COALESCE(bank_fee,0)-COALESCE(claim,0)),0)
                   FROM crm_cash_entries WHERE entity='mine' AND bank_account_id=$1""",
                aid)
            opening = target_end - flow
            await c.execute(
                """INSERT INTO bank_accounts
                       (id, entity, name, bank_name, account_no, acct_kind,
                        opening_balance, opening_date, is_default, active)
                   VALUES ($1,'mine',$2,$3,$4,'bank',$5,$6,$7,true)""",
                aid, name, bank, acct_no, opening, first_date, is_default)
            bal = opening + flow
            ok = bal == target_end
            all_ok = all_ok and ok
            print(f"  {'✓' if ok else '✗'} {name}: 期初 {opening:,} + 淨流 {flow:+,}"
                  f" = {bal:,}（目標 {target_end:,}）")
        n = await c.fetchval("SELECT count(*) FROM crm_cash_entries WHERE entity='mine'")
        rev = await c.fetchval(
            """SELECT COALESCE(SUM(COALESCE(deposit,0)),0) FROM crm_cash_entries
               WHERE entity='mine' AND category='公司_專案'""")
        print(f"\n總筆數 {n}｜公司_專案存入 {rev:,}（Sheet 錨點 20,191,994）")
        if not all_ok or rev != 20_191_994:
            print("🔴 驗證未全過 — 檢查上面明細")
            sys.exit(1)
        print("驗證全過 ✓")
    finally:
        await c.close()


if __name__ == "__main__":
    cli(run, "總資產分頁的 CSV（gid=1067034519）")
