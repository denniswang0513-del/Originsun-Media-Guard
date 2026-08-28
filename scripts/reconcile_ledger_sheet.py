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
from core.cash_taxonomy import join_category, mirror_from_path  # noqa: E402
from scripts._common import (connect, db_target, load_taxonomy_tree,  # noqa: E402
                            node_for_path, print_dry_run_end, print_target)

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


# Sheet 上打錯的日期（owner 2026-08-27「依你建議調整」）。我沒有那份 Google Sheet 的
# 寫入權，所以更正記在這裡 —— 有據可查、重跑結果一致。owner 什麼時候把 Sheet 改好，
# 這幾條就可以刪掉（刪掉後結果不變，因為那時 Sheet 自己就是對的）。
#   672/673：年份打成 2014（帳上 2024-03-14，個人_生活／信用卡回饋）
#   3902：民國西元混寫 0114/12/21（帳上 2025-12-21，公司_器材 13,954）
SHEET_DATE_FIXES = {672: "2024-03-14", 673: "2024-03-14", 3902: "2025-12-21"}

# owner 2026-08-28 對「款別對不上的五筆」的判定（分類樹階段四回填卡住的那 5 筆）。
# 同 SHEET_DATE_FIXES：我沒有那份 Sheet 的寫入權，更正記在這裡 —— 有據可查、重跑
# 結果一致。owner 什麼時候把 Sheet 改好，這幾條就可以刪掉（刪掉後結果不變）。
#   555  預收款 26,000 → 當成一般專案收款（owner 拍板；2024 營收因此 +26,000）
#   671  華南回捐公司 3,000 是**支出**，款別「已收帳款」誤填 → 清掉
#   1730 典藏顧問 36,914 誤填在「薪水」（其他 10 筆同名同額都在 公司_專案）
#   3232 快樂學游泳首期款 153,535 項目漏填（專案連結已在）
#   3561 典藏11月 36,914 項目漏填（同一天另兩筆已是 公司_專案）
# owner 2026-08-28（第二輪，上生產時 --only-rows 擋下來的那一筆）：
#   4357 台新刷卡 130 —— Sheet 寫「個人_贈予」，**帳上的個人_生活／交通 才是對的**。
#        這條是 Sheet 錯、系統對；不記在這裡的話每次對帳都會再跳出來一次。
SHEET_FIXES = {
    555:  {"item": "專案"},
    671:  {"fund": ""},
    1730: {"item": "專案"},
    3232: {"item": "專案"},
    3561: {"item": "專案"},
    4357: {"item": "生活", "sub": "交通"},
}


def load_sheet(csv_path, header_row=15):
    """回傳 (可用列, 日期壞掉的列)。header_row 是 1-based 的表頭列號。"""
    rows = list(csv.reader(io.open(csv_path, encoding="utf-8")))
    out, bad = [], []
    for n, r in enumerate(rows[header_row:], start=header_row + 1):
        r = (list(r) + [""] * 16)[:16]
        raw = (r[0] or "").strip()
        if not raw:
            continue
        d = SHEET_DATE_FIXES.get(n) or _iso(raw)
        if not d or not ("2000-01-01" <= d <= "2099-12-31"):
            if any(x.strip() for x in r[2:6]):
                bad.append({"row": n, "raw": raw, "summary": r[5].strip()[:30]})
            continue
        card, out_, in_ = _money(r[3]), _money(r[2]), _money(r[4])
        base = {
            "row": n, "date": d, "acct": r[1].strip(),
            "card": card, "out": out_, "in": in_,
            "amt": card or out_ or in_,
            "summary": (r[5].strip() or "（無摘要）")[:250],
            "memo": r[6].strip(),
            "book": r[7].strip(), "item": r[8].strip(), "sub": r[9].strip(),
            # 款別（第 11 欄）與專案標籤（第 12 欄）—— Sheet 只有三個分類欄，
            # 要長第四、五層時 owner 把值溢出到這兩欄（`家用▸變動支出▸醫療保健`
            # 底下的「乳癌治療」在款別、就醫地點在專案標籤）。分類樹的回填吃它們。
            "fund": r[11].strip(), "plabel": r[12].strip(),
        }
        base.update(SHEET_FIXES.get(n, {}))
        # 一列同時填了支出與存入 → **拆成兩腳**（owner 2026-08-27「拆成兩列」）。
        # 一列裝兩筆錢流，配對只認得到一邊，另一邊會永遠掛在「帳上有 Sheet 沒有」。
        if out_ and in_:
            a, b = dict(base), dict(base)
            a.update({"in": 0, "amt": out_, "leg": "out"})
            b.update({"out": 0, "amt": in_, "leg": "in"})
            out.extend([a, b])
            continue
        out.append(base)
    return out, bad


# 就醫地點：Sheet 把它們塞在「專案標籤」欄（專案索引那份清單裡混著它們）。
# 對應樹上 `家用▸變動支出▸醫療保健▸乳癌治療` 底下的第五層 —— 清單直接從種子
# 正本推，不在這裡再抄一份（抄了就會跟 CANONICAL 漂）。
# ⚠️ 侷限：owner 用後台樹編輯器新加的醫院這裡看不到 —— 要跑本腳本前先把
# CANONICAL 補上（種子冪等，新節點會自動長出來）。
from db.seed_cash_taxonomy import CANONICAL as _CANON  # noqa: E402
HOSPITALS = tuple(_CANON["家用"]["變動支出"]["醫療保健"]["乳癌治療"])


def sheet_path(s):
    """Sheet 這一列的**完整**分類路徑（含溢出到款別／專案標籤的第四、五層）。

    🔴 這是「Sheet 說這一列是什麼分類」的唯一定義，本檔與 backfill_cash_depth
    共用。少了它，兩支會打架：本檔只看 H/I/J 就會把 backfill 補進 sub_item 的
    610 筆款別判成「Sheet 空白、帳上有值」，`--apply` 一跑就整批抹掉
    （2026-08-27 實測，差一步就發生）。
    """
    p = [x for x in (s["book"], s["item"], s["sub"]) if x]
    if s["fund"]:
        p.append(s["fund"])
    if s["plabel"] in HOSPITALS:
        p.append(s["plabel"])
    return p


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


# ── 帳上那側的判讀（reconcile 與 backfill_cash_depth 共用，別各抄一份）──────
def db_dir(x):
    """帳上一列的方向。要跟 `_keys` 的金額取同一欄（規則見 _dir_of 的血淚註解）。"""
    if x["status"] == "card":
        return "card"
    return "in" if (x["deposit"] and not x["expense"]) else "out"


def make_db_acct(by_id):
    """帳上一列 → 帳戶名（by_id＝bank_accounts 的 id 索引，由呼叫端撈）。"""
    return lambda x: by_id.get(x["bank_account_id"] or "", {}).get("name", "")


def sheet_acct(s):
    """Sheet 一列 → 系統帳戶名（刷卡列走卡別對照、其餘走銀行別名）。"""
    return ((CARD_ACCOUNTS if s["card"] else BANK_ALIASES)
            .get(s["acct"], s["acct"]))


def match_sheet_to_db(sheet, db, db_dir, db_acct, sheet_acct):
    """Sheet 列 ↔ 系統列的分層配對。回 `(pairs, loose, no_db, no_sheet, typo)`。

    抽出來是為了讓 `backfill_cash_depth.py`（分類樹第四、五層回填）**用同一份
    規則**。這裡每一條註解都是實測踩出來的（方向要跟金額取同一欄、摘要要正規化、
    消耗式配對、日期打錯的雙胞胎不能當「Sheet 有帳上沒有」補進去），抄第二份的
    成本遠高於多一個參數。

    `db_dir` / `db_acct` / `sheet_acct` 由呼叫端給 —— 它們要讀帳戶對照表，
    那是呼叫端的東西。
    """
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
            # 🔴 同一個鍵有多個候選＝真雙胞胎（同日同方向同額同摘要）。隨便挑
            # 一隻的話，分類不同的那種就會有一半被判成「衝突」，而且**跨次不穩定**
            # （DB 回傳順序一變就對調）。同分時優先挑「分類也對得上」的那一隻 ——
            # 硬條件全中還多中一個，本來就該優先。
            avail = [x for x in pools[lvl].get(k, []) if x["id"] not in taken]
            if not avail:
                still.append(s)
                continue
            _wc, _wi, _ws = mirror_from_path(sheet_path(s))
            cand = max(avail, key=lambda x: (((x["category"] or "") == _wc) * 2
                                             + ((x["sub_item"] or "") == _ws)))
            pools[lvl][k].remove(cand)
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
    return pairs, loose, no_db, no_sheet, typo


async def run(csv_path, apply, prod, limit, report_csv=None,
              sheet_wins=False, insert_missing=False, unknown_card=None,
              only_rows=None):
    dbname, dsn = db_target(prod)
    print_target(dbname, apply)
    sheet, bad_dates = load_sheet(csv_path)
    # 一列同時填了支出與存入 → 那是兩筆錢流擠在一列，系統只會認一邊。不猜，列出來。
    two_sided = [s for s in sheet if s.get("leg")]
    print(f"Sheet 可用列 {len(sheet)}（含拆腳）；日期壞掉的 {len(bad_dates)}；"
          f"一列填兩個金額欄而被拆成兩腳的 {len(two_sided) // 2}")

    c = await connect(dsn)
    try:
        accts = {r["name"]: dict(r) for r in await c.fetch(
            "SELECT id, name, acct_kind FROM bank_accounts WHERE entity='mine'")}
        by_id = {a["id"]: a for a in accts.values()}
        # 🔴 一定要 ORDER BY：沒有排序時 Postgres 回的實體順序會隨更新改變，
        # 而 L4 那層（只靠日期/方向/金額）遇到「同日同額無摘要」的雙胞胎時
        # 是任配一隻 —— 於是重跑一次那兩筆就對調（2026-08-27 實測踩到）。
        db = [dict(r) for r in await c.fetch(
            "SELECT id, to_char(entry_date,'YYYY-MM-DD') d, summary, expense, deposit,"
            "       category, item, sub_item, status, bank_account_id"
            " FROM crm_cash_entries WHERE entity='mine'"
            " ORDER BY entry_date, id")]
        print(f"系統私帳列 {len(db)}")

        db_acct = make_db_acct(by_id)

        pairs, loose, no_db, no_sheet, typo = match_sheet_to_db(
            sheet, db, db_dir, db_acct, sheet_acct)

        # ── 分類差異 ──
        loose_ids = {e["id"] for _s, e in loose}
        fill, clear, conflict, card_fix, card_unknown = [], [], [], [], []
        for s, e in pairs:
            if e["id"] in loose_ids:
                continue
            # 分類三欄＝Sheet 完整路徑的前三層鏡射（規則正本
            # core.cash_taxonomy.mirror_from_path，跟系統寫入端同一份）
            _cat, _itm, _sub = mirror_from_path(sheet_path(s))
            want = {"category": _cat, "item": _itm, "sub_item": _sub}
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
                    # owner 指定過就跟著掛（2026-08-27：125 筆全部 → 富邦信用卡）
                    if unknown_card and cur != unknown_card:
                        card_fix.append((s, e, unknown_card, cur))
                    elif not unknown_card:
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
        dump("🟡 一列填兩個金額欄（已拆成兩腳處理）", two_sided,
             lambda t: _fmt(t) + f"  [{t['leg']} 腳]")
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

        # 🔴 `--only-rows`：衝突是 owner 一列一列判的，`--sheet-wins` 卻是整批覆蓋。
        # 生產與 dev 的漂移不會一樣（實測 2026-08-28：生產多一列 Sheet 沒判過的），
        # 開了 sheet-wins 就會把「他沒看過的那幾列」順手一起翻掉。給定 only_rows
        # 時只套用指名的 Sheet 列號，其餘衝突照樣列出來等他判。
        wins = conflict if sheet_wins else []
        if only_rows is not None:
            skipped = [t for t in wins if t[0]["row"] not in only_rows]
            wins = [t for t in wins if t[0]["row"] in only_rows]
            if skipped:
                print("")
                print(f"--only-rows：另有 {len(skipped)} 筆衝突**不在指名清單**，沒有寫入：")
                for s_, e_, w_, g_ in skipped[:limit or 20]:
                    print(f"  Sheet r{s_['row']} {s_['date']} {s_['amt']:>9,} "
                          f"{_norm(s_['summary'])[:20]}"
                          f"   Sheet: {w_['category'] or '（空）'}／{w_['sub_item'] or '—'}"
                          f"   帳上: {g_['category'] or '（空）'}／{g_['sub_item'] or '—'}")

        if not apply:
            print_dry_run_end()
            return

        # 🔴 改分類就要**同時**改 taxonomy_node_id：只寫那三欄會留下「有類別、但不
        # 在樹上」的孤兒 —— 樹狀篩選看不到它、後台的影響筆數也算不到它。樹上沒有
        # 那條路徑就整列不寫並列出來（不自己造節點，理由同 backfill_cash_depth）。
        child, _paths = await load_taxonomy_tree(c, "mine")
        n, no_node = 0, []
        for s, e, want, _got in fill + clear + wins:
            names = sheet_path(s)
            nid = node_for_path(child, names)
            if names and not nid:
                no_node.append((s, " ▸ ".join(names)))
                continue
            await c.execute(
                "UPDATE crm_cash_entries SET category=$1, item=$2, sub_item=$3,"
                " taxonomy_node_id=$4, updated_at=now() WHERE id=$5",
                want["category"] or None, want["item"] or None, want["sub_item"] or None,
                nid or None, e["id"])
            n += 1
        if no_node:
            print("")
            print(f"🔴 樹上沒有這些分類路徑，那 {len(no_node)} 列**沒有寫入**：")
            for s, pth in no_node[:limit]:
                print(f"  {pth}   [Sheet r{s['row']} {s['date']} {_norm(s['summary'])[:20]}]")
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
    ap.add_argument("--unknown-card",
                    help="卡別不明那批全掛到這張卡（owner 指定後才用，別自己猜）")
    ap.add_argument("--only-rows",
                    help="只套用這些 Sheet 列號的衝突（逗號分隔）—— 搭 --sheet-wins 用，"
                         "避免整批覆蓋到 owner 沒判過的列")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    _only = ({int(x) for x in a.only_rows.split(",") if x.strip()}
             if a.only_rows else None)
    asyncio.run(run(a.csv, a.apply, a.prod, a.limit, a.report_csv,
                    a.sheet_wins, a.insert_missing, a.unknown_card, _only))
