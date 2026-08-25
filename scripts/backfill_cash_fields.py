# -*- coding: utf-8 -*-
"""私帳收支缺欄回填（owner 2026-08-26「依照過去紀錄判斷來填寫」）。

    .venv/Scripts/python.exe scripts/backfill_cash_fields.py [--apply] [--prod]

🔴 預設 dry-run。只動 entity='mine'、只填**空**的欄（絕不覆寫已填值）。

兩層規則（保守優先，寧缺勿錯）：
  1. 同摘要精確比對：同 summary 的其他列已有 category/sub_item → 多數決回填
     （要求無異議：同摘要出現兩種類別就不填，列進人工清單）。
  2. 商家 pattern（只填 category）：summary 取第一段商家 token（－/＊/* 之前），
     同 token 的已分類列投票 —— 需 ≥3 筆樣本且最高票 ≥80% 才填。
     例：「街口電支－統一超商」缺類別 → 其他街口電支列絕大多數是 個人_生活。

sub_item 刻意只做第 1 層：子項目比類別細（同商家不同子項目很正常），
pattern 投票會把「超商」填到買咖啡的列上。

  3. 人工判斷規則（RULES，owner 2026-08-26 授權「依照你判斷的過去紀錄來填寫」）：
     每條規則旁註明依據（同商家已分類列的分布，2026-08-26 生產實查）。
     沒有規則命中的列留在人工清單（如「Pierre 個展影片」—— 分不出公私，不猜）。
"""
import asyncio
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts._common import connect, db_target, print_dry_run_end, print_target  # noqa: E402

MIN_SAMPLES = 3      # pattern 層：同商家至少要有這麼多已分類列
MIN_SHARE = 0.8      # pattern 層：最高票類別至少要佔這個比例

# 第 3 層：子字串 → 類別（順序即優先序；旁註＝生產已分類列的實查分布）
RULES = [
    ("KOBO", "個人_學習"),               # KOBO 電子書：個人_學習×30 壓倒性
    ("藍新－ＳＡＴ", "個人_學習"),        # SAT Knowledge 課程分期：藍新→個人_學習×8 為大宗
    ("利息存入", "個人_被動收入"),        # 利息：個人_被動收入×17 為大宗
    ("現金回饋", "個人_被動收入"),        # 卡回饋：個人_被動收入×23 為大宗
    ("ＣＩＴＹＬＩＮＫ", "家用_變動支出"),  # CITYLINK：家用_變動支出×10 vs 其他×1
    ("鵝肉", "家用"),                    # 呈信鵝肉：家用×2 無異議
    ("金玉堂", "家用"),                  # 金玉堂（善化＝家裡那邊）：家用×2 為大宗
    ("ATM現金", "個人_生活"),            # ATM：個人_生活×10 為大宗
    ("嘟嘟房", "個人_生活"),             # 停車：個人_生活×20 為大宗（20/43）
    ("聯信－", "個人_生活"),             # 晴光商圈停車場，同嘟嘟房晴光站的歸法
    ("Ａｕｔｏｐａｓｓ", "個人_生活"),     # 停車 app，同上
    ("ＷｅＭｏ", "個人_生活"),           # 共享機車＝日常交通
    ("街口ＴＷＱＲ", "個人_生活"),        # 跨機構購物＝日常消費
    ("誠和證件", "個人_生活"),           # 證件快照
    ("ＥａｓｙＷａｙ", "個人_生活"),      # 飲料店
    ("連加＊", "個人_生活"),             # LINE Pay 尾巴＝零星餐飲小店（大宗歸法）
    ("街口電支", "個人_生活"),           # 同 pattern 層的街口歸法
    ("Spotify", "個人_生活"),            # 個人_生活×31 壓倒性
    ("悠遊卡", "個人_生活"),             # 個人_生活×16 為大宗
    ("中油", "個人_生活"),               # 個人_生活×4 為大宗
    ("高鐵", "個人_生活"),               # 個人_生活×3 為大宗
    ("統一超商", "個人_生活"),           # 個人_生活×29 vs 家用×17
    ("全聯", "家用_變動支出"),           # 家用系×19 壓倒性（含鮮食集大全聯）
    ("潤泰旭展", "家用_變動支出"),        # 家用_變動支出×7 為大宗
    ("ＰＣＨＯＭＥ", "公司_代墊"),        # owner Sheet 自註「pchome24期 公司代墊」＋前例×24
    ("威聯通", "公司_器材"),             # QNAP 硬體；owner Sheet 同型自註「MoMO6期 公司器材」
    ("台灣大車隊", "個人_生活"),          # 計程車＝日常交通
    ("eTag", "個人_生活"),               # 國道儲值＝日常交通
    ("作業處理", "個人_生活"),            # 個人_生活×6 為大宗
    ("易遊網", "個人_旅遊"),             # 旅行社訂購（判斷歸法）
    ("CANVA", "公司_代墊"),              # 公司_代墊×2 無異議
    ("ｉＰａｒｋｉｎｇ", "個人_生活"),    # 個人_生活×4 為大宗
    ("城市車旅", "個人_生活"),           # 停車，同其他停車商家歸法
    ("COCA-COLA", "個人_生活"),          # 自販機飲料
]

_TOKEN_RE = re.compile(r"^[^－＊*\-（(]{2,}")


def merchant_token(summary: str):
    """summary 的商家前綴（－/＊/*/-/括號 之前，至少 2 字）；取不出來回 None。"""
    s = (summary or "").strip()
    if s == "" or not any(d in s for d in "－＊*-（("):
        return None                      # 無分隔符＝整句即商家，交給第 1 層
    m = _TOKEN_RE.match(s)
    return m.group(0) if m else None


def vote(counter: Counter, min_samples=1, min_share=1.0):
    """多數決：樣本夠且份額夠 → 回傳勝出值，否則 None。"""
    total = sum(counter.values())
    if total < min_samples:
        return None
    val, n = counter.most_common(1)[0]
    return val if n / total >= min_share else None


async def run(apply: bool, prod: bool):
    dbname, dsn = db_target(prod)
    print_target(dbname, apply)
    c = await connect(dsn)
    try:
        rows = await c.fetch(
            """SELECT id, summary, category, sub_item FROM crm_cash_entries
               WHERE entity='mine' ORDER BY entry_date""")
        by_summary_cat = defaultdict(Counter)
        by_summary_sub = defaultdict(Counter)
        by_token_cat = defaultdict(Counter)
        for r in rows:
            if r["category"]:
                by_summary_cat[r["summary"]][r["category"]] += 1
                tok = merchant_token(r["summary"])
                if tok:
                    by_token_cat[tok][r["category"]] += 1
            if r["sub_item"]:
                by_summary_sub[r["summary"]][r["sub_item"]] += 1

        fill_cat, fill_sub, manual = [], [], []
        for r in rows:
            if not r["category"]:
                # 第 1 層：同摘要無異議
                won = vote(by_summary_cat.get(r["summary"], Counter()))
                layer = "同摘要"
                if not won:
                    tok = merchant_token(r["summary"])
                    if tok:
                        won = vote(by_token_cat.get(tok, Counter()),
                                   MIN_SAMPLES, MIN_SHARE)
                        layer = f"商家[{tok}]"
                if not won:
                    for pat, cat in RULES:
                        if pat in (r["summary"] or ""):
                            won, layer = cat, f"規則[{pat}]"
                            break
                if won:
                    fill_cat.append((r["id"], won, layer, r["summary"]))
                else:
                    manual.append(r["summary"])
            if not r["sub_item"]:
                won = vote(by_summary_sub.get(r["summary"], Counter()))
                if won:
                    fill_sub.append((r["id"], won, r["summary"]))

        print(f"category 可回填: {len(fill_cat)}／仍缺: {len(manual)}")
        print(f"sub_item 可回填: {len(fill_sub)}")
        print("\n-- category 回填明細（依規則彙整）--")
        agg = Counter((c_, l_) for _i, c_, l_, _s in fill_cat)
        for (cat, layer), n in agg.most_common():
            print(f"  {n:>3} 筆 → {cat:<14} 依 {layer}")
        print("\n-- 仍缺（人工清單，去重前 25）--")
        for s, n in Counter(manual).most_common(25):
            print(f"  {n:>2} × {s[:56]}")
        if not apply:
            print_dry_run_end()
            return

        await c.executemany(
            """UPDATE crm_cash_entries SET category=$2, updated_at=now()
               WHERE id=$1 AND (category IS NULL OR category='')""",
            [(i, cat) for i, cat, _l, _s in fill_cat])
        await c.executemany(
            """UPDATE crm_cash_entries SET sub_item=$2, updated_at=now()
               WHERE id=$1 AND (sub_item IS NULL OR sub_item='')""",
            [(i, sub) for i, sub, _s in fill_sub])
        left = await c.fetchval(
            "SELECT count(*) FROM crm_cash_entries WHERE entity='mine'"
            " AND (category IS NULL OR category='')")
        print(f"\n已回填 category {len(fill_cat)}、sub_item {len(fill_sub)}；"
              f"仍缺 category {left}（＝人工清單）")
        print("驗證通過 ✓" if left == len(manual) else "🔴 落地筆數與預覽不符")
        if left != len(manual):
            sys.exit(1)
    finally:
        await c.close()


if __name__ == "__main__":
    # _common.cli 綁 --csv（匯入腳本契約）；本腳本無 CSV，自帶同語意旗標
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", action="store_true", help="對生產庫 mediaguard（預設 dev）")
    ap.add_argument("--apply", action="store_true", help="真的寫入（預設 dry-run）")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(run(a.apply, a.prod))
