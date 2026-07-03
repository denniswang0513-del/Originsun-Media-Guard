"""routers/website/admin_backup.py
─────────────────────────────────────
資料備份 runner 的 admin + internal relay endpoints（對齊 admin_translation）。

admin（admin_session）：
- GET/PUT /backup/settings     排程 + Drive 資料夾 + 保留份數 + Drive 授權狀態
- POST   /backup/run           立即備份一次（NAS 上 → relay master）

internal（X-Internal-Key，NAS forward 給 master 實跑）：
- POST   /internal/backup/run
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session, current_username, _check_internal_key
from services.website import backup_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-backup"])


@router.get("/backup/settings")
async def backup_get_settings(session: AsyncSession = Depends(admin_session)):
    return await backup_service.get_runner_settings(session)


@router.put("/backup/settings")
async def backup_update_settings(request: Request, session: AsyncSession = Depends(admin_session)):
    body = await request.json()
    try:
        return await backup_service.update_runner_settings(
            session, body, by=current_username(request))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/backup/run")
async def backup_run_now(session: AsyncSession = Depends(admin_session)):
    return await backup_service.trigger_backup(session)


# ── internal relay（NAS → master）──

@router.post("/internal/backup/run")
async def backup_run_internal(request: Request):
    _check_internal_key(request)
    if not backup_service.is_master():
        return {"status": "error", "error": "internal target 非 master（無 SSH key）"}
    if not backup_service.start_backup_bg():
        return {"status": "busy"}
    return {"status": "started"}
