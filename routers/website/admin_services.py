"""routers/website/admin_services.py
---
管理端：服務項目 CRUD。

Endpoints (prefix `/api/website/admin`):
- GET    /services              列出（含隱藏）
- POST   /services              新增
- PUT    /services/{id}         更新
- DELETE /services/{id}         刪除
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ._common import check_admin, get_factory, require_db
from core.schemas_website import ServiceCreate, ServiceUpdate
from services.website import service_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-services"])


@router.get("/services")
async def list_admin_services(request: Request):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        items = await service_service.list_admin_services(session)
    return {"items": items}


@router.post("/services", status_code=201)
async def create_service(req: ServiceCreate, request: Request):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        try:
            item = await service_service.create_service(session, req.model_dump())
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Create failed: {e}")
    return item


@router.put("/services/{service_id}")
async def update_service(service_id: int, req: ServiceUpdate, request: Request):
    check_admin(request)
    require_db()
    updates = req.model_dump(exclude_none=True)
    factory = await get_factory()
    async with factory() as session:
        item = await service_service.update_service(session, service_id, updates)
    if not item:
        raise HTTPException(status_code=404, detail="Service not found")
    return item


@router.delete("/services/{service_id}")
async def delete_service(service_id: int, request: Request):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        ok = await service_service.delete_service(session, service_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Service not found")
    return {"ok": True}
