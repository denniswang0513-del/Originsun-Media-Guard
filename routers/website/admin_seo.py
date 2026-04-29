"""routers/website/admin_seo.py
---
管理端：SEO 內容 CRUD（FAQ / Testimonial / QuickFact）。

對應前端「🔍 SEO / AI SEO 管理」子視圖的 Card 2/3/4。
任一寫入觸發 rebuild_service.mark_dirty()，60s debounce 後重 build 對外網站。

3 個實體共用同一套 list/create/update/delete 形狀 — 用 `_register_crud()` 工廠
集中註冊，避免 12 個 endpoint 重複貼。
"""
from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session
from core.schemas_website import (
    WebsiteFAQCreate, WebsiteFAQUpdate,
    WebsiteTestimonialCreate, WebsiteTestimonialUpdate,
    WebsiteQuickFactCreate, WebsiteQuickFactUpdate,
)
from services.website import rebuild_service, seo_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-seo"])


def _register_crud(
    *,
    prefix: str,
    name: str,
    list_fn: Callable[[AsyncSession], Awaitable[list]],
    create_fn: Callable[[AsyncSession, dict], Awaitable[dict]],
    update_fn: Callable[[AsyncSession, int, dict], Awaitable[dict | None]],
    delete_fn: Callable[[AsyncSession, int], Awaitable[bool]],
    create_schema: type[BaseModel],
    update_schema: type[BaseModel],
) -> None:
    """為 prefix 註冊 list/create/update/delete 4 個 endpoint。

    每個寫入操作後呼叫 mark_dirty 觸發 rebuild。
    """

    async def _list(session: AsyncSession = Depends(admin_session)):
        return {"items": await list_fn(session)}

    async def _create(req: create_schema, session: AsyncSession = Depends(admin_session)):  # type: ignore[valid-type]
        item = await create_fn(session, req.model_dump())
        await rebuild_service.mark_dirty()
        return item

    async def _update(item_id: int, req: update_schema, session: AsyncSession = Depends(admin_session)):  # type: ignore[valid-type]
        item = await update_fn(session, item_id, req.model_dump(exclude_unset=True))
        if not item:
            raise HTTPException(status_code=404, detail=f"{name} not found")
        await rebuild_service.mark_dirty()
        return item

    async def _delete(item_id: int, session: AsyncSession = Depends(admin_session)):
        if not await delete_fn(session, item_id):
            raise HTTPException(status_code=404, detail=f"{name} not found")
        await rebuild_service.mark_dirty()
        return {"ok": True}

    router.get(f"/{prefix}", name=f"list_{prefix}")(_list)
    router.post(f"/{prefix}", status_code=201, name=f"create_{prefix}")(_create)
    router.put(f"/{prefix}/{{item_id}}", name=f"update_{prefix}")(_update)
    router.delete(f"/{prefix}/{{item_id}}", name=f"delete_{prefix}")(_delete)


_register_crud(
    prefix="faqs", name="FAQ",
    list_fn=seo_service.list_faqs,
    create_fn=seo_service.create_faq,
    update_fn=seo_service.update_faq,
    delete_fn=seo_service.delete_faq,
    create_schema=WebsiteFAQCreate,
    update_schema=WebsiteFAQUpdate,
)

_register_crud(
    prefix="testimonials", name="Testimonial",
    list_fn=seo_service.list_testimonials,
    create_fn=seo_service.create_testimonial,
    update_fn=seo_service.update_testimonial,
    delete_fn=seo_service.delete_testimonial,
    create_schema=WebsiteTestimonialCreate,
    update_schema=WebsiteTestimonialUpdate,
)

_register_crud(
    prefix="quick_facts", name="Quick fact",
    list_fn=seo_service.list_quick_facts,
    create_fn=seo_service.create_quick_fact,
    update_fn=seo_service.update_quick_fact,
    delete_fn=seo_service.delete_quick_fact,
    create_schema=WebsiteQuickFactCreate,
    update_schema=WebsiteQuickFactUpdate,
)
