"""routers/website/admin_settings.py
---
管理端：website_settings key-value 批次讀寫。

Endpoints (prefix `/api/website/admin`):
- GET /settings    回傳所有 key-value（dict）
- PUT /settings    批次 upsert（body: {"values": {"k1": v1, ...}}）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session, current_username
from core.schemas_website import SettingUpdate
from services.website import settings_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-settings"])


@router.get("/settings")
async def get_settings(session: AsyncSession = Depends(admin_session)):
    return {"settings": await settings_service.get_all_settings(session)}


@router.put("/settings")
async def update_settings(
    req: SettingUpdate,
    request: Request,
    session: AsyncSession = Depends(admin_session),
):
    count = await settings_service.update_settings(
        session, req.values, updated_by=current_username(request)
    )
    return {"ok": True, "updated": count}
