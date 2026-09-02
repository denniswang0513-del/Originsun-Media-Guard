"""services/timesheet_digest.py — 週一工時 digest（docs/WORK_TRACKING_UI_PLAN.md P3）。

上週（週一到週日）每人合計／填了幾天／漏填幾天，推 Google Chat（settings.json
notification.google_chat_webhook）。漏填＝最近 30 天有填過的人，該工作日沒有任何列。
排程掛在 services/timesheet_puller 的 loop（同一個 master gate、同一種 cron 設定），
設定在 settings.json `timesheet.digest {enabled, cron, last_run_at, last_summary}`。
文字由 core.hr_logic.digest_text 組（純函式、無 emoji）。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from config import load_settings, save_settings
from core.hr_logic import digest_text, hours_rollup, missing_fillers

logger = logging.getLogger(__name__)
DEFAULT_CRON = "0 9 * * 1"


def get_digest_settings() -> dict:
    p = ((load_settings().get("timesheet") or {}).get("digest") or {})
    return {
        "enabled": bool(p.get("enabled")),
        "cron": str(p.get("cron") or DEFAULT_CRON),
        "last_run_at": float(p.get("last_run_at") or 0),
        "last_summary": str(p.get("last_summary") or ""),
    }


def update_digest_settings(patch: dict) -> dict:
    if patch.get("cron"):
        from services.website._runner_util import validate_cron
        validate_cron(str(patch["cron"]))
    s = load_settings()
    p = s.setdefault("timesheet", {}).setdefault("digest", {})
    for k in ("enabled", "cron"):
        if k in patch and patch[k] is not None:
            p[k] = bool(patch[k]) if k == "enabled" else str(patch[k]).strip()
    save_settings(s)
    return get_digest_settings()


def _mark(**fields) -> None:
    s = load_settings()
    s.setdefault("timesheet", {}).setdefault("digest", {}).update(fields)
    save_settings(s)


async def build_digest(session, today: date | None = None) -> dict:
    """上週的 rollup ＋ 漏填天數；回 {text, week, rollup, missing}。純查詢，不發送。"""
    from sqlalchemy import select
    from db.models import Timesheet
    today = today or date.today()
    mon = today - timedelta(days=today.weekday() + 7)        # 上週一
    sun = mon + timedelta(days=6)
    d0 = datetime(mon.year, mon.month, mon.day)
    d1 = d0 + timedelta(days=7)
    since = d0 - timedelta(days=30)
    rows = (await session.execute(
        select(Timesheet.staff_name, Timesheet.work_date, Timesheet.project_name, Timesheet.hours)
        .where(Timesheet.work_date >= since).where(Timesheet.work_date < d1)
    )).all()
    week = [(n, d.astimezone().date(), p, h) for n, d, p, h in rows if d and d.astimezone().date() >= mon]
    active = {n for n, d, _p, _h in rows if n and d}
    rollup = hours_rollup(week, mon.year, mon.month)
    missing: dict = {}
    for i in range(5):                                        # 週一到週五
        day = mon + timedelta(days=i)
        filled = {n for n, d, _p, _h in week if d == day}
        for n in missing_fillers(active, filled):
            missing[n] = missing.get(n, 0) + 1
    label = f"{mon.isoformat()} ～ {sun.isoformat()}"
    return {"text": digest_text(label, rollup, missing), "week": label, "rollup": rollup, "missing": missing}


def _post_chat(text: str) -> bool:
    import os
    import requests
    notif = load_settings().get("notification") or {}
    url = os.environ.get("GOOGLE_CHAT_WEBHOOK") or notif.get("google_chat_webhook", "")
    if not url:
        return False
    requests.post(url, json={"text": text}, timeout=10).raise_for_status()
    return True


async def send_digest(force: bool = False) -> dict:
    """算上週 → 推 Google Chat → 記 last_run。沒 webhook 回 skipped（不炸）。"""
    import time
    cfg = get_digest_settings()
    if not cfg["enabled"] and not force:
        return {"status": "disabled"}
    from db.session import get_session_factory
    factory = get_session_factory()
    if factory is None:
        return {"status": "error", "message": "資料庫離線"}
    try:
        async with factory() as session:
            d = await build_digest(session)
        sent = _post_chat(d["text"])
        summary = f"{datetime.now():%m/%d %H:%M} {d['week']}：{len(d['rollup']['people'])} 人" + ("" if sent else "（沒設 webhook，沒送）")
        _mark(last_run_at=time.time(), last_summary=summary)
        return {"status": "ok" if sent else "skipped", "text": d["text"], "sent": sent}
    except Exception as e:
        logger.exception("[timesheet_digest] 失敗")
        _mark(last_run_at=time.time(), last_summary=f"{datetime.now():%m/%d %H:%M} 失敗：{e}")
        return {"status": "error", "message": str(e)}
