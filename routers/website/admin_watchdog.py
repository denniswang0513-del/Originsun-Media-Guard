"""routers/website/admin_watchdog.py — 站點守衛 admin API

前端「🛡️ 站點守衛」+「🔍 Google 收錄」兩張卡片的後端。
邏輯全在 services/website/watchdog_runner.py，這裡只做守衛 + 形狀轉換。

立即執行類端點一律丟背景 task（探測約 10 秒、收錄掃描可能數分鐘），
立刻回 {"queued": true}，前端輪詢 /watchdog/status 取結果。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session_factory
from services.website import watchdog_runner

from ._common import admin_guard, admin_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/website/admin", tags=["website-admin-watchdog"])


def _iso(dt) -> str | None:
    return dt.isoformat() if isinstance(dt, datetime) else None


async def _index_summary(session: AsyncSession, cfg: dict) -> dict | None:
    """從 website_index_status 聚合出儀表板要的數字。沒資料 → 回 total=0 的骨架。"""
    from db.models_website.watchdog import WebsiteIndexStatus

    # 一趟聚合就夠 —— 原本 count / count-where / max 各掃一次同一張表
    total, indexed, checked_at = (await session.execute(select(
        func.count(),
        func.count().filter(func.upper(WebsiteIndexStatus.verdict) == "PASS"),
        func.max(WebsiteIndexStatus.checked_at),
    ).select_from(WebsiteIndexStatus))).one()
    total = total or 0
    indexed = indexed or 0

    home_url = cfg["site_url"] + "/"
    home = (await session.execute(
        select(WebsiteIndexStatus).where(WebsiteIndexStatus.url == home_url))).scalar_one_or_none()

    home_out = None
    if home is not None:
        days = None
        if home.last_crawl_at:
            days = (datetime.now(timezone.utc) - home.last_crawl_at).days
        home_out = {
            "url": home.url, "verdict": home.verdict,
            "coverage_state": home.coverage_state,
            "last_crawl_at": _iso(home.last_crawl_at), "days_since_crawl": days,
        }

    return {
        "total": total, "indexed": indexed, "not_indexed": max(0, total - indexed),
        "stale_days": cfg["stale_days"], "homepage": home_out, "checked_at": _iso(checked_at),
    }


@router.get("/watchdog/status")
async def watchdog_status(session: AsyncSession = Depends(admin_session)):
    cfg = await watchdog_runner.get_watchdog_settings(session)
    summary = await _index_summary(session, cfg) if cfg["gsc_configured"] else None
    return {
        "enabled": bool(cfg["enabled"]),
        "cron": cfg["cron"],
        "index_cron": cfg["index_cron"],
        "site_url": cfg["site_url"],
        "gsc_site_url": cfg["gsc_site_url"],
        "alert_email": cfg["alert_email"],
        "stale_days": cfg["stale_days"],
        "last_probe_at": float(cfg["last_run_at"] or 0) or None,
        "last_probe": cfg["last_probe"],
        "gsc_configured": cfg["gsc_configured"],
        "is_master": cfg["is_master"],
        "index_summary": summary,
    }


async def _bg(coro_fn) -> None:
    """背景執行：自帶 session（請求的 session 在回應後就關了）。"""
    factory = get_session_factory()
    if factory is None:
        return
    try:
        async with factory() as s:
            await coro_fn(s)
    except Exception as e:  # noqa: BLE001 — 背景任務不能把例外丟回 event loop
        logger.error("[watchdog] 背景任務失敗：%s", e)


# event loop 只持有 task 的弱參考 —— 沒人拿著強參考的話，探測/掃描可能在跑完前被 GC。
_pending: set = set()


def _fire(coro_fn) -> dict:
    task = asyncio.create_task(_bg(coro_fn))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
    return {"queued": True}


@router.post("/watchdog/run")
async def watchdog_run(_: None = Depends(admin_guard)):
    """立即跑一次多 UA 篡改探測。"""
    return _fire(lambda s: watchdog_runner.run_probe(s, trigger="manual"))


@router.post("/watchdog/run-index")
async def watchdog_run_index(_: None = Depends(admin_guard)):
    """立即跑一次 Search Console 收錄掃描（可能數分鐘）。"""
    return _fire(lambda s: watchdog_runner.run_index_check(s, trigger="manual"))


@router.get("/watchdog/events")
async def watchdog_events(limit: int = Query(20, ge=1, le=100),
                          session: AsyncSession = Depends(admin_session)):
    from db.models_website.watchdog import WebsiteWatchdogEvent
    rows = (await session.execute(
        select(WebsiteWatchdogEvent)
        .order_by(desc(WebsiteWatchdogEvent.created_at)).limit(limit))).scalars().all()
    return {"items": [{
        "id": r.id, "level": r.level, "kind": r.kind, "title": r.title,
        "detail": r.detail, "created_at": _iso(r.created_at),
    } for r in rows]}


@router.get("/watchdog/index")
async def watchdog_index(state: str = Query("all", pattern="^(all|indexed|not_indexed)$"),
                         limit: int = Query(50, ge=1, le=500),
                         session: AsyncSession = Depends(admin_session)):
    from db.models_website.watchdog import WebsiteIndexStatus
    stmt = select(WebsiteIndexStatus)
    if state == "indexed":
        stmt = stmt.where(func.upper(WebsiteIndexStatus.verdict) == "PASS")
    elif state == "not_indexed":
        stmt = stmt.where(func.coalesce(func.upper(WebsiteIndexStatus.verdict), "") != "PASS")
    rows = (await session.execute(stmt.order_by(WebsiteIndexStatus.url).limit(limit))).scalars().all()
    return {"items": [{
        "url": r.url, "verdict": r.verdict, "coverage_state": r.coverage_state,
        "last_crawl_at": _iso(r.last_crawl_at), "error": r.error,
    } for r in rows]}


@router.put("/watchdog/config")
async def watchdog_config(request: Request, session: AsyncSession = Depends(admin_session)):
    payload = await request.json()
    try:
        cfg = await watchdog_runner.update_watchdog_settings(session, payload)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"ok": True, "config": {k: cfg[k] for k in (
        "enabled", "cron", "index_cron", "stale_days", "alert_email",
        "site_url", "gsc_site_url", "gsc_configured")}}
