"""routers/website/admin_posts.py
---
管理端：部落格文章 + 文章分類 CRUD + Notion 匯入 + 強制 redirect 同步。

文章 CRUD: 標準 list/create/update/delete + on_change=mark_dirty
分類 CRUD: 同上
額外端點:
- POST /admin/posts/import-notion          觸發 Notion 抓 + DB upsert
- POST /admin/posts/redirects/sync          強制重生 nginx redirects.conf 推 NAS
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session, register_crud
from core.schemas_website import (
    PostCreate, PostUpdate,
    PostCategoryCreate, PostCategoryUpdate,
    NotionImportRequest, PostImportResult, RedirectSyncResult,
)
from services.website import notion_service, post_service, rebuild_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-posts"])


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

@router.post("/posts/redirects/sync", response_model=RedirectSyncResult)
async def force_sync_redirects(_session: AsyncSession = Depends(admin_session)):
    """admin 點「🔄 強制重新同步」時呼叫。

    通常 publish 流程會自動 sync；這個 endpoint 是當 admin 看到計數對不上時的自救用。
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
