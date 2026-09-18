# -*- coding: utf-8 -*-
"""services/knowledge_report.py — 研究週報／月報（docs/KNOWLEDGE_BASE_PLAN.md §9.5）。

owner 2026-09-18：「我想要有個週報或月報」＋「這些生產的資料都用 md 檔整理起來」
＋「不用送 google chat 送 discord」＋「這些報表可以使用連結」。

所以一份報告有三個面貌，同一份內容：
  1. **md 檔** —— 存在書架旁邊的 `reports\\`（跟書一樣不進 DB、不進 git）。
  2. **連結** —— `/knowledge.html#report/<id>`，端點在 `routers/api_knowledge.py`。
  3. **Discord** —— 一則摘要＋那個連結（他在手機上看，連結要點得到）。

排程：每天過 `knowledge.report.hour` 之後看一次 —— 週一發上一週、每月一號發上個月。
報告檔已經在了就不重發（檔案存在＝那一期發過了，不用另外記狀態）。
一期沒有任何新收錄就整個跳過，不發空報告。
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime

from core import knowledge_logic as kl
from services import knowledge_service as ks

logger = logging.getLogger(__name__)

#: 報告檔名（也是連結上的 id）：`weekly-2026-W38` / `monthly-2026-09`
_ID_RE = re.compile(r"^(weekly|monthly)-\d{4}-(W\d{2}|\d{2})$")
#: 書架網址（Discord 的連結指這裡；office-api 那一面不供 knowledge.html，要用絕對網址）
PUBLIC_BASE = "https://foundry.originsun-studio.com/knowledge.html"
#: 這個功能在排程 `_daily_fired` 裡的名字
TASK_KEY = "knowledge_report"


class ReportNotFound(Exception):
    """id 不合規或那一期的檔不在（端點回 404）。跟書分開，訊息才講得對東西。"""


def settings_report() -> dict:
    """`settings.knowledge.report`（讀不到就用預設：開、週一、9 點）。"""
    try:
        from config import load_settings
        raw = ((load_settings().get("knowledge") or {}).get("report") or {})
    except Exception:
        raw = {}
    from services.knowledge_watch import _int_in
    return {
        "enabled": True if raw.get("enabled") is None else bool(raw.get("enabled")),
        "weekday": _int_in(raw.get("weekday"), 0, 0, 6),      # 0＝週一
        "hour": _int_in(raw.get("hour"), 9, 0, 23),
    }


# ── 檔案 ─────────────────────────────────────────────────────
def reports_dir() -> str:
    """書架旁邊的 `reports\\`（`<書架根目錄>\\..\\reports`）。不存在就建。"""
    d = os.path.join(os.path.dirname(os.path.abspath(ks.root())), "reports")
    os.makedirs(d, exist_ok=True)
    return d


def is_valid_report_id(name) -> bool:
    return bool(_ID_RE.match(str(name or "")))


def report_path(report_id: str) -> str:
    """🔴 任何拼路徑前先過 `is_valid_report_id` —— 同書 id 的規矩，不信外面來的字。"""
    if not is_valid_report_id(report_id):
        raise ReportNotFound(report_id)
    return os.path.join(reports_dir(), report_id + ".md")


def _title_of(report_id: str) -> str:
    kind, _, label = report_id.partition("-")
    return ("研究週報 " if kind == "weekly" else "研究月報 ") + label


def list_reports() -> list:
    """新的排前面。只回檔名合規的。"""
    out = []
    for fn in os.listdir(reports_dir()):
        rid = fn[:-3] if fn.endswith(".md") else ""
        if not is_valid_report_id(rid):
            continue
        out.append({"id": rid, "title": _title_of(rid),
                    "kind": rid.split("-", 1)[0],
                    "bytes": os.path.getsize(os.path.join(reports_dir(), fn))})
    return sorted(out, key=lambda r: (r["id"].split("-", 1)[1], r["kind"]), reverse=True)


def read_report(report_id: str) -> dict:
    path = report_path(report_id)
    if not os.path.exists(path):
        raise ReportNotFound(report_id)
    with open(path, encoding="utf-8") as f:
        return {"id": report_id, "title": _title_of(report_id), "md": f.read()}


def report_url(report_id: str) -> str:
    return f"{PUBLIC_BASE}#report/{report_id}"


# ── 收集 ─────────────────────────────────────────────────────
def collect(start: date, end: date) -> list:
    """`[(書名, [則])]`，照書分組、只留區間內的；沒東西的書不會進來。"""
    groups = []
    for b in ks.list_books():
        try:
            rows = [i for i in ks.read_extend(b["id"]) if kl.in_span(i, start, end)]
        except ks.BookNotFound:
            continue
        if rows:
            groups.append((b.get("title") or "（未命名）", rows))
    return groups


def _write(report_id: str, md: str) -> str:
    path = report_path(report_id)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(md)
    return path


def build(report_id: str, start: date, end: date) -> dict:
    """做一期報告：寫 md 檔、回 `{id, url, groups, n}`。沒東西就回 `n=0` 且不寫檔。"""
    kind, _, label = report_id.partition("-")
    groups = collect(start, end)
    n = sum(len(rows) for _, rows in groups)
    if not n:
        return {"id": report_id, "url": "", "groups": [], "n": 0}
    md = (kl.weekly_report_md if kind == "weekly" else kl.monthly_report_md)(label, start, end, groups)
    _write(report_id, md)
    return {"id": report_id, "url": report_url(report_id), "groups": groups, "n": n}


def push(report: dict) -> bool:
    """推 Discord：摘要＋連結。沒設 webhook 就只是回 False（報告檔還是留著）。"""
    if not report.get("n"):
        return False
    from notifier import send_discord
    text = kl.report_push_text(_title_of(report["id"]), report["groups"],
                               tail="完整報告：" + report["url"])
    return send_discord(text)


def run_one(report_id: str, start: date, end: date) -> dict:
    """做一期＋推出去。已經有那個檔就不重做（檔案存在＝發過了）。"""
    if os.path.exists(report_path(report_id)):
        return {"id": report_id, "n": 0, "skipped": "已經有這一期了"}
    rep = build(report_id, start, end)
    if rep["n"]:
        rep["pushed"] = push(rep)
    return rep


def daily_check_sync(today: date) -> list:
    """今天該發哪幾期（週一發上一週、一號發上個月；同一天兩個都到就都發）。"""
    done = []
    if today.weekday() == settings_report()["weekday"]:
        label, start, end = kl.last_week(today)
        done.append(run_one("weekly-" + label, start, end))
    if today.day == 1:
        label, start, end = kl.last_month(today)
        done.append(run_one("monthly-" + label, start, end))
    return done


async def daily_check() -> None:
    """排程每分鐘叫一次；真正的 gate 在 `_run_daily_master_task` 與 `enabled` 裡。"""
    conf = settings_report()
    if not conf["enabled"]:
        return
    from core.scheduler import _run_daily_master_task

    async def _body(factory, now: datetime):
        # 🔴 丟到執行緒：掃書架、讀每本的延伸、requests.post(timeout=10) 打 Discord 都是同步 I/O，
        # 直接在這裡跑＝在排程迴圈（主事件迴圈）上跑，Discord 慢一次就把 8000 的所有 HTTP 一起卡住
        # （2026-09-19 /polish BUG-7）。body 回來才標當日完成的語意不變。
        import asyncio
        for rep in await asyncio.to_thread(daily_check_sync, now.date()):
            if rep.get("n"):
                logger.info("研究報告 %s：%d 則，Discord %s",
                            rep["id"], rep["n"], "推了" if rep.get("pushed") else "沒推（沒設 webhook）")

    await _run_daily_master_task(TASK_KEY, "", _body,
                                 hour_getter=lambda s: settings_report()["hour"])
