# -*- coding: utf-8 -*-
"""收支明細 Google Sheet → crm_cash_entries（母公司帳 entity='parent'）。

    # 只出對帳報告，不寫任何東西（預設；預設 dev 庫）
    .venv/Scripts/python.exe scripts/import_cashbook.py --csv cash.csv
    # 只匯某些年度（試匯用）
    .venv/Scripts/python.exe scripts/import_cashbook.py --csv cash.csv --years 2026
    # 真的寫入 / 對生產庫
    .venv/Scripts/python.exe scripts/import_cashbook.py --csv cash.csv --years 2026 --apply [--prod]

🔴 預設 dry-run。契約比照 scripts/import_invoices.py / import_petty_cash.py。

CSV 來源：Sheet「收支明細」分頁（gid=200471177）→ export?format=csv。

這份分頁的版面（2026-08-19 實測）：
1. 前 28 列是期初/期末金額小工具，真表頭在第 29 列 → 用「應收發票」欄名當指紋
   自動偵測（「日期」二字在小工具區也可能出現，不可靠）。
2. 這是**富邦帳戶的流水帳**：期初 3,047,170 ＋ 存入合計 − 支出合計 ＝ 期末 869,478
   完全吻合（全表 1,419 列）。每列恰好只填「支出」或「存入」一邊，實測 0 例外。
3. 🔴「請款」欄**不可**對映到系統的 claim 欄。這欄在 Sheet 是「該筆應收的總額
   註記」（例：ＣＤ轉收 232,050 分 5 筆存入，每筆請款欄都寫 232,050；代開發票
   42,000 實收 8,400，請款欄寫 42,000），純備忘、不是另一筆錢。但系統的 claim
   在餘額公式裡是**流出**（core.finance_logic.cash_entry_flow ＝ deposit −
   expense − bank_fee − claim）—— 對映過去會讓富邦餘額憑空少 2,230,427（＝請款
   欄合計，2026-08-19 實測）。所以只寫進 note 當備忘。
4. 「收支」欄整欄都是常數「收支」、「資料來源」欄是常數「記帳表_富邦」→ 兩欄
   皆無資訊量，刻意不匯（單帳戶流水帳，來源已由本 docstring 記錄）。

🔴 欄位對映的唯一陷阱：Sheet 的「項目」（轉存/發票代開/請款單…）要進系統的
**category** 欄 —— 三表引擎（core/finance_logic.py iter_expense_items 等）查
finance_category_map 用的是 e["category"]；後端 import_cash_csv 的 _CASH_COL_MAP
把「項目」對到 item 欄，那條路匯進去報表會整批變「未歸類支出」。所以不走後端
CSV 端點，本腳本直接建列。

「應收發票」欄（金額_名稱_申請人_發票號，與發票 Sheet 的索引標籤同格式）：
第 3 段是**申請人**不是收款人 → 不塞 payee（那欄語意是 AP 收款人「姓名_身分證」），
整串進 note 保真；發票明細靠 invoice_id 連結即可回查。

去重：收支明細沒有天然唯一鍵，而且**完全相同的兩列是合法資料**（實測
2025/04/01 勞健保 428 網路自轉 ×2）→ 不能用 set 去重（會誤砍第二筆）。
改用多重集：先數 DB 既有列每個 key 出現幾次，來源列逐一「消耗」既有配額，
配額歸零後的同 key 列照常新增 → 重跑不重複、雙胞胎列不誤殺。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import io
import os
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from scripts._common import find_header, parse_date, resolve_db_url  # noqa: E402
from db.models import CrmCashEntry, CrmInvoice  # noqa: E402
from routers.crm.finance import _parse_money  # noqa: E402

HEADER_MARK = "應收發票"   # 表頭列的指紋（期初/期末小工具區沒有這個字）
ENTITY = "parent"          # 匯入一律落母公司帳（我的帳不走匯入）

# ── 逐筆修正表（比照 import_invoices.py：改動要看得見、可稽核、能一眼推翻）──
#
# 鍵＝(日期原文, 支出, 存入, 附註) —— 收支明細沒有單號可當鍵，這四項組合實測唯一。
# owner 2026-08-19 核可：「這是我寫錯請你修改」。每一筆都寫明**為什麼**判定是手誤，
# 判準只有兩條，不合的一律不改、改列 REVIEW：
#   (a) 同一種附註在全表壓倒性歸某個項目，這幾列是少數派（＝手滑選錯下拉）
#   (b) 方向與科目性質矛盾（收入列掛到費用科目）
FIXUPS = {
    ("2026/01/23", 9786, 0, "00332091606分行作業管理部"): {
        "category": "水電網路",
        "why": "摘要直接寫「電費」，同附註 48 列有 46 列歸水電網路 —— 原填「轉存」是手誤"},
    ("2025/05/01", 0, 428, "士源健保眷屬營業部"): {
        "category": "勞健保",
        "why": "健保退費沖回，同附註 9 列有 8 列歸勞健保 —— 原填「轉存」是手誤"},
    ("2025/05/01", 0, 428, "士源健保昱涵營業部"): {
        "category": "勞健保", "why": "同上（另一位眷屬）"},
    ("2025/05/08", 4515, 0, "管理費中山分行"): {
        "category": "辦公室管理費",
        "why": "同附註 20 列多數歸辦公室管理費 —— 原填「房租」是手誤（房租另有 47,015 的列）"},
    ("2025/08/08", 4515, 0, "管理費中山分行"): {
        "category": "辦公室管理費", "why": "同上"},
    ("2026/04/19", 47015, 0, "房租中山分行"): {
        "category": "房租",
        "why": "同附註 24 列有 23 列歸房租 —— 原填「辦公室管理費」是手誤（與上面兩列剛好互換）"},
    # 2025/06/06 同一天四筆轉給四位工作者共 747,469：附註各自對應的人在全表其他
    # 78 列都歸「請款單」。這是發款不是帳戶間轉存 —— 歸「轉存」會讓這筆成本
    # 整個從損益消失（transfer 不進損益），金額大，影響顯著。
    ("2025/06/06", 56000, 0, "黃聖鈞中山分行"): {
        "category": "請款單", "why": "發款給個人，同附註 15 列有 14 列歸請款單"},
    ("2025/06/06", 85750, 0, "鄭曉駿中山分行"): {
        "category": "請款單", "why": "發款給個人，同附註 13 列有 12 列歸請款單"},
    ("2025/06/06", 76852, 0, "蘇家弘中山分行"): {
        "category": "請款單", "why": "發款給個人，同附註 24 列有 23 列歸請款單"},
    ("2025/06/06", 528867, 0, "王士源中山分行"): {
        "category": "請款單", "why": "發款給個人，同附註 26 列有 25 列歸請款單"},
    # 以下三列是「收入掛到費用科目」—— 方向矛盾，不需要靠樣式統計也知道是錯的
    ("2024/07/05", 0, 219030, "********40014402822000"): {
        "category": "發票代開",
        "why": "219,030 是**收入**卻掛「獎金」（費用科目）；同附註樣式多數歸發票代開"},
    ("2026/04/27", 0, 8400, "********41818997中國信託"): {
        "category": "發票代開",
        "why": "8,400 是**收入**卻掛「專案外包」（費用科目）；同附註 26 列有 21 列歸發票代開"},
    ("2026/06/06", 0, 200, "11504分行作業管理部"): {
        "category": "其他收入",
        "why": "摘要「發票獎金」＝統一發票中獎，是其他收入；原填「獎金」是員工獎金（費用科目）"},
}

# ── 待確認清單（**不改資料**，只標 status='待確認' 並在備註寫原因）──
# 這些是「看起來怪但我沒有足夠證據判定對錯」的列。標記讓它們在 UI 篩得出來，
# 由 owner 逐筆看過再決定 —— 猜一個值寫進帳比留著問號危險得多。
REVIEW = {
    ("2025/04/29", 0, 77700, "現代婦女基金會南門分行"): "發票標 5,250 卻收到 77,700（可能是多張發票合併收款）",
    ("2025/08/12", 0, 40000, "人間魚詩社建成分行"): "發票 100,000 只收 40,000（可能是分期）",
    ("2025/01/15", 0, 111050, "現代婦女基金會南門分行"): "發票 144,900 只收 111,050（差 33,850）",
    ("2026/01/08", 0, 15735, "泓電自動化股份有中國信託"): "發票 31,500 只收 15,735（剛好一半，可能是分期）",
    ("2024/07/16", 0, 52470, "英特力生技股份有013065"): "發票 58,000 只收 52,470（差 5,530）",
    ("2024/10/07", 0, 162139, "********40332810中國信託"): "發票 157,500 卻收到 162,139（多 4,639）",
    ("2026/07/15", 0, 76115, "泛亞工程建設股第一銀行"): "發票 73,500 卻收到 76,115（多 2,615）",
    ("2025/11/28", 0, 14000, "Ｖｉｔｏ調光玉山銀行"): "發票 14,700 只收 14,000（差 700）",
    ("2025/07/01", 0, 16070, "財團法人中強光電兆豐銀行"): "發票 15,750 卻收到 16,070（多 320）",
    ("2024/11/14", 0, 3350, "鈞接案器材租費福港分行"): "發票 3,150 卻收到 3,350（多 200）",
    ("2024/07/05", 0, 4270, "黃聖鈞824000"): "收入 4,270 掛「專案雜支」（費用科目），但看不出正確歸屬",
    ("2024/04/25", 0, 38000, "********40202522822000"): "同附註樣式多數是發票代開，這列歸「專案」—— 兩者都說得通",
    ("2024/05/06", 0, 28802, "********06102812812000"): "收入掛「請款單」（應付結清），語意可疑但無法判定正確值",
    ("2025/12/31", 0, 140000, "財團法人公共電兆豐銀行"): "同附註多數是發票代開，這列歸「專案」—— 購片收入可能真的是專案",
    ("2026/04/30", 0, 48800, "財團法人文心藝術中國信託"): "客戶匯款卻歸「轉存」（不進損益），很可能漏計營收",
    ("2025/09/17", 4000, 0, "稅金會計中山分行"): "同附註多數是營業稅，這列歸「會計」—— 兩者都說得通",
    # 泛亞：VT26196653 在 2026/03/16（第 1258 列）已經收過款，這列不可能是同一張。
    # 依日期節奏推最可能是 BN31830502「0506空拍及照相」42,000（2026-05-11 開、尚未收款），
    # 但那是推論不是事實 —— 所以**清掉發票號**（不亂連），標待確認由 owner 指定。
    ("2026/06/15", 0, 41990, "泛亞工程建設股第一銀行"):
        "發票號 VT26196653 重複（2026/03/16 已收過）；推測應為 BN31830502「0506空拍及照相」"
        "42,000，待確認 —— 已先清掉發票號避免錯誤連結",
}
CLEAR_INVOICE = {("2026/06/15", 0, 41990, "泛亞工程建設股第一銀行")}


def build_note(row: dict) -> str:
    """附註為主體，其餘保真欄位各佔一行（Text 欄，UI 詳情面板會全文顯示）。"""
    lines = []
    if row.get("附註", "").strip():
        lines.append(row["附註"].strip())
    for label, col in (("應收發票", "應收發票"), ("請款總額", "請款"),
                       ("匯款客戶", "客戶匯款登錄"), ("備註", "備註")):
        v = row.get(col, "").strip()
        if v:
            lines.append(f"{label}：{v}")
    return "\n".join(lines)


def load(csv_path: str, years: set):
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8-sig", newline="")))
    h = find_header(rows, HEADER_MARK)
    hdr = [c.strip() for c in rows[h]]
    recs, bad_date, blank_summary, out_of_scope = [], [], [], 0
    fixed, flagged = [], []
    for ln, raw in enumerate(rows[h + 1:], start=h + 2):
        if not any(c.strip() for c in raw):
            continue
        row = {hdr[i]: (raw[i] if i < len(raw) else "") for i in range(len(hdr)) if hdr[i]}

        raw_date = (row.get("日期") or "").strip()
        d = parse_date(raw_date)
        if not d:
            bad_date.append((ln, raw_date, row.get("摘要", "")))
            continue
        if years and d.year not in years:
            out_of_scope += 1
            continue

        expense = _parse_money(row.get("支出", ""))
        deposit = _parse_money(row.get("存入", ""))

        summary = (row.get("摘要") or "").strip()
        if not summary:
            # 摘要是 NOT NULL 欄；帳要平所以不能跳列 → 用附註頂替（報告會列出）
            summary = (row.get("附註") or "").strip() or (row.get("項目") or "").strip() or "（無摘要）"
            blank_summary.append((ln, raw_date, summary))

        inv_no = (row.get("發票號碼") or "").strip()
        if inv_no.upper() in ("N/A", "NA"):
            inv_no = ""

        # 逐筆修正 / 待確認標記（見檔頭 FIXUPS、REVIEW）
        key = (raw_date, expense, deposit, (row.get("附註") or "").strip())
        status = (row.get("狀態") or "").strip()
        review_note = REVIEW.get(key)
        if review_note:
            status = "待確認"
            flagged.append((ln, raw_date, review_note))
        if key in CLEAR_INVOICE:
            inv_no = ""

        # 規則修正（2026-08-19 三方比對定案）：富邦把「調錢到貸款還款帳戶」的
        # 轉出（一銀貸款利息 5,015／合庫貸款 35,015／還本 51,015、65,389）在
        # 2026 起誤記「銀行利息」——真正的利息費用在貸款帳戶端認列，這邊是
        # 帳戶間轉存。2024-25 同類列本來就記「轉存」，此規則讓全表一致。
        category = (row.get("項目") or "").strip()
        note_txt = (row.get("附註") or "")
        if category == "銀行利息" and expense and "貸款" in note_txt:
            category = "轉存"
        fx = FIXUPS.get(key)
        if fx:
            fixed.append((ln, raw_date, category, fx["category"], fx["why"]))
            category = fx["category"]

        recs.append({
            "line": ln, "date": d,
            "data": dict(
                entry_date=d,
                expense=expense or None,
                deposit=deposit or None,
                summary=summary[:255],
                note=(build_note(row)
                      + (f"\n⚠ 待確認：{review_note}" if review_note else "")) or None,
                category=category or None,   # 🔴 引擎讀 category（項目欄＋上面規則修正）
                sub_item=(row.get("子項目") or "").strip() or None,
                status=status or None,
                project_label=(row.get("專案標籤") or "").strip() or None,
                invoice_number=inv_no or None,
                has_invoice=1 if inv_no else 0,
            ),
        })
    return recs, bad_date, blank_summary, out_of_scope, fixed, flagged


def dedup_key(d: dict):
    return (d["entry_date"].strftime("%Y-%m-%d") if d.get("entry_date") else "",
            d.get("expense") or 0, d.get("deposit") or 0,
            d.get("summary") or "", d.get("category") or "")


def report(recs, bad_date, blank_summary, out_of_scope, fixed, flagged, years, csv_path):
    print("=" * 68)
    print("來源:", csv_path, f"   年度篩選: {sorted(years) if years else '全部'}")
    print("=" * 68)
    print(f"可匯入列: {len(recs)}    年度範圍外跳過: {out_of_scope}")

    tot = defaultdict(lambda: [0, 0, 0])
    for r in recs:
        y = str(r["date"].year)
        tot[y][0] += 1
        tot[y][1] += r["data"].get("expense") or 0
        tot[y][2] += r["data"].get("deposit") or 0
    print(f"\n{'年度':<10}{'筆數':>6}{'支出':>16}{'存入':>16}")
    for y in sorted(tot):
        c, e, dp = tot[y]
        print(f"{y:<10}{c:>6}{e:>16,}{dp:>16,}")
    g = [sum(v[i] for v in tot.values()) for i in range(3)]
    print(f"{'合計':<10}{g[0]:>6}{g[1]:>16,}{g[2]:>16,}")

    both = [r for r in recs if (r["data"].get("expense") and r["data"].get("deposit"))]
    neither = [r for r in recs if not r["data"].get("expense") and not r["data"].get("deposit")]
    print(f"\n支出/存入 兩邊都填: {len(both)} 列（預期 0）    兩邊都空: {len(neither)} 列（預期 0）")
    for r in (both + neither)[:10]:
        print(f"      第 {r['line']} 列  {r['data'].get('summary','')}")

    linked = sum(1 for r in recs if r["data"].get("invoice_number"))
    print(f"發票號碼列: {linked}（寫入時會反查 crm_invoices 連 invoice_id）")

    if fixed:
        print(f"\n[修正] FIXUPS 套用了 {len(fixed)} 列（owner 核可的分類手誤）:")
        for ln, d, before, after, why in fixed:
            print(f"      第 {ln} 列 {d}  {before} → {after}")
            print(f"          {why}")
    if flagged:
        print(f"\n[待確認] 標記了 {len(flagged)} 列（狀態=待確認，資料原樣不動）:")
        for ln, d, why in flagged:
            print(f"      第 {ln} 列 {d}  {why}")

    if blank_summary:
        print(f"\n[補] 摘要空白 {len(blank_summary)} 列，以附註/項目頂替（NOT NULL 欄，帳要平不能跳列）:")
        for ln, raw, used in blank_summary:
            print(f"      第 {ln} 列  {raw}  → 摘要={used!r}")

    if bad_date:
        print(f"\n[!] 日期無法解析 {len(bad_date)} 列（**不匯入**，帳會不平，須先修來源）:")
        for ln, raw, s in bad_date:
            print(f"      第 {ln} 列  日期={raw!r}  {s[:24]}")
    return g


async def resolve_bank_account(s, name: str) -> str:
    """帳戶名 → bank_accounts.id。這份 Sheet 是單一帳戶（富邦）的流水帳，
    不掛帳戶的話現金流量表整批進「未掛帳戶」不列入 —— 匯入時就該掛好。"""
    rows = (await s.execute(text(
        "SELECT id, name FROM bank_accounts WHERE entity = :e AND name LIKE :n"
    ), {"e": ENTITY, "n": f"%{name}%"})).all()
    if len(rows) != 1:
        raise SystemExit(f"[ABORT] 帳戶「{name}」在目標庫比中 {len(rows)} 筆"
                         f"（{[r[1] for r in rows]}）—— 先在財務後台建好帳戶再匯。")
    print(f"銀行帳戶: {rows[0][1]} ({rows[0][0]})")
    return rows[0][0]


async def run(csv_path: str, prod: bool, apply: bool, years: set, bank_account: str):
    recs, bad_date, blank_summary, out_of_scope, fixed, flagged = load(csv_path, years)
    totals = report(recs, bad_date, blank_summary, out_of_scope, fixed, flagged,
                    years, csv_path)
    if bad_date:
        raise SystemExit("[ABORT] 有日期壞列 —— 匯入會讓帳不平，先修來源再跑。")

    url = resolve_db_url(prod)
    dbname = url.rsplit("/", 1)[-1]
    print(f"\n目標資料庫: {dbname}   模式: {'寫入 (--apply)' if apply else 'DRY-RUN（不寫入）'}")

    eng = create_async_engine(url, pool_pre_ping=True)
    Session = async_sessionmaker(eng, expire_on_commit=False)
    async with Session() as s:
        # F1 月結守衛：直接寫 DB 繞過 API，鎖帳檢查要自己做
        months = sorted({r["date"].strftime("%Y-%m") for r in recs})
        locked = (await s.execute(text(
            "SELECT month FROM finance_month_close "
            "WHERE entity = :e AND reopened_at IS NULL AND month = ANY(:m)"
        ), {"e": ENTITY, "m": months})).scalars().all()
        if locked:
            raise SystemExit(f"[ABORT] 這些月份已月結鎖帳: {locked} —— 先 reopen 再匯。")

        bank_id = await resolve_bank_account(s, bank_account) if bank_account else None
        if not bank_id:
            print("[!] 未指定 --bank-account —— 這批列不會進現金流量表（未掛帳戶）")

        # 發票連結：號碼 → invoice_id（號碼在發票表唯一，實測 393/393）
        inv_nos = {r["data"]["invoice_number"] for r in recs if r["data"].get("invoice_number")}
        inv_map, ambiguous = {}, set()
        if inv_nos:
            for num, iid in (await s.execute(
                    select(CrmInvoice.invoice_number, CrmInvoice.id)
                    .where(CrmInvoice.invoice_number.in_(inv_nos)))).all():
                if num in inv_map:
                    ambiguous.add(num)
                inv_map[num] = iid
            for num in ambiguous:
                del inv_map[num]
        unmatched = sorted(n for n in inv_nos if n not in inv_map and n not in ambiguous)
        print(f"發票連結: 對上 {len(inv_nos) - len(unmatched) - len(ambiguous)}/{len(inv_nos)}"
              f"   對不上: {unmatched or '無'}   號碼重複不敢連: {sorted(ambiguous) or '無'}")

        # 引擎科目對映檢查：category 不在 finance_category_map → 報表會落「未歸類」
        cats = {r["data"]["category"] for r in recs if r["data"].get("category")}
        # 正本在 routers/crm/_shared.cash_category_texts —— 那支的註解點名
        # 「匯入腳本」是它要收編的四處之一，這裡是最後一處手寫 SQL。
        from routers.crm._shared import cash_category_texts
        mapped = set(await cash_category_texts(s))
        unmapped_cats = sorted(cats - mapped)
        if unmapped_cats:
            print(f"[!] 這些項目不在 finance_category_map（報表會列未歸類）: {unmapped_cats}")

        # 多重集去重（見 docstring：完全相同的兩列是合法資料，set 會誤殺）
        existing = Counter()
        for row in (await s.execute(
                select(CrmCashEntry.entry_date, CrmCashEntry.expense, CrmCashEntry.deposit,
                       CrmCashEntry.summary, CrmCashEntry.category)
                .where(CrmCashEntry.entity == ENTITY))).all():
            existing[(row[0].strftime("%Y-%m-%d") if row[0] else "",
                      row[1] or 0, row[2] or 0, row[3] or "", row[4] or "")] += 1
        print(f"該庫既有母公司收支: {sum(existing.values())} 筆")

        new, dup = [], []
        for r in recs:
            k = dedup_key(r["data"])
            if existing[k] > 0:
                existing[k] -= 1
                dup.append(r)
            else:
                new.append(r)
        print(f"本次會新增 {len(new)} 筆；判定為既有而跳過 {len(dup)} 筆")

        if not apply:
            print("\nDRY-RUN 結束 —— 沒有寫入任何東西。確認上面數字無誤後加 --apply。")
            await eng.dispose()
            return
        if not new:
            print("沒有要新增的列。")
            await eng.dispose()
            return

        now = datetime.now(timezone.utc)
        for r in new:
            d = dict(r["data"])
            iid = inv_map.get(d.get("invoice_number") or "")
            s.add(CrmCashEntry(id=uuid.uuid4().hex, entity=ENTITY, invoice_id=iid,
                               bank_account_id=bank_id,
                               created_at=now, updated_at=now, **d))
        await s.commit()
        print(f"[OK] 已寫入 {len(new)} 筆到 {dbname}")

        # 寫後複驗：DB 匯入範圍的筆數與兩側合計要與來源一致。
        # 🔴 一定要照 bank_account_id 收斂 —— 多個銀行帳戶之後，只用 entity 過濾
        # 會把別的帳戶也加進來，驗算永遠對不上（實測 1,419 變 1,622 誤報失敗）。
        cond = [CrmCashEntry.entity == ENTITY]
        if bank_id:
            cond.append(CrmCashEntry.bank_account_id == bank_id)
        rows = (await s.execute(
            select(CrmCashEntry.entry_date, CrmCashEntry.expense, CrmCashEntry.deposit)
            .where(*cond))).all()
        scoped = [(e or 0, dp or 0) for dt, e, dp in rows
                  if dt and (not years or dt.year in years)]
        te, td = sum(x[0] for x in scoped), sum(x[1] for x in scoped)
        ok = (len(scoped) == totals[0] and te == totals[1] and td == totals[2])
        print(f"[驗] {dbname} 範圍內現有 {len(scoped)} 筆  支出 {te:,}  存入 {td:,}"
              f"（來源 {totals[0]} 筆 / {totals[1]:,} / {totals[2]:,}）"
              f" → {'全綠' if ok else '🔴 不一致！'}")
        linked_db = (await s.execute(text(
            "SELECT count(*) FROM crm_cash_entries WHERE entity = :e "
            "AND invoice_id IS NOT NULL AND (:b = '' OR bank_account_id = :b)"),
            {"e": ENTITY, "b": bank_id or ""})).scalar()
        print(f"[驗] invoice_id 已連結: {linked_db} 筆")
    await eng.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--years", default="", help="逗號分隔年度，如 2026；空=全部")
    ap.add_argument("--prod", action="store_true", help="對生產庫 mediaguard（預設 dev）")
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設 dry-run）")
    ap.add_argument("--bank-account", default="富邦",
                    help="掛帳銀行帳戶名（模糊比對，需唯一）；空字串=不掛（不進現金流量表）")
    a = ap.parse_args()
    yrs = {int(y) for y in a.years.split(",") if y.strip()}
    asyncio.run(run(a.csv, a.prod, a.apply, yrs, a.bank_account))
