"""services/timesheet_puller.py — 主控端定時拉工時 Google Sheet（取代 Apps Script 推）。

owner 2026-09-03：「可以寫一個東西定期向 Google Sheet 拉資料就好了嗎？那個連結是公開連結」
—— 可以。整本 xlsx 用公開連結 export 拿得到（助理分頁的 IMPORTRANGE 值也在裡面），
每次拉全表、走同一條 ingest（row_hash 去重），重疊安全、不用 marker、不用碰試算表那邊。

設定在 settings.json `timesheet.pull`（services.timesheet_settings；dev／生產各自一份檔案）：
    enabled   bool   預設 false —— 生產由 owner 打開（PUT /api/v1/timesheets/pull）
    sheet_id  str    試算表 id（網址 /d/<id>/ 那段）
    cron      str    預設每週六 09:00
    last_run_at / last_summary   runner 狀態

只在 master 跑（core.topology.is_master_machine）：全機隊共用同一顆 mediaguard DB，
沒 gate 每台 agent 都會各拉一份寫同一張表。dev checkout 的 gate 也回 False —— dev 要測
用 POST /pull（force）手動觸發。
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from core.schemas import TimesheetRow
from services.timesheet_ingest import ingest, ingest_context
from services.timesheet_settings import SettingsBlock
from services.timesheet_sheet import fetch_xlsx, load_workbook, read_rows

logger = logging.getLogger(__name__)

DEFAULT_CRON = "0 9 * * 6"     # 每週六 09:00（owner 2026-09-03：總表為準，一週拉一次就好）
_BATCH = 500                     # 一個交易幾列（9,800 列全表 ≈ 20 個交易）
_run_lock = asyncio.Lock()
_scheduler_task: Optional[asyncio.Task] = None
settings = SettingsBlock("pull", settable=("enabled", "sheet_id", "cron"),
                         defaults={"enabled": False, "sheet_id": "", "cron": DEFAULT_CRON})


def get_pull_settings() -> dict:
    return {**settings.get(), "running": _run_lock.locked()}


def update_pull_settings(patch: dict) -> dict:
    settings.update(patch)
    return get_pull_settings()


async def run_pull(force: bool = False) -> dict:
    """抓整本 → 「總表」→ ingest（source=sheet）。回合計；同時只跑一份。"""
    cfg = settings.get()
    if not cfg["enabled"] and not force:
        return {"status": "disabled"}
    if not cfg["sheet_id"]:
        return {"status": "error", "message": "沒設 sheet_id（試算表網址 /d/<id>/ 那段）"}
    if _run_lock.locked():
        return {"status": "running"}
    async with _run_lock:
        t0 = time.time()
        try:
            data = await asyncio.to_thread(fetch_xlsx, cfg["sheet_id"])
            good, bad = await asyncio.to_thread(lambda: read_rows(load_workbook(data)))
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory is None:
                return {"status": "error", "message": "資料庫離線"}
            tot = {"inserted": 0, "skipped": 0, "skipped_manual_priority": 0, "skipped_deleted": 0,
                   "conflicts": 0, "skipped_conflict": 0}
            sets = {k: set() for k in ("ambiguous_projects", "unmatched_projects",
                                       "staff_ambiguous", "staff_unmatched")}
            async with factory() as session:      # 查表建一次，20 批共用
                ctx = await ingest_context(session, (x["staff"] for x in good))
            for i in range(0, len(good), _BATCH):
                chunk = [TimesheetRow(**x) for x in good[i:i + _BATCH]]
                async with factory() as session:
                    r = await ingest(session, chunk, "sheet", ctx)
                for k in tot:
                    tot[k] += r.get(k, 0)
                for k in sets:
                    sets[k] |= set(r.get(k, []))
            out = {"status": "ok", "rows": len(good), "bad_rows": len(bad), **tot,
                   **{k: sorted(v) for k, v in sets.items()},
                   "seconds": round(time.time() - t0, 1)}
            summary = (f"{datetime.now():%m/%d %H:%M} 拉 {len(good)} 列：新增 {tot['inserted']}、"
                       f"重複 {tot['skipped']}、總表已刪 {tot['skipped_deleted']}、壞列 {len(bad)}；撞案 {len(sets['ambiguous_projects'])}、"
                       f"找不到 {len(sets['unmatched_projects'])}"
                       + (f"；⚠ 衝突 {tot['conflicts']}（到總表決定）" if tot["conflicts"] else ""))
            settings.mark(last_run_at=time.time(), last_summary=summary)
            logger.info("[timesheet_puller] %s", summary)
            if tot["conflicts"]:
                try:                                   # 提醒 owner 去總表選；沒 webhook 就靜靜略過
                    from notifier import notify_tab_async
                    await notify_tab_async("timesheet_conflict", count=tot["conflicts"])
                except Exception:
                    logger.exception("[timesheet_puller] 衝突通知失敗")
            return out
        except Exception as e:
            logger.exception("[timesheet_puller] 拉取失敗")
            settings.mark(last_run_at=time.time(), last_summary=f"{datetime.now():%m/%d %H:%M} 失敗：{e}")
            return {"status": "error", "message": str(e)}


async def _scheduler_loop() -> None:
    """每 60 秒看 cron 到期沒 → run_pull。gate：只在生產 master 跑。"""
    from core.topology import is_master_machine
    from services.website._runner_util import cron_due
    if not is_master_machine():
        logger.info("[timesheet_puller] 非 master（或 dev）— 工時拉取排程不啟動；手動 POST /pull 仍可用")
        return
    await asyncio.sleep(62)       # 錯開其他 runner 的啟動檢查
    while True:
        try:
            cfg = settings.get()
            # 首次啟用：立刻拉一次（沒有額度顧慮；資料本來就該在）
            if cfg["enabled"] and cfg["sheet_id"] and (cfg["last_run_at"] <= 0 or cron_due(cfg["cron"], cfg["last_run_at"])):
                await run_pull()
        except Exception:
            logger.exception("[timesheet_puller] scheduler loop 異常")
        await asyncio.sleep(60)


def start_scheduler_task() -> None:
    """main.py startup 呼叫一次 — 啟動背景 cron loop（gate 在 loop 裡）。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
