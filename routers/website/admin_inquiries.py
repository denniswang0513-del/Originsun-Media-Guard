"""routers/website/admin_inquiries.py
---
管理端：聯絡表單收件箱 + 狀態管理 + 轉 CRM client。

Endpoints (prefix `/api/website/admin`):
- GET    /inquiries                  列出（可 ?status=new 篩選 + 分頁）
- GET    /inquiries/{id}             單筆詳情
- PUT    /inquiries/{id}             更新 status/notes
- DELETE /inquiries/{id}             刪除
- POST   /inquiries/{id}/convert     轉為 CRM clients（建立新 Client + 標記 converted）
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ._common import check_admin, current_username, get_factory, require_db
from core.schemas_website import ContactInquiryConvert, ContactInquiryUpdate
from services.website import inquiry_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-inquiries"])


@router.get("/inquiries")
async def list_inquiries(
    request: Request,
    status: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        items, total = await inquiry_service.list_inquiries(
            session, status=status, page=page, limit=limit
        )
    return {"items": items, "total": total, "page": page, "limit": limit}


@router.get("/inquiries/{inquiry_id}")
async def get_inquiry(inquiry_id: int, request: Request):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        inq = await inquiry_service.get_inquiry(session, inquiry_id)
    if not inq:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    return inq


@router.put("/inquiries/{inquiry_id}")
async def update_inquiry(inquiry_id: int, req: ContactInquiryUpdate, request: Request):
    check_admin(request)
    require_db()
    updates = req.model_dump(exclude_none=True)
    factory = await get_factory()
    async with factory() as session:
        inq = await inquiry_service.update_inquiry(
            session, inquiry_id, updates, handled_by=current_username(request)
        )
    if not inq:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    return inq


@router.delete("/inquiries/{inquiry_id}")
async def delete_inquiry(inquiry_id: int, request: Request):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        ok = await inquiry_service.delete_inquiry(session, inquiry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Inquiry not found")
    return {"ok": True}


@router.post("/inquiries/{inquiry_id}/convert")
async def convert_inquiry(
    inquiry_id: int,
    req: ContactInquiryConvert,
    request: Request,
):
    check_admin(request)
    require_db()
    factory = await get_factory()
    async with factory() as session:
        result = await inquiry_service.convert_to_client(
            session,
            inquiry_id,
            req.model_dump(exclude_none=True),
            handled_by=current_username(request),
        )
    if not result:
        raise HTTPException(status_code=404, detail="Inquiry not found or convert failed")
    return result
