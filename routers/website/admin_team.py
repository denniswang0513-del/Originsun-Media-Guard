"""routers/website/admin_team.py
---
管理端：官網「團隊成員」顯示覆寫編輯（對應 about.js 的團隊卡）。

團隊成員的正本資料（name / role / photo_url / bio）在 CRM「人力資源」，
官網管理端**永不寫入**正本欄位；只編輯官網顯示覆寫欄位 + 顯示開關：
  - show_on_website     是否出現在官網團隊頁
  - website_title       官網顯示職稱（空 → fallback role）
  - website_photo_url   官網頭像（空 → fallback photo_url）
  - website_bio         官網簡介（空 → fallback bio）
  - website_sort_order  官網團隊頁排序

Endpoints (prefix `/api/website/admin`):
- GET /team    列出所有 crm_staff（含正本 name/role/photo_url/bio 作 reference + 覆寫值）
- PUT /team    批次更新 show_on_website + website_*（body: {"items": [...]}）

任一寫入觸發 rebuild_service.mark_dirty()，60s debounce 後重 build 對外網站（團隊是公開內容）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, UploadFile, File, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session
from core.schemas_website import TeamOverrideBatch
from services.website import rebuild_service
# 唯一可寫入 crm_staff 的欄位白名單（永不含正本 name/role/photo_url/bio）。與 CRM staff
# router 共用 db.models 的單一定義，避免兩處各維護一份而漂移。
from db.models import WEBSITE_TEAM_OVERRIDE_FIELDS as _WRITABLE_FIELDS

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-team"])


@router.get("/team")
async def list_team_admin(session: AsyncSession = Depends(admin_session)):
    """列出所有 crm_staff（不限 show_on_website），給官網管理端編輯。

    name / role / photo_url / bio 是 CRM 正本，前端唯讀顯示作 reference / placeholder。
    排序：website_sort_order（NULL 視為 0）, name。
    """
    from db.models import CrmStaff
    stmt = select(CrmStaff).order_by(
        func.coalesce(CrmStaff.website_sort_order, 0), CrmStaff.name
    )
    staff = list((await session.execute(stmt)).scalars())
    return {"items": [
        {
            "id": s.id,
            # CRM 正本（唯讀 reference）
            "name": s.name,
            "role": s.role,
            "photo_url": s.photo_url,
            "bio": s.bio,
            # 官網顯示開關 + 覆寫值（可編輯）
            "show_on_website": bool(s.show_on_website),
            "website_title": s.website_title,
            "website_photo_url": s.website_photo_url,
            "website_bio": s.website_bio,
            "website_sort_order": s.website_sort_order if s.website_sort_order is not None else 0,
        }
        for s in staff
    ]}


@router.put("/team")
async def update_team_admin(
    req: TeamOverrideBatch,
    request: Request,
    session: AsyncSession = Depends(admin_session),
):
    """批次更新 show_on_website + website_*；**永不寫入** 正本 name/role/photo_url/bio。

    只更新 body 帶到的既有員工 id；找不到的 id 略過（不報錯）。
    更新後 mark_dirty → rebuild（團隊是公開內容）。
    """
    from db.models import CrmStaff

    updated = 0
    for item in req.items:
        patch = item.model_dump(exclude_unset=True)
        patch.pop("id", None)
        # 防禦：只保留白名單欄位（即使 schema 已限制，仍二次過濾免將來擴 schema 漏網）
        patch = {k: v for k, v in patch.items() if k in _WRITABLE_FIELDS}
        if not patch:
            continue
        staff = await session.get(CrmStaff, item.id)
        if staff is None:
            continue
        for field, value in patch.items():
            setattr(staff, field, value)
        updated += 1

    await session.commit()
    await rebuild_service.mark_dirty()
    return {"ok": True, "updated": updated}


@router.post("/team/{staff_id}/upload-photo")
async def upload_team_photo(
    staff_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(admin_session),
):
    """上傳官網團隊頭像 → /uploads/team/{staff_id}/{uuid}.webp，寫入 website_photo_url + rebuild。

    複用 admin_posts 的 WebP 轉檔 + 上傳路徑（NAS container /app/uploads/，由 NAS Nginx
    serve /uploads/）。只動官網覆寫欄位 website_photo_url，永不碰 CRM 正本 photo_url。
    """
    import os
    import uuid as _uuid
    from db.models import CrmStaff
    from .admin_posts import _save_image_as_webp, _UPLOAD_BASE, _ALLOWED_IMG_EXT

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext and ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=415, detail=f"不支援的圖片格式：{ext}")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空檔案")
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="圖片過大（上限 10MB）")

    staff = await session.get(CrmStaff, staff_id)
    if staff is None:
        raise HTTPException(status_code=404, detail="找不到員工")

    upload_dir = os.path.join(_UPLOAD_BASE, "team", staff_id)
    os.makedirs(upload_dir, exist_ok=True)
    saved = _save_image_as_webp(content, upload_dir, _uuid.uuid4().hex)
    rel = os.path.relpath(saved, _UPLOAD_BASE).replace("\\", "/")
    url = f"/uploads/{rel}"

    staff.website_photo_url = url
    await session.commit()
    await rebuild_service.mark_dirty()
    return {"url": url}
