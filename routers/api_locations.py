"""
api_locations.py — 場景資料庫 API（P-a，docs/PREPROD_PLAN.md A 段）

場勘成果資產化：場景 CRUD + 照片牆（uploads/locations/{id}/）+ 使用履歷
（哪些專案用過＋評分＋踩雷心得）。守衛 = 管理員 OR preprod_locations /
preprod_plan 模組。DB 三表：preprod_locations / _photos / _usages（create_all 自建）。
"""

import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, File, HTTPException, Request, UploadFile  # type: ignore

import core.state as state
from core.auth import check_admin_or_module
from core.schemas import LocationPayload, LocationUsagePayload

router = APIRouter(prefix="/api/v1/locations", tags=["locations"])

# 照片檔案落地：<repo>/uploads/locations/{location_id}/（main.py 已 mount /uploads）
_UPLOAD_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# LocationPayload 中可直接 setattr 到 model 的欄位（部分更新白名單）
_LOC_FIELDS = (
    "name", "category", "region", "address", "contact_name", "contact_phone",
    "permit_required", "permit_note", "fee_note", "attributes", "tags",
    "note", "status", "cover_url",
)


def _check_auth(request: Request) -> dict:
    return check_admin_or_module(request, "preprod_locations", "preprod_plan")


def _require_factory():
    """DB 守門：離線一律 503（模式同 api_timesheets）。"""
    if not state.db_online:
        raise HTTPException(status_code=503, detail="資料庫離線")
    from db.session import get_session_factory
    factory = get_session_factory()
    if not factory:
        raise HTTPException(status_code=503, detail="資料庫離線")
    return factory


def _parse_date(raw):
    """'YYYY-MM-DD' → datetime；空值回 None，格式錯 422。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="used_date 格式須為 YYYY-MM-DD")


def _loc_dict(loc, photo_count: int = 0, usage_count: int = 0) -> dict:
    return {
        "id": loc.id,
        "name": loc.name,
        "category": loc.category or "",
        "region": loc.region or "",
        "address": loc.address or "",
        "contact_name": loc.contact_name or "",
        "contact_phone": loc.contact_phone or "",
        "permit_required": int(loc.permit_required or 0),
        "permit_note": loc.permit_note or "",
        "fee_note": loc.fee_note or "",
        "attributes": loc.attributes or {},
        "tags": loc.tags or [],
        "note": loc.note or "",
        "status": loc.status or "可用",
        "cover_url": loc.cover_url or "",
        "created_by": loc.created_by or "",
        "created_at": loc.created_at.isoformat() if loc.created_at else None,
        "updated_at": loc.updated_at.isoformat() if loc.updated_at else None,
        "photo_count": photo_count,
        "usage_count": usage_count,
    }


def _photo_dict(p) -> dict:
    return {
        "id": p.id,
        "location_id": p.location_id,
        "url": p.url or "",
        "caption": p.caption or "",
        "sort_order": p.sort_order or 0,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _usage_dict(u, project_name: str = "") -> dict:
    return {
        "id": u.id,
        "location_id": u.location_id,
        "project_id": u.project_id or "",
        "project_name": project_name or "",
        "used_date": u.used_date.strftime("%Y-%m-%d") if u.used_date else None,
        "rating": u.rating,
        "lesson": u.lesson or "",
        "created_by": u.created_by or "",
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


async def _get_location_or_404(session, lid: str):
    from db.models import PreprodLocation
    loc = await session.get(PreprodLocation, lid)
    if not loc:
        raise HTTPException(status_code=404, detail="找不到此場景")
    return loc


# ── 場景 CRUD ─────────────────────────────────────────────


@router.get("")
async def list_locations(request: Request, q: str = "", category: str = "",
                         region: str = "", tag: str = "", status: str = ""):
    """場景列表 + 篩選（q=名稱/地址、分類、縣市、tag、狀態），附照片/履歷數。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import or_, select, func as safunc
    from db.models import PreprodLocation, PreprodLocationPhoto, PreprodLocationUsage

    async with factory() as session:
        query = select(PreprodLocation).order_by(PreprodLocation.updated_at.desc())
        if category:
            query = query.where(PreprodLocation.category == category)
        if region:
            query = query.where(PreprodLocation.region == region)
        if status:
            query = query.where(PreprodLocation.status == status)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(PreprodLocation.name.ilike(ql),
                                    PreprodLocation.address.ilike(ql)))
        rows = (await session.execute(query)).scalars().all()

        photo_counts = dict((await session.execute(
            select(PreprodLocationPhoto.location_id, safunc.count(PreprodLocationPhoto.id))
            .group_by(PreprodLocationPhoto.location_id))).all())
        usage_counts = dict((await session.execute(
            select(PreprodLocationUsage.location_id, safunc.count(PreprodLocationUsage.id))
            .group_by(PreprodLocationUsage.location_id))).all())

    # tags 是 JSONB list — 量小，python 端過濾即可（不上 contains 運算子）
    if tag:
        rows = [r for r in rows if tag in (r.tags or [])]

    return {"locations": [
        _loc_dict(r, photo_counts.get(r.id, 0), usage_counts.get(r.id, 0)) for r in rows
    ]}


@router.post("")
async def create_location(req: LocationPayload, request: Request):
    """新增場景（name 必填）。"""
    payload = _check_auth(request)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name 必填")
    factory = _require_factory()

    from db.models import PreprodLocation

    data = req.model_dump(exclude_unset=True)
    data["name"] = name
    loc = PreprodLocation(
        id=uuid.uuid4().hex,
        created_by=payload.get("username") or "",
        **{k: v for k, v in data.items() if k in _LOC_FIELDS},
    )
    async with factory() as session:
        session.add(loc)
        await session.commit()
        await session.refresh(loc)
    return {"status": "ok", "location": _loc_dict(loc)}


@router.get("/{lid}")
async def get_location(lid: str, request: Request):
    """場景詳情：欄位 + photos[] + usages[]（附 crm_projects.name）。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import CrmProject, PreprodLocationPhoto, PreprodLocationUsage

    async with factory() as session:
        loc = await _get_location_or_404(session, lid)
        photos = (await session.execute(
            select(PreprodLocationPhoto)
            .where(PreprodLocationPhoto.location_id == lid)
            .order_by(PreprodLocationPhoto.sort_order, PreprodLocationPhoto.created_at)
        )).scalars().all()
        usages = (await session.execute(
            select(PreprodLocationUsage, CrmProject.name)
            .outerjoin(CrmProject, CrmProject.id == PreprodLocationUsage.project_id)
            .where(PreprodLocationUsage.location_id == lid)
            .order_by(PreprodLocationUsage.created_at.desc())
        )).all()

    d = _loc_dict(loc, photo_count=len(photos), usage_count=len(usages))
    d["photos"] = [_photo_dict(p) for p in photos]
    d["usages"] = [_usage_dict(u, pname or "") for u, pname in usages]
    return {"location": d}


@router.put("/{lid}")
async def update_location(lid: str, req: LocationPayload, request: Request):
    """部分更新：只動 payload 有帶的欄位。"""
    _check_auth(request)
    factory = _require_factory()

    data = req.model_dump(exclude_unset=True)
    if "name" in data and not (data["name"] or "").strip():
        raise HTTPException(status_code=422, detail="name 不可為空")

    async with factory() as session:
        loc = await _get_location_or_404(session, lid)
        for k, v in data.items():
            if k in _LOC_FIELDS:
                setattr(loc, k, v)
        loc.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(loc)
    return {"status": "ok", "location": _loc_dict(loc)}


@router.delete("/{lid}")
async def delete_location(lid: str, request: Request):
    """刪場景：連帶刪 photos/usages 列 + best-effort 清掉 uploads 目錄。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import delete as sa_delete
    from db.models import PreprodLocationPhoto, PreprodLocationUsage

    async with factory() as session:
        loc = await _get_location_or_404(session, lid)
        await session.execute(sa_delete(PreprodLocationPhoto)
                              .where(PreprodLocationPhoto.location_id == lid))
        await session.execute(sa_delete(PreprodLocationUsage)
                              .where(PreprodLocationUsage.location_id == lid))
        await session.delete(loc)
        await session.commit()

    if _ID_RE.match(lid):  # id 是自產 uuid hex，格式外的一律不碰檔案系統
        shutil.rmtree(os.path.join(_UPLOAD_BASE, "locations", lid), ignore_errors=True)
    return {"status": "ok"}


# ── 照片 ─────────────────────────────────────────────────


@router.post("/{lid}/photos")
async def upload_photos(lid: str, request: Request, files: List[UploadFile] = File(...)):
    """上傳照片（可多張）。存 uploads/locations/{lid}/{uuid}.{ext}；
    location 尚無封面時，第一張自動設為 cover_url。"""
    _check_auth(request)
    if not _ID_RE.match(lid):
        raise HTTPException(status_code=422, detail="無效的場景 ID")
    factory = _require_factory()

    from sqlalchemy import select, func as safunc
    from db.models import PreprodLocationPhoto

    # 副檔名白名單先全驗，避免存到一半才 422
    exts = []
    for f in files:
        ext = (os.path.splitext(f.filename or "")[1] or "").lower()
        if ext not in _ALLOWED_IMG_EXT:
            raise HTTPException(status_code=422, detail=f"不支援的圖片格式：{ext or '(無副檔名)'}")
        exts.append(ext)

    async with factory() as session:
        loc = await _get_location_or_404(session, lid)
        next_sort = (await session.execute(
            select(safunc.coalesce(safunc.max(PreprodLocationPhoto.sort_order), -1))
            .where(PreprodLocationPhoto.location_id == lid))).scalar() + 1

        dest_dir = os.path.join(_UPLOAD_BASE, "locations", lid)
        os.makedirs(dest_dir, exist_ok=True)

        saved = []
        for f, ext in zip(files, exts):
            fname = f"{uuid.uuid4().hex}{ext}"
            content = await f.read()
            with open(os.path.join(dest_dir, fname), "wb") as fp:
                fp.write(content)
            photo = PreprodLocationPhoto(
                id=uuid.uuid4().hex,
                location_id=lid,
                url=f"/uploads/locations/{lid}/{fname}",
                sort_order=next_sort,
            )
            next_sort += 1
            session.add(photo)
            saved.append(photo)

        if not loc.cover_url and saved:
            loc.cover_url = saved[0].url
            loc.updated_at = datetime.now(timezone.utc)
        await session.commit()
        for p in saved:
            await session.refresh(p)
        cover_url = loc.cover_url or ""

    return {"status": "ok", "photos": [_photo_dict(p) for p in saved], "cover_url": cover_url}


@router.delete("/photos/{pid}")
async def delete_photo(pid: str, request: Request):
    """刪照片：DB 列 + best-effort 刪檔；若剛好是封面則改指下一張（或清空）。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import PreprodLocation, PreprodLocationPhoto

    async with factory() as session:
        photo = await session.get(PreprodLocationPhoto, pid)
        if not photo:
            raise HTTPException(status_code=404, detail="找不到此照片")
        url = photo.url or ""
        lid = photo.location_id
        await session.delete(photo)
        await session.flush()

        loc = await session.get(PreprodLocation, lid)
        if loc and url and loc.cover_url == url:
            rest = (await session.execute(
                select(PreprodLocationPhoto)
                .where(PreprodLocationPhoto.location_id == lid)
                .order_by(PreprodLocationPhoto.sort_order, PreprodLocationPhoto.created_at)
            )).scalars().first()
            loc.cover_url = rest.url if rest else None
        await session.commit()

    # url 由本模組自產（/uploads/locations/...），照相對路徑 best-effort 刪檔
    if url.startswith("/uploads/locations/"):
        try:
            os.remove(os.path.join(_UPLOAD_BASE, *url.split("/")[2:]))
        except OSError:
            pass
    return {"status": "ok"}


# ── 使用履歷 ─────────────────────────────────────────────


@router.post("/{lid}/usages")
async def create_usage(lid: str, req: LocationUsagePayload, request: Request):
    """新增使用履歷（專案/日期/評分 1-5/心得踩雷）。"""
    payload = _check_auth(request)
    if req.rating is not None and not (1 <= req.rating <= 5):
        raise HTTPException(status_code=422, detail="rating 須為 1-5")
    used_date = _parse_date(req.used_date)
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import CrmProject, PreprodLocationUsage

    async with factory() as session:
        await _get_location_or_404(session, lid)
        usage = PreprodLocationUsage(
            id=uuid.uuid4().hex,
            location_id=lid,
            project_id=(req.project_id or "").strip() or None,
            used_date=used_date,
            rating=req.rating,
            lesson=req.lesson or None,
            created_by=payload.get("username") or "",
        )
        session.add(usage)
        await session.commit()
        await session.refresh(usage)
        project_name = ""
        if usage.project_id:
            project_name = (await session.execute(
                select(CrmProject.name).where(CrmProject.id == usage.project_id)
            )).scalar() or ""

    return {"status": "ok", "usage": _usage_dict(usage, project_name)}


@router.delete("/usages/{uid}")
async def delete_usage(uid: str, request: Request):
    """刪使用履歷。"""
    _check_auth(request)
    factory = _require_factory()

    from db.models import PreprodLocationUsage

    async with factory() as session:
        usage = await session.get(PreprodLocationUsage, uid)
        if not usage:
            raise HTTPException(status_code=404, detail="找不到此履歷")
        await session.delete(usage)
        await session.commit()
    return {"status": "ok"}
