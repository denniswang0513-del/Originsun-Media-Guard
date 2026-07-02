"""routers/website/admin_initiatives.py
---
管理端：公益合作 / 創作計畫 案例 CRUD（prefix /api/website/admin）。

list/create/update/delete 走 register_crud；list 回傳含連動作品標題（顯示用）。
任一寫入 mark_dirty → 對外網站重建。公開讀取在 public.py 的 /initiatives。
"""
from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session, register_crud
from core.schemas_website import WebsiteInitiativeCreate, WebsiteInitiativeUpdate
from routers.website.admin_posts import _ALLOWED_IMG_EXT, _UPLOAD_BASE, _save_image_as_webp
from services.website import initiative_service, rebuild_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-initiatives"])


@router.post("/initiatives/upload-cover")
async def upload_initiative_cover(
    file: UploadFile = File(...),
    _session: AsyncSession = Depends(admin_session),
):
    """上傳獨立案例封面 → /uploads/initiatives/{uuid}.webp（自動轉 WebP）。"""
    ext = (os.path.splitext(file.filename or "image.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="圖片大於 10MB")
    upload_dir = os.path.join(_UPLOAD_BASE, "initiatives")
    os.makedirs(upload_dir, exist_ok=True)
    saved = _save_image_as_webp(content, upload_dir, uuid.uuid4().hex[:12])
    rel = os.path.relpath(saved, _UPLOAD_BASE).replace("\\", "/")
    return {"url": f"/uploads/{rel}"}

register_crud(
    router,
    prefix="initiatives", name="Initiative",
    list_fn=initiative_service.list_admin,
    create_fn=initiative_service.create_initiative,
    update_fn=initiative_service.update_initiative,
    delete_fn=initiative_service.delete_initiative,
    create_schema=WebsiteInitiativeCreate,
    update_schema=WebsiteInitiativeUpdate,
    on_change=rebuild_service.mark_dirty,
)
