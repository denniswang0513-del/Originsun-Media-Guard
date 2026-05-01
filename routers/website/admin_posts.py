"""routers/website/admin_posts.py
---
管理端：部落格文章 + 文章分類 CRUD + Notion 匯入 + 強制 redirect 同步 + 圖片上傳。

文章 CRUD: 標準 list/create/update/delete + on_change=mark_dirty
分類 CRUD: 同上
額外端點:
- POST /admin/posts/import-notion          觸發 Notion 抓 + DB upsert
- POST /admin/redirects/sync               強制重生 nginx redirects.conf 推 NAS（posts+works）
- POST /admin/posts/redirects/sync         (Deprecated) 同上之 alias
- POST /admin/posts/{id}/upload-image      上傳 block 內 image，自動 WebP 轉檔
"""
from __future__ import annotations

import io
import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session, register_crud
from core.schemas_website import (
    PostCreate, PostUpdate,
    PostCategoryCreate, PostCategoryUpdate,
    NotionImportRequest, PostImportResult, RedirectSyncResult,
)
from db.models_website import WebsitePost
from services.website import notion_service, post_service, rebuild_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-posts"])

# 上傳基礎路徑（NAS container：/app/uploads/，host：/share/.../Website/uploads/）
_UPLOAD_BASE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "uploads",
)
_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}


def _save_image_as_webp(content: bytes, dest_dir: str, base_name: str) -> str:
    """寫圖檔到 dest_dir/<base_name>.webp，原始格式自動轉 WebP（quality 82）。

    Pillow 不可用 / 開檔失敗 → fallback 寫原 bytes 到 .bin（罕見）。
    """
    webp_path = os.path.join(dest_dir, base_name + ".webp")
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(content))
        if img.mode in ("RGBA", "LA"):
            img = img.convert("RGBA")
        else:
            img = img.convert("RGB")
        img.save(webp_path, "WEBP", quality=82, method=6)
        return webp_path
    except Exception as e:
        logger.warning("[upload] WebP convert failed (%s) — fallback raw", e)
        raw_path = webp_path.replace(".webp", ".bin")
        with open(raw_path, "wb") as f:
            f.write(content)
        return raw_path


# ── 標準 CRUD: posts ──

# update_post / create_post 接 auto_create_categories=True 是給 admin 自由打 slug 用；
# 包成 lambda 才能跟 register_crud 的 (session, data) 簽名相容。
async def _create_post(session: AsyncSession, data: dict) -> dict:
    return await post_service.create_post(session, data, auto_create_categories=True)


async def _update_post(session: AsyncSession, post_id: int, data: dict):
    return await post_service.update_post(session, post_id, data, auto_create_categories=True)


# 文章列表預設不過濾，admin Tab 會用 query string 自行 filter
async def _list_posts(session: AsyncSession) -> list[dict]:
    items, _total = await post_service.list_posts(session, limit=500)
    return items


register_crud(
    router,
    prefix="posts", name="Post",
    list_fn=_list_posts,
    create_fn=_create_post,
    update_fn=_update_post,
    delete_fn=post_service.delete_post,
    create_schema=PostCreate,
    update_schema=PostUpdate,
    on_change=rebuild_service.mark_dirty,
)


# ── 標準 CRUD: post categories ──

register_crud(
    router,
    prefix="post_categories", name="Post category",
    list_fn=post_service.list_categories,
    create_fn=post_service.create_category,
    update_fn=post_service.update_category,
    delete_fn=post_service.delete_category,
    create_schema=PostCategoryCreate,
    update_schema=PostCategoryUpdate,
    on_change=rebuild_service.mark_dirty,
)


# ── Notion 匯入 ──

@router.post("/posts/import-notion", response_model=PostImportResult)
async def import_from_notion(
    req: NotionImportRequest,
    session: AsyncSession = Depends(admin_session),
):
    """從 Notion 抓文章 → upsert 進 DB。

    預設行為（force=False）：
    - 已存在 notion_page_id 的文章跳過（DB 為真）
    - 新文章插入為 status='draft'，admin 必須過目才上線

    force=True：
    - 已存在的整篇覆寫 metadata + body（保留 admin 設的 status / old_urls / SEO）
    """
    try:
        # notion_service 抓 Notion → 回 raw posts + categories（不寫檔，只回 dict）
        notion_data = await notion_service.fetch_from_notion()
    except Exception as e:
        logger.exception("[notion-import] fetch failed")
        raise HTTPException(status_code=502, detail=f"Notion 抓取失敗：{e}")

    result = await post_service.upsert_from_notion(
        session,
        notion_posts=notion_data["posts"],
        notion_categories=notion_data["categories"],
        force=req.force,
    )

    if result["inserted"] or result["overwritten"]:
        await rebuild_service.mark_dirty()

    return result


# ── 強制重新同步 redirects 到 NAS nginx ──
# /admin/redirects/sync 是新統一名（含 posts + works）。/admin/posts/redirects/sync
# 是舊 alias 保留 backward compat（既有部署的 admin Tab 還在用）。

async def _force_sync_redirects_impl():
    """admin 點「🔄 強制重新同步」時的共用邏輯。

    通常 publish 流程會自動 sync；這個 endpoint 是當 admin 看到計數對不上時的自救用。
    /api/website/redirects 已 merge posts + works，sync 動作影響全部 namespace。
    """
    from datetime import datetime, timezone
    try:
        from publish_update import sync_redirects_to_nas
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="sync_redirects_to_nas 不可用（NAS container 沒這個函式，要從 master 觸發）",
        )

    ok = sync_redirects_to_nas()
    return RedirectSyncResult(
        synced=0,
        last_sync=datetime.now(timezone.utc) if ok else None,
        method="nginx-hard-301",
        ok=ok,
        error=None if ok else "sync 失敗，看 master log",
    )


@router.post("/redirects/sync", response_model=RedirectSyncResult)
async def force_sync_redirects(_session: AsyncSession = Depends(admin_session)):
    """同步 posts + works 全部 redirects 到 NAS nginx。"""
    return await _force_sync_redirects_impl()


@router.post("/posts/redirects/sync", response_model=RedirectSyncResult, deprecated=True)
async def force_sync_redirects_legacy(_session: AsyncSession = Depends(admin_session)):
    """[Deprecated] 用 /admin/redirects/sync — 此 alias 保留 backward compat。"""
    return await _force_sync_redirects_impl()


# ── 單一文章詳情（含完整 body）— 編輯 Modal 開啟時用 ──

@router.get("/posts/{post_id}")
async def get_post_detail(
    post_id: int,
    session: AsyncSession = Depends(admin_session),
):
    item = await post_service.get_post(session, post_id)
    if not item:
        raise HTTPException(status_code=404, detail="文章不存在")
    return item


# ── 圖片上傳（block 內 image block 用） ──

@router.post("/posts/{post_id}/upload-image")
async def upload_post_image(
    post_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(admin_session),
):
    """上傳 block 內圖片到 /uploads/posts/{post_id}/{uuid}.webp。

    - 自動 WebP 轉檔（PIL）— 比 JPG 小 30%，比 PNG 小 50-70%
    - 不檢查 post 是否已 publish（draft 也能上傳）
    - 回傳的 url 可直接塞進 image block 的 src 欄位
    - 不寫 DB（只寫檔），post.body 由 admin 編輯時 PUT 更新
    """
    post = await session.get(WebsitePost, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="文章不存在")

    ext = (os.path.splitext(file.filename or "image.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")

    upload_dir = os.path.join(_UPLOAD_BASE, "posts", str(post_id))
    os.makedirs(upload_dir, exist_ok=True)
    base_name = uuid.uuid4().hex[:12]
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="圖片大於 10MB")

    saved = _save_image_as_webp(content, upload_dir, base_name)
    rel = os.path.relpath(saved, _UPLOAD_BASE).replace("\\", "/")
    return {"url": f"/uploads/{rel}", "filename": os.path.basename(saved)}
