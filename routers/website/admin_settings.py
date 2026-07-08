"""routers/website/admin_settings.py
---
管理端：website_settings key-value 批次讀寫。

Endpoints (prefix `/api/website/admin`):
- GET /settings    回傳所有 key-value（dict）
- PUT /settings    批次 upsert（body: {"values": {"k1": v1, ...}}）
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session, current_username
from core.schemas_website import SettingUpdate
from services.website import rebuild_service, settings_service

# 影響對外 HTML / 公開 endpoint 的 setting key 才要 mark_dirty 觸發 rebuild。
# Notion token / 內部通知設定改了不需要重 build 對外網站。
# - copy.*  頁面行銷文案覆寫（Phase 2 各頁 + Phase 3 footer/contact 表單欄位）
# - forms.* 聯絡表單選項清單（Phase 3 service_types / budget_ranges）
# - home.*  首頁 hero / showreel / stats / rating（admin「🏠 首頁設定」）
# - portfolio.* /portfolio 頁 PDF 連結
_PUBLIC_AFFECTING_PREFIXES = (
    "company.", "social.", "seo.", "about.", "turnstile.",
    "copy.", "forms.", "home.", "portfolio.", "brand.",
)
# analytics.* 多數只影響後台儀表板（property_id / service_account / dashboard 設定），
# 不該重建對外站。唯有 GA4 gtag 追蹤 ID 會烤進每頁 <head> → 只有它要 rebuild。
_PUBLIC_AFFECTING_KEYS = ("analytics.ga_measurement_id",)

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
    if any(k.startswith(_PUBLIC_AFFECTING_PREFIXES) or k in _PUBLIC_AFFECTING_KEYS
           for k in req.values):
        await rebuild_service.mark_dirty()
    return {"ok": True, "updated": count}


@router.post("/brand/upload")
async def upload_brand_image(
    request: Request,
    kind: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(admin_session),
):
    """上傳品牌 Logo / favicon → /uploads/brand/{kind}-{uuid}，寫 brand.{kind}_url + rebuild。

    kind ∈ {logo, favicon}。SVG/ICO 原樣保存（保留向量 / favicon 格式）；其餘轉 WebP。
    """
    import os
    import uuid as _uuid
    from .admin_posts import _save_image_as_webp, _UPLOAD_BASE, _ALLOWED_IMG_EXT

    if kind not in ("logo", "favicon"):
        raise HTTPException(status_code=400, detail="kind 須為 logo 或 favicon")
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed = _ALLOWED_IMG_EXT | {".svg", ".ico"}
    if ext and ext not in allowed:
        raise HTTPException(status_code=415, detail=f"不支援的格式：{ext}")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="空檔案")
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="圖片過大（上限 5MB）")

    upload_dir = os.path.join(_UPLOAD_BASE, "brand")
    os.makedirs(upload_dir, exist_ok=True)
    if ext in (".svg", ".ico"):
        fname = f"{kind}-{_uuid.uuid4().hex}{ext}"
        saved = os.path.join(upload_dir, fname)
        with open(saved, "wb") as f:
            f.write(content)
    else:
        saved = _save_image_as_webp(content, upload_dir, f"{kind}-{_uuid.uuid4().hex}")
    rel = os.path.relpath(saved, _UPLOAD_BASE).replace("\\", "/")
    url = f"/uploads/{rel}"

    await settings_service.update_settings(
        session, {f"brand.{kind}_url": url}, updated_by=current_username(request)
    )
    await rebuild_service.mark_dirty()
    return {"url": url}
