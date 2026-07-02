"""routers/website/admin_translation.py
---
英文翻譯（transcreation）runner 的 admin + internal relay endpoints（對齊 admin_seo 的 SEO runner）。

admin（走 admin_session）：
- GET  /translation/audit                所有可翻實體 + 狀態（missing/stale/translated/approved）
- GET/PUT /translation/settings          排程 + 品牌調性 + 術語表 + 自動核准
- POST /translation/run                  「立即翻譯」整批背景
- POST /translation/{etype}/{eid}/generate   單實體生成建議（不存）
- POST /translation/{etype}/{eid}/apply      套用人工確認/編輯後的 _en（存 + 標 approved）

internal（X-Internal-Key，NAS website-api forward 給 master 跑 claude）：
- POST /internal/translation/run
- POST /internal/translation/{etype}/{eid}/generate
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import (
    admin_session, current_username, _check_internal_key, _admin_session_for_internal,
)
from services.website import translation_service, rebuild_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-translation"])

_ENTITY_TYPES = {"work", "post", "service"}


@router.get("/translation/audit")
async def translation_audit(session: AsyncSession = Depends(admin_session)):
    return {"items": await translation_service.list_audit(session)}


@router.get("/translation/settings")
async def translation_get_settings(session: AsyncSession = Depends(admin_session)):
    return await translation_service.get_runner_settings(session)


@router.put("/translation/settings")
async def translation_update_settings(request: Request, session: AsyncSession = Depends(admin_session)):
    body = await request.json()
    try:
        return await translation_service.update_runner_settings(
            session, body, by=current_username(request))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/translation/run")
async def translation_run_now(session: AsyncSession = Depends(admin_session)):
    settings = await translation_service.get_runner_settings(session)
    return await translation_service.trigger_batch(session, batch_size=settings["batch_size"])


@router.post("/translation/{etype}/{eid}/generate")
async def translation_generate(etype: str, eid: str, session: AsyncSession = Depends(admin_session)):
    if etype not in _ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="unknown entity type")
    return await translation_service.generate_for_entity(session, etype, eid)


@router.post("/translation/{etype}/{eid}/apply")
async def translation_apply(etype: str, eid: str, request: Request,
                            session: AsyncSession = Depends(admin_session)):
    """存人工確認/編輯後的 _en 並標記 approved（fields = {"<field>_en": ...}）。"""
    if etype not in _ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="unknown entity type")
    body = await request.json()
    fields = body.get("fields") or {}
    obj = await translation_service._get_entity(session, etype, eid)
    if obj is None:
        raise HTTPException(status_code=404, detail="實體不存在")
    src_hash = body.get("source_hash") or translation_service._source_hash(etype, obj)
    await translation_service._writeback(
        session, etype, eid, fields, src_hash, approve=True, by=current_username(request))
    await rebuild_service.mark_dirty()
    return {"ok": True}


# ── internal relay（NAS → master；X-Internal-Key）──

@router.post("/internal/translation/run")
async def translation_run_internal(request: Request):
    _check_internal_key(request)
    try:
        batch_size = int(request.query_params.get("batch_size", 10) or 10)
    except ValueError:
        batch_size = 10
    if not translation_service.start_batch_bg(batch_size):
        return {"status": "busy"}
    return {"status": "started"}


@router.post("/internal/translation/{etype}/{eid}/generate")
async def translation_generate_internal(etype: str, eid: str, request: Request):
    _check_internal_key(request)
    if etype not in _ENTITY_TYPES:
        raise HTTPException(status_code=400, detail="unknown entity type")
    async with _admin_session_for_internal() as session:
        return await translation_service._generate_local(session, etype, eid)
