# -*- coding: utf-8 -*-
"""私帳 Sheet ↔ 系統收支 全表對帳（owner 2026-08-27「檢查一下所有的收支對應，
原本的對應沒有填的在表單裡如實反應，資料有衝突比對不上的，告訴我我來判斷」）。

    .venv/Scripts/python.exe scripts/reconcile_ledger_sheet.py --csv 私帳.csv [--apply] [--prod]

🔴 預設 dry-run，而且**只同步「照抄得出來」的那一半**：
- Sheet 有值 → 系統照抄（類別／項目／子項目／卡別）
- Sheet 空白 → 系統也清空（owner 要「如實反應」，我先前推論補的分類要退掉）
- **兩邊都有值但不一樣 → 不動，列出來給 owner 判斷**（--apply 也不會覆蓋）
- 配不到的（Sheet 有帳上沒有、帳上有 Sheet 沒有）→ 一樣只列出來

owner 2026-08-27 後來要「完全同步」→ 加 `--sheet-wins --insert-missing`：
衝突也照 Sheet 覆蓋、Sheet 有帳上沒有的補進來。**還是不刪帳上的列**，
也不補「日期打錯的雙胞胎」（那是 Sheet 年份寫錯，補了就變同一筆記兩次）。

**分層配對**（消耗式，每層都要日期＋方向＋金額量級全中，只放寬辨識度）：
    L1 ＋摘要前 12 字 ＋帳戶   L2 ＋摘要   L3 ＋帳戶   L4 只有日期/方向/金額
L4 是「靠金額配的」，摘要對不上，一律當可疑列出來而不照抄。
🔴 方向（支出／存入／刷卡）一定要進鍵：轉帳的兩隻腳同日期同金額同摘要，
少了方向會左右腳配反（實測把「典藏專案轉匯」的收入腳配到儲蓄腳，
於是兩邊的類別看起來互換 —— 那是配對的錯，不是資料的錯）。
🔴 摘要要正規化：Sheet 的備註1 常是整段銀行明細（含換行／tab／全形空白），
帳上只存第一行，直接比會全軍覆沒（實測 473 筆假性「帳上找不到」）。

Sheet 欄（表頭在第 15 列）：
    0 日期 / 1 帳戶 / 2 支出 / 3 信用卡 / 4 存入 / 5 備註1(資訊) /
    6 備註2(其他資訊) / 7 類別 / 8 項目 / 9 子項目 / 10 標籤(複合鍵) /
    11 款別 / 12 專案標籤 / 13 專案代碼
"""
import asyncio
import csv
import io
import re
import sys
from collections import Counter, defaultdict
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.cash_taxonomy import join_category  # noqa: E402
from scripts._common import connect, db_target, print_dry_run_end, print_target  # noqa: E402

# 帳戶欄 → 系統帳戶。刷卡列掛卡片帳戶（卡別），其餘掛銀行帳戶。
# 🔴 只認這兩個**確定**的對映。刷卡列的帳戶欄還有「收入」39 筆、空白 86 筆，
# 那是哪張卡我不知道 —— 猜一個掛上去等於把 12 萬的卡費算到錯的卡，寧可列出來問。
CARD_ACCOUNTS = {"台新": "台新信用卡", "支出": "富邦信用卡"}
BANK_ALIASES = {"支出": "富邦-支出戶", "收入": "富邦-收入戶", "儲蓄": "富邦-儲蓄戶",
                "台新": "台新（個人）", "永豐": "永豐（個人）"}


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
        return datetime(int(p[0]), int(p[1]), int(p[2])).strftime("%Y-%m-%d")
    except ValueError:
        return None


def load_sheet(csv_path, header_row=15):
    """回傳 (可用列, 日期壞掉的列)。header_row 是 1-based 的表頭列號。"""
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8")))
    out, bad = [], []
    for n, r in enumerate(rows[header_row:], start=header_row + 1):
        r = (list(r) + [""] * 16)[:16]
        raw = (r[0] or "").strip()
        if not raw:
            continue
        d = _iso(raw)
        if not d or not ("2000-01-01" <= d <= "2099-12-31"):
            if any(x.strip() for x in r[2:6]):
                bad.append({"row": n, "raw": raw, "summary": r[5].strip()[:30]})
            continue
        card, out_, in_ = _money(r[3]), _money(r[2]), _money(r[4])
        out.append({
            "row": n, "date": d, "acct": r[1].strip(),
            "card": card, "out": out_, "in": in_,
            "amt": card or out_ or in_,
            "summary": (r[5].strip() or "（無摘要）")[:250],
            "memo": r[6].strip(),
            "book": r[7].strip(), "item": r[8].strip(), "sub": r[9].strip(),
        })
    return out, bad


def _norm(summary):
    """摘要正規化：只取第一行、收掉 tab/全形空白/重複空白。"""
    t = (summary or "").replace("　", " ").split("\n")[0]
    return re.sub(r"\s+", " ", t).strip()


def _dir_of(sheet_row=None, db_row=None):
    """方向要跟 amt 取的是同一欄 —— 兩者不同步就永遠配不到。
    🔴 實測踩過：2025-06-01 那列同時填了 支出 15,000 和 存入 50,000，amt 取 15,000（支出）
    但方向判成 in，於是配不到帳上那筆、被當「Sheet 有帳上沒有」補了一筆重複列進去。
    現在 card > out > in 跟 amt 的優先序一致，兩邊填的列另外列成異常。"""
    if sheet_row is not None:
        if sheet_row["card"]:
            return "card"
        # 三欄都空白的列（Sheet 有 28 筆只寫了備註）也要落在 "out"，
        # 跟下面 db_dir 的預設一致 —— 否則那批補進去之後反而配不回來
        return "in" if (sheet_row["in"] and not sheet_row["out"]) else "out"
    return "in" if (db_row["deposit"] and not db_row["expense"]) else "out"


def _keys(day, direction, amt, summary, acct):
    """L1→L4，愈後面愈鬆（日期/方向/金額三者永遠要中）。"""
    n = _norm(summary)[:12]
    a = abs(int(amt or 0))
    return [(1, day, direction, a, n, acct), (2, day, direction, a, n),
            (3, day, direction, a, acct), (4, day, direction, a)]


def _fmt(x):
    return f"{x['date']} {x['acct'] or '—':<3} {x['amt']:>9,} {x['summary'][:26]}"


async def run(csv_path, apply, prod, limit, report_csv=None,
              sheet_wins=False, insert_missing=False):
    dbname, dsn = db_target(prod)
    print_target(dbname, apply)
    sheet, bad_dates = load_sheet(csv_path)
    # 一列同時填了支出與存入 → 那是兩筆錢流擠在一列，系統只會認一邊。不猜，列出來。
    two_sided = [s for s in sheet if sum(1 for k in ("card", "out", "in") if s[k]) > 1]
    print(f"Sheet 可用列 {len(sheet)}；日期壞掉的 {len(bad_dates)}；"
          f"一列填兩個金額欄的 {len(two_sided)}")

    c = await connect(dsn)
    try:
        accts = {r["name"]: dict(r) for r in await c.fetch(
            "SELECT id, name, acct_kind FROM bank_accounts WHERE entity='mine'")}
        by_id = {a["id"]: a for a in accts.values()}
        db = [dict(r) for r in await c.fetch(
            "SELECT id, to_char(entry_date,'YYYY-MM-DD') d, summary, expense, deposit,"
            "       category, item, sub_item, status, bank_account_id"
            " FROM crm_cash_entries WHERE entity='mine'")]
        print(f"系統私帳列 {len(db)}")

        def db_dir(x):
            if x["status"] == "card":
                return "card"
            return "in" if (x["deposit"] and not x["expense"]) else "out"

        def db_acct(x):
            return by_id.get(x["bank_account_id"] or "", {}).get("name", "")

        def sheet_acct(s):
            return ((CARD_ACCOUNTS if s["card"] else BANK_ALIASES)
                    .get(s["acct"], s["acct"]))

        # 分層配對：先用最嚴的鍵配一輪，剩下的才降一級（消耗式，配走就不再參與）
        pools = [defaultdict(list) for _ in range(4)]
        for x in db:
            ks = _keys(x["d"], db_dir(x), x["expense"] or x["deposit"], x["summary"], db_acct(x))
            for lvl, k in enumerate(ks):
                pools[lvl][k].append(x)
        taken, pairs, loose, rest = set(), [], [], list(sheet)
        for lvl in range(4):
            still = []
            for s in rest:
                k = _keys(s["date"], _dir_of(sheet_row=s), s["amt"], s["summary"], sheet_acct(s))[lvl]
                cand = None
                while pools[lvl].get(k):
                    x = pools[lvl][k].pop()
                    if x["id"] not in taken:
                        cand = x
                        break
                if cand is None:
                    still.append(s)
                    continue
                taken.add(cand["id"])
                pairs.append((s, cand))
                # Sheet 那格本來就沒摘要（備註1 空白）→ 摘要對不上是必然，不算可疑；
                # 帳上那側的「生活」「被動收入」是更早一次匯入拿類別當摘要留下的
                if (lvl == 3 and s["summary"] != "（無摘要）"
                        and _norm(s["summary"])[:12] != _norm(cand["summary"])[:12]):
                    loose.append((s, cand))
            rest = still
        no_db = rest
        no_sheet = [x for x in db if x["id"] not in taken]

        # 日期打錯的雙胞胎：Sheet 一列、帳上一列，方向/金額/摘要全同，只有日期兜不攏
        # （實測 Sheet 那側 2014-03-14 vs 帳上 2024-03-14、0114/12/21 vs 2025-12-21）。
        # 這種**不能**照「Sheet 有帳上沒有」補進去 —— 補了就是同一筆記兩次，
        # 而且新那筆還落在錯的年份。挑出來讓 owner 去改 Sheet。
        # 鍵用「月日」而不是摘要：這批的摘要本來就對不起來（Sheet 那格空白、
        # 帳上是更早匯入拿類別當摘要），同月同日＋同方向＋同金額已經夠specific。
        idx = defaultdict(list)
        for x in no_sheet:
            idx[(db_dir(x), abs(x["expense"] or x["deposit"] or 0), x["d"][5:])].append(x)
        typo, kept = [], []
        for s in no_db:
            k = (_dir_of(sheet_row=s), abs(s["amt"]), s["date"][5:])
            if s["amt"] and idx.get(k):
                typo.append((s, idx[k].pop()))
            else:
                kept.append(s)
        no_db = kept
        no_sheet = [x for v in idx.values() for x in v]

        # ── 分類差異 ──
        loose_ids = {e["id"] for _s, e in loose}
        fill, clear, conflict, card_fix, card_unknown = [], [], [], [], []
        for s, e in pairs:
            if e["id"] in loose_ids:
                continue
            want = {"category": join_category(s["book"], s["item"]),
                    "item": s["item"], "sub_item": s["sub"]}
            got = {"category": e["category"] or "", "item": e["item"] or "",
                   "sub_item": e["sub_item"] or ""}
            if want != got:
                if all(not got[f] or not want[f] or got[f] == want[f] for f in want):
                    (fill if any(want.values()) else clear).append((s, e, want, got))
                else:
                    conflict.append((s, e, want, got))
            # 卡別
            if s["card"]:
                name = CARD_ACCOUNTS.get(s["acct"], "")
                cur = by_id.get(e["bank_account_id"] or "", {}).get("name", "")
                if not name:
                    card_unknown.append((s, e))
                elif cur != name:
                    card_fix.append((s, e, name, cur))

        print(f"\n── 對帳結果 ──")
        print(f"配對成功            {len(pairs):>5}")
        print(f"Sheet 有、帳上沒有  {len(no_db):>5}")
        print(f"帳上有、Sheet 沒有  {len(no_sheet):>5}")
        print(f"照 Sheet 補值       {len(fill):>5}（帳上空白或一致，Sheet 有值）")
        print(f"照 Sheet 清空       {len(clear):>5}（Sheet 空白，帳上有值＝我先前推論補的）")
        print(f"🔴 衝突（要你判斷） {len(conflict):>5}（兩邊都有值且不同）")
        print(f"卡別要改            {len(card_fix):>5}")
        print(f"🔴 卡別不明（要你說）{len(card_unknown):>4}（刷卡列但帳戶欄不是台新/支出）")
        print(f"🟡 靠金額配的       {len(loose):>5}（摘要對不上，分類不照抄）")
        print(f"🔴 日期打錯的雙胞胎 {len(typo):>5}（同金額同摘要、年份對不上，不補不刪）")
        if bad_dates:
            print(f"🔴 日期壞掉         {len(bad_dates):>5}")

        def dump(title, items, fmt):
            if not items:
                return
            print(f"\n── {title}（{len(items)}）──")
            for x in items[:limit]:
                print("  " + fmt(x))
            if len(items) > limit:
                print(f"  …另外 {len(items) - limit} 筆（--limit 調整）")

        dump("🔴 衝突：兩邊都有值且不同", conflict,
             lambda t: f"{_fmt(t[0])}\n      Sheet: {t[2]['category'] or '（空）'}／"
                       f"{t[2]['sub_item'] or '—'}   帳上: {t[3]['category'] or '（空）'}／"
                       f"{t[3]['sub_item'] or '—'}")
        dump("🟡 靠金額配上的（摘要對不起來，分類不照抄）", loose,
             lambda t: f"{_fmt(t[0])}\n      帳上摘要: {_norm(t[1]['summary'])[:30]}")
        dump("🔴 刷卡列但認不出是哪張卡", card_unknown, lambda t: _fmt(t[0]))
        dump("🔴 日期打錯的雙胞胎（Sheet 年份寫錯）", typo,
             lambda t: f"Sheet {t[0]['date']} vs 帳上 {t[1]['d']}  "
                       f"{t[0]['amt']:>9,} {_norm(t[0]['summary'])[:26]}")
        dump("🔴 Sheet 有、帳上找不到", no_db, _fmt)
        dump("🔴 帳上有、Sheet 找不到", no_sheet,
             lambda e: f"{e['d']} {(e['expense'] or e['deposit'] or 0):>9,} "
                       f"{(e['summary'] or '')[:30]}  [{e['category'] or '（空）'}]")
        dump("🔴 一列填了兩個金額欄（系統只認一邊）", two_sided, _fmt)
        dump("🔴 日期壞掉（Sheet）", bad_dates,
             lambda b: f"第 {b['row']} 列 日期「{b['raw']}」 {b['summary']}")

        if clear:
            print("\n清空的分佈（帳上目前的值）：",
                  Counter((g["category"] or "（空）") for _s, _e, _w, g in clear).most_common(8))
        if fill:
            print("補值的分佈（Sheet 的值）：",
                  Counter((w["category"] or "（空）") for _s, _e, w, _g in fill).most_common(8))
        if conflict:
            print("\n衝突樣態（Sheet → 帳上，前 12 種）：")
            for (a, bb), n in Counter(
                    ((w["category"] or "（空）") + "／" + (w["sub_item"] or "—"),
                     (g["category"] or "（空）") + "／" + (g["sub_item"] or "—"))
                    for _s, _e, w, g in conflict).most_common(12):
                print(f"    {n:>4}  {a:<22} → {bb}")
        if card_fix:
            print("\n卡別調整分佈：",
                  Counter(f"{cur or '（未掛）'}→{name}" for _s, _e, name, cur in card_fix).most_common())

        if report_csv:
            # 要 owner 判斷的三類寫成一份 CSV（BOM，Excel 開中文不亂碼）
            with io.open(report_csv, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["類型", "Sheet列", "日期", "帳戶", "金額", "摘要",
                            "Sheet的分類", "系統的分類", "怎麼處理"])
                for s, _e, want, got in conflict:
                    w.writerow(["分類衝突", s["row"], s["date"], s["acct"], s["amt"],
                                _norm(s["summary"]),
                                (want["category"] or "") + "／" + (want["sub_item"] or ""),
                                (got["category"] or "") + "／" + (got["sub_item"] or ""),
                                "兩邊都有值且不同 —— 系統目前保留自己的值"])
                for s, e in card_unknown:
                    w.writerow(["卡別不明", s["row"], s["date"], s["acct"], s["amt"],
                                _norm(s["summary"]), "", db_acct(e) or "（未掛）",
                                "刷卡列但帳戶欄不是台新/支出，要你指定哪張卡"])
                for s in no_db:
                    w.writerow(["Sheet有帳上沒有", s["row"], s["date"], s["acct"], s["amt"],
                                _norm(s["summary"]),
                                join_category(s["book"], s["item"]), "", "沒進系統"])
                for e in no_sheet:
                    w.writerow(["帳上有Sheet沒有", "", e["d"], db_acct(e),
                                e["expense"] or e["deposit"] or 0, _norm(e["summary"]),
                                "", e["category"] or "", "Sheet 裡找不到對應列"])
                for s, e in typo:
                    w.writerow(["日期打錯", s["row"], f"Sheet {s['date']} / 帳上 {e['d']}",
                                s["acct"], s["amt"], _norm(s["summary"]),
                                join_category(s["book"], s["item"]), e["category"] or "",
                                "同一筆但年份對不上 —— 請改 Sheet 的日期"])
                for bd in bad_dates:
                    w.writerow(["日期壞掉", bd["row"], bd["raw"], "", "", bd["summary"],
                                "", "", "日期解析不出來，整列被跳過"])
            print(f"\n要你判斷的清單已寫到：{report_csv}")

        if not apply:
            print_dry_run_end()
            return

        n = 0
        for s, e, want, _got in fill + clear + (conflict if sheet_wins else []):
            await c.execute(
                "UPDATE crm_cash_entries SET category=$1, item=$2, sub_item=$3,"
                " updated_at=now() WHERE id=$4",
                want["category"] or None, want["item"] or None, want["sub_item"] or None, e["id"])
            n += 1
        m = 0
        for s, e, name, _cur in card_fix:
            a = accts.get(name)
            if not a:
                print(f"  🔴 沒有帳戶「{name}」—— 跳過（先建帳戶再跑一次）")
                continue
            await c.execute(
                "UPDATE crm_cash_entries SET bank_account_id=$1, status='card',"
                " updated_at=now() WHERE id=$2", a["id"], e["id"])
            m += 1
        k = 0
        if insert_missing and no_db:
            now = datetime.now(timezone.utc)
            for s in no_db:
                if s in two_sided:
                    print(f"  ⚠️ 第 {s['row']} 列同時有支出與存入 —— 不補（要你先拆成兩列）")
                    continue
                if s["card"]:
                    acct_id = (accts.get(CARD_ACCOUNTS.get(s["acct"], "")) or {}).get("id")
                    status, expense, deposit = "card", s["card"], None
                else:
                    acct_id = (accts.get(BANK_ALIASES.get(s["acct"], s["acct"])) or {}).get("id")
                    status, expense, deposit = None, (s["out"] or None), (s["in"] or None)
                await c.execute(
                    "INSERT INTO crm_cash_entries (id, entity, entry_date, expense, deposit,"
                    "        summary, bank_memo, category, item, sub_item, status,"
                    "        bank_account_id, has_invoice, created_at, updated_at)"
                    " VALUES ($1,'mine',$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,0,$12,$12)",
                    uuid.uuid4().hex, datetime.strptime(s["date"], "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc),
                    expense, deposit, s["summary"], s["memo"] or None,
                    join_category(s["book"], s["item"]) or None,
                    s["item"] or None, s["sub"] or None, status, acct_id, now)
                k += 1
        print(f"\n已更新分類 {n} 筆、卡別 {m} 筆、補進 {k} 筆"
              + ("" if sheet_wins else f"；衝突 {len(conflict)} 筆**沒動**（等你判斷）")
              + (f"；日期打錯的雙胞胎 {len(typo)} 筆沒補沒刪" if typo else ""))
    finally:
        await c.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--limit", type=int, default=40, help="每一類最多列幾筆")
    ap.add_argument("--prod", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report-csv", help="把要你判斷的清單寫成 CSV")
    ap.add_argument("--sheet-wins", action="store_true",
                    help="衝突也照 Sheet 覆蓋（owner 要「完全同步」時才開）")
    ap.add_argument("--insert-missing", action="store_true",
                    help="Sheet 有帳上沒有的列補進系統（日期打錯的雙胞胎不補）")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(a.csv, a.apply, a.prod, a.limit, a.report_csv,
                    a.sheet_wins, a.insert_missing))
