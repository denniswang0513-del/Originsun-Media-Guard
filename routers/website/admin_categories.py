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

from fastapi import APIRouter, HTTPException, Request

from ._common import check_admin, get_factory, require_db
from core.schemas_website import CategoryCreate, CategoryReorder, CategoryUpdate
from services.website import category_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-categories"])


@router.get("/categories")
async def list_admin_categories(request: Request):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        items = await category_service.list_admin_categories(session)
    return {"items": items}


@router.post("/categories", status_code=201)
async def create_category(req: CategoryCreate, request: Request):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        try:
            item = await category_service.create_category(session, req.model_dump())
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Create failed: {e}")
    return item


@router.put("/categories/{category_id}")
async def update_category(category_id: int, req: CategoryUpdate, request: Request):
    check_admin(request)
    require_db()
    updates = req.model_dump(exclude_none=True)
    factory = await get_factory()
    async with factory() as session:
        item = await category_service.update_category(session, category_id, updates)
    if not item:
        raise HTTPException(status_code=404, detail="Category not found")
    return item


@router.delete("/categories/{category_id}")
async def delete_category(category_id: int, request: Request):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        ok = await category_service.delete_category(session, category_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Category not found")
    return {"ok": True}


@router.post("/categories/reorder")
async def reorder_categories(req: CategoryReorder, request: Request):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        await category_service.reorder_categories(session, req.order)
    return {"ok": True, "count": len(req.order)}
