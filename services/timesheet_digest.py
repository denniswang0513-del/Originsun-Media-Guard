"""services/timesheet_digest.py — 週一工時 digest（docs/WORK_TRACKING_UI_PLAN.md P3）。

上週（週一到週日）每人合計／填了幾天／漏填幾天，推 Google Chat（notifier.send_google_chat，
同 notify_tab 的 webhook 來源）。漏填＝最近 ACTIVE_WINDOW_DAYS 天有填過的人，該工作日沒有任何列。
自己的排程 loop（同其他 runner：start_scheduler_task ＋ master gate），設定在 settings.json
`timesheet.digest {enabled, cron, last_run_at, last_summary}`（services.timesheet_settings）。
文字由 core.hr_logic.digest_text 組（純函式、無 emoji）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional

from core.hr_logic import (ACTIVE_WINDOW_DAYS, active_fillers, digest_text, hours_rollup,
                           is_workday, local_day, missing_fillers)
from services.timesheet_settings import SettingsBlock

logger = logging.getLogger(__name__)
DEFAULT_CRON = "0 9 * * 1"
_scheduler_task: Optional[asyncio.Task] = None
settings = SettingsBlock("digest", settable=("enabled", "cron"),
                         defaults={"enabled": False, "cron": DEFAULT_CRON})


async def build_digest(session, today: date | None = None) -> dict:
    """上週的 rollup ＋ 漏填天數；回 {text, week, rollup, missing}。純查詢，不發送。"""
    from sqlalchemy import select
    from db.models import Timesheet
    today = today or date.today()
    mon = today - timedelta(days=today.weekday() + 7)        # 上週一
    sun = mon + timedelta(days=6)
    d0 = datetime(mon.year, mon.month, mon.day)
    rows = (await session.execute(
        select(Timesheet.staff_name, Timesheet.work_date, Timesheet.project_name, Timesheet.hours)
        .where(Timesheet.work_date >= d0 - timedelta(days=ACTIVE_WINDOW_DAYS))
        .where(Timesheet.work_date < d0 + timedelta(days=7)))).all()
    data = [(n, local_day(d), p, h) for n, d, p, h in rows]
    week = [x for x in data if x[1] and x[1] >= mon]
    active = active_fillers(data, sun)
    rollup = hours_rollup(week, mon.year, mon.month)
    missing: dict = {}
    for i in range(7):
        day = mon + timedelta(days=i)
        if not is_workday(day):
            continue
        filled = {n for n, d, _p, h in week if d == day and (h or 0) > 0}
        for n in missing_fillers(active, filled):
            missing[n] = missing.get(n, 0) + 1
    label = f"{mon.isoformat()} ～ {sun.isoformat()}"
    return {"text": digest_text(label, rollup, missing), "week": label, "rollup": rollup, "missing": missing}


async def send_digest(force: bool = False) -> dict:
    """算上週 → 推 Google Chat → 記 last_run。沒 webhook 回 skipped（不炸）。"""
    from notifier import send_google_chat
    cfg = settings.get()
    if not cfg["enabled"] and not force:
        return {"status": "disabled"}
    from db.session import get_session_factory
    factory = get_session_factory()
    if factory is None:
        return {"status": "error", "message": "資料庫離線"}
    try:
        async with factory() as session:
            d = await build_digest(session)
        sent = send_google_chat(d["text"])
        summary = f"{datetime.now():%m/%d %H:%M} {d['week']}：{len(d['rollup']['people'])} 人" + ("" if sent else "（沒設 webhook，沒送）")
        settings.mark(last_run_at=time.time(), last_summary=summary)
        return {"status": "ok" if sent else "skipped", "text": d["text"], "sent": sent}
    except Exception as e:
        logger.exception("[timesheet_digest] 失敗")
        settings.mark(last_run_at=time.time(), last_summary=f"{datetime.now():%m/%d %H:%M} 失敗：{e}")
        return {"status": "error", "message": str(e)}


async def _scheduler_loop() -> None:
    """每 60 秒看 cron 到期沒 → send_digest。首次啟用等下一個時點，不立刻轟一封。只在 master 跑。"""
    from core.topology import is_master_machine
    from services.website._runner_util import cron_due
    if not is_master_machine():
        logger.info("[timesheet_digest] 非 master（或 dev）— digest 排程不啟動；POST /digest 仍可手動")
        return
    await asyncio.sleep(65)
    while True:
        try:
            cfg = settings.get()
            if cfg["enabled"] and cfg["cron"]:
                if cfg["last_run_at"] <= 0:
                    settings.mark(last_run_at=time.time())
                elif cron_due(cfg["cron"], cfg["last_run_at"]):
                    await send_digest()
        except Exception:
            logger.exception("[timesheet_digest] scheduler loop 異常")
        await asyncio.sleep(60)


def start_scheduler_task() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
