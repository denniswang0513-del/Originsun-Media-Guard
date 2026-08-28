# -*- coding: utf-8 -*-
"""匯入腳本共用底座 —— dev/prod 庫切換、表頭定位、金額/日期正規化、CLI 骨架。

為什麼收在這裡：這幾段本來各躺在 import_cashbook / import_invoices /
import_petty_cash 裡，三份**一字不差**，而三個 docstring 各自寫「與 X 同一套
寫法，不得漂移」指向不同的檔 —— 繞成一圈，等於沒有正本。真正需要防漂移的東西
就該只有一份可以改。

2026-08-25 第二輪（/simplify 連兩輪點名）：私帳五支匯入腳本
（import_my_ledger / _projects / _equipment / _snapshots / backfill_my_ledger_detail）
各自帶著一份 money()、一份日期解析、一份表頭 col() closure、一份
「dbname/dsn ＋ 目標資料庫 banner ＋ argparse ＋ asyncio.run」樣板 —— 五份金額
解析已經漂出四種語意（見 money() 的旗標說明），正是漂移發生後才被抓到的證據。
差異一律用**明示旗標**保留，不靜默統一：呼叫端寫得出 `none_on_bad=True`，
下一個人才看得懂那是刻意的。
"""
import argparse
import asyncio
import re
import sys


def resolve_db_url(prod: bool) -> str:
    """dev/prod 庫切換的單一正本。

    🔴 只換庫名、不動整串 —— memory 有一筆把 `database_url` 整串取代、害 dev
    連進生產庫的前科。dry-run 報告器與實際寫入必須走同一支，否則「看的是 dev、
    寫的是 prod」這種事會靜默發生。
    """
    from config import load_settings
    url = load_settings().get("database_url", "")
    return (url.replace("/mediaguard_dev", "/mediaguard") if prod
            else (url if url.endswith("_dev") else url + "_dev"))


def db_target(prod: bool) -> tuple:
    """(顯示用庫名, asyncpg 用 DSN) —— 五支腳本開頭一字不差的兩行。

    庫名只給 banner 印，DSN 是把 SQLAlchemy 的 `postgresql+asyncpg://` 去掉方言
    （asyncpg.connect 不吃 `+asyncpg`）。兩者同源於 resolve_db_url，才不會出現
    「banner 印 dev、連線連 prod」——banner 是人唯一的煞車，它必須跟連線同一個字串。
    """
    url = resolve_db_url(prod)
    return url.rsplit("/", 1)[-1], url.replace("postgresql+asyncpg://", "postgresql://")


def print_target(dbname: str, apply: bool) -> None:
    """匯入腳本的第一行輸出：目標庫 + 是否真的寫。

    這行是 --apply 唯一的視覺確認，五支必須長得一模一樣 —— 格式若各寫各的，
    人就得每支重新確認一次「這是不是 dry-run」。
    """
    print(f"目標資料庫: {dbname}   模式: {'寫入 (--apply)' if apply else 'DRY-RUN（不寫入）'}")


def print_dry_run_end(hint: bool = False) -> None:
    """dry-run 收尾語。hint=True 追加「確認上面數字無誤後加 --apply」——
    有錨點可對的腳本（ledger / projects）才需要那句催人核對的話。"""
    print("\nDRY-RUN 結束 —— 沒有寫入任何東西。"
          + ("確認上面數字無誤後加 --apply。" if hint else ""))


async def connect(dsn: str):
    """asyncpg 連線（15 秒逾時）。

    逾時是必要的：NAS Postgres 不在時 asyncpg.connect 會**無限期**卡住，
    匯入腳本就變成一個看不出在等什麼的黑畫面。asyncpg 延遲 import —— 讓
    純解析的 dry-run（如 import_my_ledger 根本不連線）不必付這個 import 成本。
    """
    import asyncpg
    return await asyncio.wait_for(asyncpg.connect(dsn), 15)


def cli(run, csv_help: str) -> None:
    """匯入腳本的 __main__ 骨架：--csv/--prod/--apply → asyncio.run(run(csv, apply, prod))。

    三個旗標的語意（**預設 dry-run、預設 dev 庫**）是這批腳本的安全契約，
    所以連 argparse 也只留一份 —— 少一支腳本忘了 `action="store_true"`，
    `--apply` 就會變成永遠為真的字串。sys.stdout.reconfigure 同理：Windows
    主控台預設 cp950，少一行中文輸出就整支 UnicodeEncodeError 掛掉。
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help=csv_help)
    ap.add_argument("--prod", action="store_true", help="對生產庫 mediaguard（預設 dev）")
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設 dry-run）")
    ap.add_argument("--force-rebuild", action="store_true",
                    help="即使偵測到匯入後才產生的資料也照樣整批重建（會刪掉它們）")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(a.csv, a.apply, a.prod, a.force_rebuild))


def find_header(rows: list, mark: str) -> int:
    """回表頭列的索引（該列有一格恰好等於 mark）。找不到直接中止。"""
    for i, r in enumerate(rows):
        if any(c.strip() == mark for c in r):
            return i
    raise SystemExit(f"找不到表頭列（沒有任何一列含「{mark}」）")


def col_getter(header_row: list):
    """表頭列 → `col(row, 欄名, default="")` 取格函式（回傳值已 strip）。

    為什麼要函式而不是直接給 index dict：Sheet 匯出的 CSV **列長不齊**（尾巴
    連續空格會被截掉），所以每次取格都得檢查 `len(r) > i`；三支腳本各自寫了
    一份一模一樣的 closure 來包這個檢查，漏掉就是 IndexError。
    同名欄以**後出現者**為準（與原本三份 dict comprehension 的語意一致）。
    """
    ix = {h.strip(): i for i, h in enumerate(header_row)}

    def col(r, name, default=""):
        i = ix.get(name)
        return (r[i] if i is not None and len(r) > i else default).strip()

    return col


def money(s: str, *, none_on_bad: bool = False, usd: bool = False):
    """Sheet 金額字串 → int（四捨五入到元）。

    共同規則（五份複本原本就一致的部分）：去掉 `NT$` 與千分位逗號、全形括號
    先換成半形，**會計式括號＝負數**（`NT$ (1,004)` → −1004）。

    旗標保留刻意的差異，不靜默統一：
    - `none_on_bad=True`（淨值快照）：空格、`#REF!`（早年公式斷鏈）、看不懂的
      字串一律回 **None** 而不是 0 —— 快照要能區分「這個桶當天沒有值」與
      「這個桶是 0」，回 0 會讓斷鏈的格子被當成真的歸零，畫進趨勢圖。
    - `usd=True`（總資產明細）：連 `US$`／裸 `$` 也去掉 —— 只有那張表混了
      外幣欄。其他表沒有 `$` 開頭的格，開著也不會有事，但寫明旗標才看得出
      「這裡真的有外幣」。

    🔴 2026-08-25 統一時的一個行為修正：import_my_equipment 那份**沒有**處理
    括號（`NT$ (1,004)` 會 float() 失敗 → 靜默變 0，正負顛倒還不出聲）。固定
    資產表當天實測 0 格帶括號，所以修正對現有資料是 no-op；但語意上五份裡
    四份都認會計括號，那份是漏的，統一到「認括號」。
    """
    s = (s or "").replace("NT$", "").replace(",", "")
    if usd:
        s = s.replace("US$", "").replace("$", "")
    s = s.replace("（", "(").replace("）", ")").strip()
    if not s or (none_on_bad and "#REF" in s):
        return None if none_on_bad else 0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    try:
        v = float(s)
    except ValueError:
        return None if none_on_bad else 0
    return -round(v) if neg else round(v)


def parse_date(v: str, *, minguo: bool = False):
    """'2024/01/02' / '2024-1-2' → 該日的 UTC 午夜。壞值回 None（報告會列出來）。

    🔴 日期只把 Sheet 的斜線格式正規化，實際的 datetime **交給
    routers.crm._shared._parse_shoot_date 造** —— 這個 repo 的日期欄慣例是
    UTC 午夜（台北是 UTC+8，UTC 午夜換算台北仍同一天，_fmt_day 與前端取 ISO
    前 10 碼才會一致）。這裡自己 `datetime(y,m,d).date()` 會被寫成**台北**午夜
    ＝前一天 16:00Z，前端顯示就少一天（2026-08-19 第一版匯入實際踩到，394 筆全中）。

    `minguo=True`（owner 私帳總資產表）：三位數以下的年份視為民國年 +1911
    （`0114/12/21` → 2025/12/21，該表實測有 1 格這樣填）。**預設關閉**是刻意的
    —— 其他表若冒出 `0114/...` 那是打錯字，該進「跳過」清單讓人看見，不是
    默默幫它加 1911 造出一個看似合理的年份。
    """
    pat = (r"(\d{2,4})[/-](\d{1,2})[/-](\d{1,2})" if minguo
           else r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")
    m = re.fullmatch(pat, (v or "").strip())
    if not m:
        return None
    y = int(m[1])
    if not (1900 <= y <= 2100) and not (minguo and y < 1000):
        # 🔴 `0114/12/21` 這種格子在沒開 minguo 時是**打錯字**，要回 None 進
        # 「跳過」清單讓人看見。原本 (\d{4}) 會收下它並造出西元 114 年 ——
        # 一個沒有人會發現的日期（/simplify 第 4 輪抓到）。
        return None
    if minguo and y < 1000:
        y += 1911
    from routers.crm._shared import _parse_shoot_date
    return _parse_shoot_date(f"{y:04d}-{int(m[2]):02d}-{int(m[3]):02d}")


async def assert_safe_rebuild(conn, checks, force: bool) -> None:
    """整批刪掉重建之前，先確認沒有「匯入之後才產生的資料」會被一起清掉。

    🔴 為什麼需要這支：這批腳本的「冪等清場」在第 0 天是真的 —— 那時 mine
    帳本裡除了匯入沒有別的東西。功能長出來之後就不再成立：
      · PUT /project-ledger 會寫 ledger_detail 與結案日（owner 逐案整理的成果）
      · 卡單 apply 每個月寫進新的收支列
      · 持股 CRUD、拍快照
    重跑 --apply 會把這些一起刪掉，專案還會換一批新的 uuid（612 筆收支回掛與
    34 張請款單的關聯得全部重來）。而唯一會跑這些腳本的人，正是那些資料的作者。

    checks = [(說明, SQL 回一個數字), ...]；任一 > 0 就中止，除非 --force-rebuild。
    """
    hits = []
    for label, sql in checks:
        n = await conn.fetchval(sql)
        if n:
            hits.append(f"{label}：{n} 筆")
    if not hits:
        return
    print("\n🔴 中止：偵測到匯入之後才產生的資料，整批重建會把它們一起刪掉 ——")
    for h in hits:
        print("   ·", h)
    print("   確定要放棄這些資料的話，加 --force-rebuild 再跑一次。")
    sys.exit(1)


async def load_taxonomy_tree(conn, entity="mine"):
    """收支分類樹 → `({(parent_id, name): id}, {id: [路徑…]})`。

    給改分類的腳本用：寫 category/sub_item 的同時一定要把 `taxonomy_node_id`
    一起寫對，否則會留下「有類別、但不在樹上」的孤兒 —— 樹狀篩選看不到它，
    後台的影響筆數也算不到它。規則正本見 `core.cash_taxonomy.mirror_from_path`。
    """
    from core.cash_tree import index_paths   # walk 只有一份（含環保險）

    rows = await conn.fetch("SELECT id, parent_id, name FROM cash_taxonomy_nodes"
                            " WHERE entity=$1", entity)
    child = {(r["parent_id"] or "", r["name"]): r["id"] for r in rows}
    return child, index_paths((r["id"], r["parent_id"], r["name"]) for r in rows)


def node_for_path(child, names):
    """路徑（名稱陣列）→ 節點 id；對不到回空字串。"""
    nid = ""
    for nm in names:
        nid = child.get((nid, nm), "")
        if not nid:
            return ""
    return nid
