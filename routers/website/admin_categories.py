"""routers/website/admin_categories.py
---
管理端：作品分類 CRUD + 拖曳排序。

Endpoints (prefix `/api/website/admin`):
- GET    /categories            列出（含全部作品計數）
- POST   /categories            新增
- PUT    /categories/{id}       更新
- DELETE /categories/{id}       刪除（連帶清除多對多關聯）
- POST   /categories/reorder    批次重排
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session
from core.schemas_website import CategoryCreate, CategoryReorder, CategoryUpdate
from services.website import category_service, rebuild_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-categories"])


@router.get("/categories")
async def list_admin_categories(session: AsyncSession = Depends(admin_session)):
    items = await category_service.list_admin_categories(session)
    return {"items": items}


@router.post("/categories", status_code=201)
async def create_category(
    req: CategoryCreate,
    session: AsyncSession = Depends(admin_session),
):
    try:
        result = await category_service.create_category(session, req.model_dump())
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"Slug '{req.slug}' already exists")
    await rebuild_service.mark_dirty()
    return result


@router.put("/categories/{category_id}")
async def update_category(
    category_id: int,
    req: CategoryUpdate,
    session: AsyncSession = Depends(admin_session),
):
    item = await category_service.update_category(
        session, category_id, req.model_dump(exclude_none=True)
    )
    if not item:
        raise HTTPException(status_code=404, detail="Category not found")
    await rebuild_service.mark_dirty()
    return item


@router.delete("/categories/{category_id}")
async def delete_category(
    category_id: int,
    session: AsyncSession = Depends(admin_session),
):
    ok = await category_service.delete_category(session, category_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Category not found")
    await rebuild_service.mark_dirty()
    return {"ok": True}


@router.post("/categories/reorder")
async def reorder_categories(
    req: CategoryReorder,
    session: AsyncSession = Depends(admin_session),
):
    await category_service.reorder_categories(session, req.order)
    await rebuild_service.mark_dirty()
    return {"ok": True, "count": len(req.order)}
