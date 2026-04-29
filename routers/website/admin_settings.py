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
from services.website import rebuild_service, settings_service

# 影響對外 HTML / 公開 endpoint 的 setting key 才要 mark_dirty 觸發 rebuild。
# Notion token / 內部通知設定改了不需要重 build 對外網站。
_PUBLIC_AFFECTING_PREFIXES = (
    "company.", "social.", "seo.", "about.", "turnstile.",
)

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
    # 影響對外 HTML / 公開資料的 settings 改了 → 排定 rebuild
    if any(k.startswith(_PUBLIC_AFFECTING_PREFIXES) for k in req.values):
        await rebuild_service.mark_dirty()
    return {"ok": True, "updated": count}
