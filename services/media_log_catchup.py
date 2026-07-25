"""services/media_log_catchup.py — master 補做「NAS 對外容器做不到的事」（只在 master 跑）。

影像紀錄的上傳改由 NAS 對外容器收（master 關機也能傳），但那個容器刻意極簡：
沒有 ffmpeg（對外服務不該為了抽影格多背 250MB）、沒有 notifier 與 webhook 設定。
這些工作因此改成「master 醒著時掃 DB 補做」，兩件都設計成**冪等、可重複執行**：

  1. 縮圖/時長補算 —— 影片縮圖與時長、圖片縮圖漏產、舊縮圖遷移到共用圖床
  2. 上傳通知 —— 每專案安靜 NOTIFY_QUIET_SEC 後發一則 digest

master 長期關機 = 縮圖與通知延後出現，不影響收檔與下載（刻意接受的降級）。
"""
from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_INTERVAL_SEC = 600          # 每 10 分鐘掃一次（縮圖不是即時需求）
_BATCH = 20                  # 單輪上限 — 補算會跑 ffmpeg，不要一次吃滿 CPU
_FIRST_DELAY_SEC = 90        # 開機後讓其他 startup 工作先跑完


def _needs_catchup(rec, assets_base: str) -> bool:
    """這筆是否需要補：沒縮圖，或縮圖還在舊落點（非圖床網址）。

    純函式（不碰磁碟/DB）→ 單元測試在 tests/unit/test_media_log_catchup.py。
    SQL 端等價過濾在 _needs_catchup_clause（兩者必須同義，有守衛測試對照）。
    """
    url = (getattr(rec, "thumb_url", "") or "").strip()
    if not url:
        return True
    return bool(assets_base) and not url.startswith(assets_base)


def _needs_catchup_clause(model, assets_base: str):
    """_needs_catchup 的 SQL 版 —— 下推到 DB 端過濾 + LIMIT，避免每 10 分鐘
    撈全表進記憶體再 Python 過濾（表隨上傳單調成長）。舊縮圖遷移完 + 影片都有
    縮圖後，這個條件自然命中 0 筆 → 掃描退化成幾乎空查詢（一次性遷移自動退場）。"""
    from sqlalchemy import or_
    missing = or_(model.thumb_url.is_(None), model.thumb_url == "")
    if not assets_base:
        return missing                    # 圖床未設定 → 只補「完全沒縮圖」的
    # 有縮圖但不在圖床（舊落點）→ 也要遷移
    return or_(missing, model.thumb_url.notlike(f"{assets_base}%"))


async def _catchup_one(session, rec) -> bool:
    """補一筆。有變更並已寫回 → True。"""
    from core.drive_map import to_local_path
    from routers.crm.media_log import (_make_image_thumb, _make_video_thumb,
                                       _probe_duration)
    # stored_path 存 canonical UNC → 翻成本機視角（master 補算讀 NAS 原檔的關鍵）
    path = to_local_path(rec.stored_path or "")
    if not path or not await asyncio.to_thread(os.path.isfile, path):
        return False                      # 原檔不在（已刪/未掛載）→ 這輪跳過
    changed = False
    if rec.media_type == "video":
        if rec.duration_sec is None:
            d = await _probe_duration(path)
            if d is not None:
                rec.duration_sec = d
                changed = True
        thumb = await _make_video_thumb(path, rec.project_id, rec.id)
    else:
        thumb = await _make_image_thumb(path, rec.project_id, rec.id)
    if thumb and thumb != rec.thumb_url:
        rec.thumb_url = thumb
        changed = True
    return changed


async def run_media_log_catchup_once() -> dict:
    """跑一輪；回 {scanned, fixed}。給排程與手動觸發共用。"""
    from core.assets_host import assets_target
    from db.session import get_session_factory
    from db.models import ProjectMediaFile
    from sqlalchemy import select

    factory = get_session_factory()
    if factory is None:
        return {"scanned": 0, "fixed": 0, "skipped": "db offline"}
    _dir, assets_base = assets_target("medialog")
    fixed = 0
    async with factory() as session:
        todo = (await session.execute(
            select(ProjectMediaFile)
            .where(_needs_catchup_clause(ProjectMediaFile, assets_base))
            .limit(_BATCH)
        )).scalars().all()
        for rec in todo:
            try:
                if await _catchup_one(session, rec):
                    fixed += 1
            except Exception as e:      # 單筆失敗不擋整批（下一輪再試）
                logger.warning("[media_log catchup] %s 失敗: %s", rec.id, e)
        if fixed:
            await session.commit()
    return {"scanned": len(todo), "fixed": fixed}


# ── 上傳通知（原本在收檔端用行程內計時器，NAS 容器上是靜默失效）────────────
# 每專案記一個「已通知到哪個時間點」水位，存 DB 設定（master 重啟不重發）。
_NOTIFIED_KEY = "media_log.notified_until"


def _pending_by_project(rows, marks: dict, cutoff) -> dict:
    """{project_id: [尚未通知的檔]} — 只收「最後一筆已安靜超過 cutoff」的專案，
    還在連傳的專案本輪跳過（保留原本 digest 語意：拍攝中不轟炸群組）。
    純函式（不碰 DB/時鐘）→ 單元測試在 tests/unit/test_media_log_catchup.py。
    """
    buckets: dict = {}
    for r in rows:
        created = getattr(r, "created_at", None)
        if created is None:
            continue
        mark = marks.get(r.project_id)
        if mark and created <= mark:
            continue                       # 已通知過
        buckets.setdefault(r.project_id, []).append(r)
    return {pid: files for pid, files in buckets.items()
            if max(f.created_at for f in files) <= cutoff}


async def run_upload_notify_once() -> dict:
    """跑一輪上傳通知；回 {projects, files}。"""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from db.models import CrmProject, ProjectMediaFile
    from db.session import get_session_factory
    from routers.crm.media_log import NOTIFY_QUIET_SEC
    from services.website import settings_service

    factory = get_session_factory()
    if factory is None:
        return {"projects": 0, "files": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=NOTIFY_QUIET_SEC)
    async with factory() as session:
        all_settings = await settings_service.get_all_settings(session)
        raw = all_settings.get(_NOTIFIED_KEY) or {}
        marks = {}
        for pid, iso in raw.items():
            try:
                marks[pid] = datetime.fromisoformat(iso)
            except (TypeError, ValueError):
                pass
        # 只撈「已安靜超過 cutoff」的候選（連傳中的新檔本輪本就不發）—— 下推到
        # DB 端過濾，避免撈全表（表隨上傳成長）。已通知過的由 _pending_by_project
        # 依水位再剔。
        rows = (await session.execute(
            select(ProjectMediaFile)
            .where(ProjectMediaFile.created_at <= cutoff))).scalars().all()
        pending = _pending_by_project(rows, marks, cutoff)
        if not pending:
            return {"projects": 0, "files": 0}
        names = {p.id: (p.name or "") for p in (await session.execute(
            select(CrmProject).where(CrmProject.id.in_(list(pending))))).scalars()}

    sent = files = 0
    new_marks = dict(raw)
    for pid, group in pending.items():
        uploaders = sorted({(f.uploader_name or "").strip()
                            for f in group if (f.uploader_name or "").strip()})
        try:
            from notifier import notify_tab_async
            await notify_tab_async(
                "media_log_upload",
                project_name=names.get(pid, ""),
                file_count=len(group),
                uploaders="、".join(uploaders) or "未具名")
            sent += 1
            files += len(group)
        except Exception as e:
            logger.warning("[media_log notify] %s 發送失敗: %s", pid, e)
            continue                       # 水位不前進 → 下一輪重試
        new_marks[pid] = max(f.created_at for f in group).isoformat()

    if new_marks != raw:
        async with factory() as session:
            await settings_service.update_settings(
                session, {_NOTIFIED_KEY: new_marks}, updated_by="media_log")
            await session.commit()
    return {"projects": sent, "files": files}


async def _guarded(label: str, coro_fn, on_result) -> None:
    """跑一個 *_once 協程，CancelledError 傳遞、其餘吞掉記 warning，成功時
    交給 on_result 記 log。兩段工作（補算 / 通知）共用同一個錯誤隔離殼。"""
    try:
        on_result(await coro_fn())
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("[media_log %s] 這輪失敗: %s", label, e)


async def run_media_log_catchup() -> None:
    """常駐迴圈（master gate 內建 — 機隊與 dev 不跑，避免重複補算/重複通知）。"""
    from core.topology import is_master_machine
    if not is_master_machine():
        return
    await asyncio.sleep(_FIRST_DELAY_SEC)
    while True:
        await _guarded("catchup", run_media_log_catchup_once,
                       lambda r: r.get("fixed") and
                       logger.info("[media_log catchup] 補了 %s 筆縮圖/時長", r["fixed"]))
        await _guarded("notify", run_upload_notify_once,
                       lambda n: n.get("projects") and
                       logger.info("[media_log notify] %s 個專案 / %s 個檔",
                                   n["projects"], n["files"]))
        await asyncio.sleep(_INTERVAL_SEC)
