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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session
from core.schemas_website import ServiceCreate, ServiceUpdate
from services.website import service_service, rebuild_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-services"])


@router.get("/services")
async def list_admin_services(session: AsyncSession = Depends(admin_session)):
    items = await service_service.list_admin_services(session)
    return {"items": items}


@router.post("/services", status_code=201)
async def create_service(
    req: ServiceCreate,
    session: AsyncSession = Depends(admin_session),
):
    try:
        item = await service_service.create_service(session, req.model_dump())
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"Slug '{req.slug}' already exists")
    await rebuild_service.mark_dirty()
    return item


@router.put("/services/{service_id}")
async def update_service(
    service_id: int,
    req: ServiceUpdate,
    session: AsyncSession = Depends(admin_session),
):
    item = await service_service.update_service(
        session, service_id, req.model_dump(exclude_none=True)
    )
    if not item:
        raise HTTPException(status_code=404, detail="Service not found")
    await rebuild_service.mark_dirty()
    return item


@router.delete("/services/{service_id}")
async def delete_service(
    service_id: int,
    session: AsyncSession = Depends(admin_session),
):
    ok = await service_service.delete_service(session, service_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Service not found")
    await rebuild_service.mark_dirty()
    return {"ok": True}
