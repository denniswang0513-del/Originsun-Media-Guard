"""routers/website/admin_settings.py
---
管理端：website_settings key-value 批次讀寫。

Endpoints (prefix `/api/website/admin`):
- GET /settings    回傳所有 key-value（dict）
- PUT /settings    批次 upsert（body: {"values": {"k1": v1, ...}}）
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ._common import check_admin, current_username, get_factory, require_db
from core.schemas_website import SettingUpdate
from services.website import settings_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-settings"])


@router.get("/settings")
async def get_settings(request: Request):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        data = await settings_service.get_all_settings(session)
    return {"settings": data}


@router.put("/settings")
async def update_settings(req: SettingUpdate, request: Request):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        count = await settings_service.update_settings(
            session, req.values, updated_by=current_username(request)
        )
    return {"ok": True, "updated": count}
