# -*- coding: utf-8 -*-
"""工時 Google Sheet 歷史匯入（docs/TIMESHEET_IMPORT_PLAN.md Phase B／C）。

    # dry-run：只出報告，不寫任何東西（--prod 讀生產庫的私帳案來預估對映）
    .venv/Scripts/python.exe scripts/import_timesheets.py --xlsx timesheet.xlsx --budget [--prod]
    # 真的匯（dev 8001）；--prod 改打生產 8000
    .venv/Scripts/python.exe scripts/import_timesheets.py --xlsx timesheet.xlsx --budget --apply [--prod]

xlsx 來源：整本試算表 `export?format=xlsx`（「總表（勿動）」已是兩個輸入分頁的
聯集、值已算好）。**--apply 走 HTTP 打 /timesheets/ingest**，不直接碰 DB —— 同 row_hash、
同手填優先去重，重跑第二次 0 新增（冪等）。

🔴 dry-run 讀庫**不經 `db.session.init_db()`** —— 那支會 `create_all` 到目標庫，帶 --prod
的「只讀預估」就會在生產建表（2026-09-02 實際發生過一次，建了空的 timesheet_project_map）。
這裡用 scripts/_common.resolve_db_url 拿 DSN、自己開 engine，只 SELECT。

🔴 日期一律轉成 `yyyy/MM/dd` 字串再送：Apps Script 是 `Utilities.formatDate(d,
'Asia/Taipei','yyyy/MM/dd')`，hash 用的是日期**字串**，兩條路格式不同就會在交接處
重複入庫（規劃 D9）。
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import datetime as dt
import io
import os
import sys
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.hr_logic import INTERNAL_BUCKETS, explain_miss, resolve_project  # noqa: E402
from scripts._common import admin_session, api_base, resolve_db_url  # noqa: E402

DATA_SHEET = "總表（勿動）"
STATUS_SHEET = "專案狀態(勿動)"
BATCH = 200


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date_str(v) -> str:
    """xlsx 的 datetime → 'yyyy/MM/dd'（同 Apps Script）；已是字串就原樣 strip。"""
    if isinstance(v, (dt.datetime, dt.date)):
        return f"{v.year:04d}/{v.month:02d}/{v.day:02d}"
    return str(v or "").strip()


def read_rows(wb) -> tuple:
    """回 (可送的列, 壞列)。壞列＝日期解析不了或時數不是數字 —— 收進報告，不送。"""
    ws = wb[DATA_SHEET]
    good, bad = [], []
    for i, r in enumerate(ws.iter_rows(min_row=4, values_only=True), start=4):
        r = list(r) + [None] * 8
        d, who, proj, task, hrs = r[:5]
        if d is None and not who and not proj and not task:
            continue
        h = _num(hrs)
        ds = _date_str(d)
        if h is None or not ds or len(ds) < 8:
            bad.append({"row": i, "date": ds, "staff": who, "project": proj, "hours": hrs})
            continue
        good.append({"date": ds, "staff": str(who or "").strip(),
                     "project": str(proj or "").strip(),
                     "task": str(task or "").strip(), "hours": h})
    return good, bad


def read_budgets(wb) -> list:
    """「專案狀態」：預算＝剩餘＋實際（規劃 §1.1 驗過）；≤0＝沒設，不送。"""
    out = []
    for r in wb[STATUS_SHEET].iter_rows(min_row=2, values_only=True):
        r = list(r) + [None] * 12
        menu = (r[1] or "")
        if not menu:
            continue
        b = (_num(r[9]) or 0) + (_num(r[10]) or 0)
        if b > 0:
            out.append({"sheet_name": str(menu).strip(), "budget_hours": round(b, 1)})
    return out


async def _lookup(prod: bool):
    """只 SELECT 的查表（不 init_db、不 create_all）—— 跟 router 吃同一支 services 函式。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from services.timesheet_lookup import load_project_lookup
    engine = create_async_engine(resolve_db_url(prod))   # settings 的 DSN 已是 +asyncpg
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            return await load_project_lookup(s)
    finally:
        await engine.dispose()


def _dry_run(hours, P, prod: bool) -> None:
    lk = asyncio.run(_lookup(prod))
    tiers = collections.defaultdict(list)
    for p in sorted(hours, key=lambda x: -hours[x]):
        _pid, why = resolve_project(p, lk)
        tiers[why].append((p, round(hours[p], 1)))
    P("## 對映預估（不寫）")
    for why in ("map", "exact", "key", "key+client", "ambiguous", "none", "bucket"):
        rows = tiers.get(why, [])
        P(f"- **{why}**：{len(rows)} 個，{round(sum(h for _, h in rows))} 小時")
    P("")
    P("## 撞案（owner 在 tab 上「指定專案」）")
    for p, h in tiers.get("ambiguous", []):
        cands = explain_miss(p, lk)["candidates"]
        P(f"- {p}（{h}h）→ 候選 {[(pid[:8], cli) for pid, _nm, cli in cands]}")
    P("")
    P("## 找不到（owner 決定：對既有案／建新案／當桶）—— 括號內是相似案名建議，不是對映")
    for p, h in tiers.get("none", []):
        sug = explain_miss(p, lk)["suggestions"]
        P(f"- {p}（{h}h）" + (f" → 像：{sug}" if sug else ""))


def _apply(base: str, good: list, budgets: list, P) -> None:
    """走 HTTP。灌預算＝寫私帳案，守 full scope → token 要帶 finance_mine。"""
    s = admin_session("import_timesheets", modules=["finance_mine"])
    r = s.get(base + "/api/v1/timesheets/ingest_token", timeout=30)
    r.raise_for_status()
    ingest_headers = {"X-Timesheet-Token": r.json()["token"]}

    tot = collections.Counter()
    amb: set = set()
    unm: set = set()
    sun: set = set()
    for i in range(0, len(good), BATCH):
        r = s.post(base + "/api/v1/timesheets/ingest",
                   json={"rows": good[i:i + BATCH], "source": "import"},
                   headers=ingest_headers, timeout=120)
        r.raise_for_status()
        resp = r.json()
        for k in ("inserted", "skipped", "skipped_manual_priority"):
            tot[k] += resp.get(k, 0)
        amb |= set(resp.get("ambiguous_projects", []))
        unm |= set(resp.get("unmatched_projects", []))
        sun |= set(resp.get("staff_unmatched", [])) | set(resp.get("staff_ambiguous", []))
        print(f"  batch {i // BATCH + 1}: {resp.get('inserted')} 新增 / {resp.get('skipped')} 重複")
    P("## 匯入結果")
    P(f"- 新增 {tot['inserted']}、重複跳過 {tot['skipped']}、手填優先擋下 {tot['skipped_manual_priority']}")
    P(f"- 撞案 {len(amb)}：{sorted(amb)}")
    P(f"- 找不到 {len(unm)}：{sorted(unm)}")
    P(f"- 人員對不到／同名 {len(sun)}：{sorted(sun)}")
    if budgets:
        r = s.put(base + "/api/v1/timesheets/budgets", json={"items": budgets}, timeout=120)
        r.raise_for_status()
        b = r.json()
        P(f"- 預算：寫入 {b['applied']} 案；撞案 {len(b['ambiguous_projects'])}；"
          f"找不到 {len(b['unmatched_projects'])}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")   # Windows 主控台 cp950 會讓中文報告整支炸掉
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--budget", action="store_true", help="一併從「專案狀態」灌 budget_hours")
    ap.add_argument("--apply", action="store_true", help="真的送；不帶＝只出報告（dry-run）")
    ap.add_argument("--prod", action="store_true", help="生產（8000／mediaguard）；預設 dev（8001／mediaguard_dev）")
    ap.add_argument("--report", default=os.path.join(REPO, "docs", "timesheet_import_report.md"))
    a = ap.parse_args()

    import openpyxl
    wb = openpyxl.load_workbook(a.xlsx, read_only=True, data_only=True)   # 開一次，兩個分頁共用
    good, bad = read_rows(wb)
    budgets = read_budgets(wb) if a.budget else []
    hours = collections.Counter()
    for r in good:
        hours[r["project"]] += r["hours"]
    staff = collections.Counter(r["staff"] for r in good)

    out = io.StringIO()

    def P(*x):
        out.write(" ".join(str(y) for y in x) + "\n")

    mode = ("生產" if a.prod else "dev") + ("，APPLY" if a.apply else "，dry-run")
    P(f"# 工時匯入報告 {dt.datetime.now():%Y-%m-%d %H:%M}（{mode}）")
    P("")
    P(f"- 可送 {len(good)} 列 / {sum(hours.values()):.1f} 小時；壞列 {len(bad)}（不送）")
    P(f"- 人員：{dict(staff.most_common())}（對不到 crm_staff 的在 ingest 回應 staff_unmatched）")
    P(f"- 專案名 {len(hours)} 個；內部桶 {sorted(p for p in hours if p in INTERNAL_BUCKETS)}")
    if a.budget:
        P(f"- 預算：{len(budgets)} 案有值")
    P("")
    if bad:
        P("## 壞列（請到 Sheet 修）")
        for b in bad[:60]:
            P(f"- 列 {b['row']}：{b['date']!r} {b['staff']} {b['project']} hours={b['hours']!r}")
        P("")

    if a.apply:
        _apply(api_base(a.prod), good, budgets, P)
    else:
        _dry_run(hours, P, prod=a.prod)

    Path(a.report).write_text(out.getvalue(), encoding="utf-8")
    print(out.getvalue())
    print(f"報告：{a.report}")


if __name__ == "__main__":
    main()
