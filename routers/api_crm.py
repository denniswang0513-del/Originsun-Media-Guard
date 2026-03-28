"""
api_crm.py — CRM 客戶管理 API
Endpoints:
  GET    /api/v1/crm/clients              — 列表（搜尋 + 篩選）
  POST   /api/v1/crm/clients              — 新增
  GET    /api/v1/crm/clients/{id}         — 詳情
  PUT    /api/v1/crm/clients/{id}         — 更新
  DELETE /api/v1/crm/clients/{id}         — 刪除
  POST   /api/v1/crm/clients/import_csv   — CSV 匯入（Notion / Google Sheets 格式）
  GET    /api/v1/crm/users               — 取得可選 AM/PM 使用者列表
"""
import asyncio
import csv
import io
import os
import shutil
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Query

from config import load_settings as _load_settings

import core.state as state

try:
    from sqlalchemy import select, or_
    from sqlalchemy.exc import IntegrityError
    from db.models import Client, User, CrmProject
    _HAS_DB = True
except ImportError:
    _HAS_DB = False

router = APIRouter(prefix="/api/v1/crm", tags=["CRM"])


# ── Helpers ──────────────────────────────────────────────────

def _check_auth(request: Request):
    try:
        from core.auth import check_admin
        check_admin(request)
    except ImportError:
        pass


def _require_db():
    if not state.db_online:
        raise HTTPException(status_code=503, detail="資料庫目前不可用")


async def _get_factory():
    from db.session import get_session_factory
    factory = get_session_factory()
    if not factory:
        raise HTTPException(status_code=503, detail="資料庫未初始化")
    return factory


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_shoot_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    try:
        d = date.fromisoformat(date_str)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _to_dict(c) -> dict:
    return {
        "id": c.id,
        "short_name": c.short_name,
        "full_name": c.full_name or "",
        "tax_id": c.tax_id or "",
        "am_username": c.am_username or "",
        "source_channel": c.source_channel or "",
        "contact_person": c.contact_person or "",
        "contact_method": c.contact_method or "",
        "status": c.status or "潛在客戶",
        "cooperation_note": c.cooperation_note or "",
        "payment_info": c.payment_info or "",
        "payment_note": c.payment_note or "",
        "notes": c.notes or "",
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


from core.schemas import ClientPayload, CrmProjectPayload


# ── Endpoints ────────────────────────────────────────────────

@router.get("/clients")
async def list_clients(
    q: str = Query(""),
    status: str = Query(""),
    am: str = Query(""),
):
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        query = select(Client).order_by(Client.updated_at.desc())
        if status:
            query = query.where(Client.status == status)
        if am:
            query = query.where(Client.am_username == am)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(
                Client.short_name.ilike(ql),
                Client.full_name.ilike(ql),
                Client.contact_person.ilike(ql),
            ))
        result = await session.execute(query)
        clients = result.scalars().all()

    return {"clients": [_to_dict(c) for c in clients], "total": len(clients)}


@router.post("/clients")
async def create_client(req: ClientPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    now = _now()
    client = Client(id=uuid.uuid4().hex, created_at=now, updated_at=now, **req.model_dump())
    try:
        async with factory() as session:
            session.add(client)
            await session.commit()
            await session.refresh(client)
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"客戶代稱「{req.short_name}」已存在")
    return {"status": "ok", "client": _to_dict(client)}


@router.get("/clients/{client_id}")
async def get_client(client_id: str):
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        client = await session.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="找不到此客戶")
    return _to_dict(client)


@router.put("/clients/{client_id}")
async def update_client(client_id: str, req: ClientPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        client = await session.get(Client, client_id)
        if not client:
            raise HTTPException(status_code=404, detail="找不到此客戶")
        for k, v in req.model_dump().items():
            setattr(client, k, v)
        client.updated_at = _now()
        await session.commit()
        await session.refresh(client)
    return {"status": "ok", "client": _to_dict(client)}


@router.delete("/clients/{client_id}")
async def delete_client(client_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        client = await session.get(Client, client_id)
        if not client:
            raise HTTPException(status_code=404, detail="找不到此客戶")
        await session.delete(client)
        await session.commit()
    return {"status": "ok"}


# ── CSV Import ───────────────────────────────────────────────

# 欄位別名對照表（Notion export + Google Sheets 兩種格式）
_COL_MAP = {
    "short_name":        ["客戶代稱", "name", "客戶名稱", "名稱", "客戶"],
    "full_name":         ["全稱", "full_name", "抬頭", "legal_name", "發票抬頭"],
    "tax_id":            ["統編", "統一編號", "tax_id"],
    "am_username":       ["am", "業務", "am_username"],
    "source_channel":    ["來源管道", "source_channel"],
    "contact_person":    ["客戶聯絡人", "聯絡人", "contact_person", "客戶聯絡人/聯絡..."],
    "contact_method":    ["聯絡方式", "聯絡管道", "contact_method"],
    "status":            ["狀態", "status"],
    "cooperation_note":  ["合作契機", "cooperation_note"],
    "payment_info":      ["匯款資訊", "匯款帳號", "payment_info"],
    "payment_note":      ["匯款備註", "payment_note"],
    "notes":             ["備註", "notes"],
}


def _map_row(header_map: dict, row: dict) -> dict:
    """Map a CSV row to client fields using pre-computed header_map."""
    result = {}
    for field, aliases in _COL_MAP.items():
        for alias in aliases:
            orig = header_map.get(alias.lower())
            if orig is not None:
                val = row.get(orig, "").strip()
                if val:
                    result[field] = val
                break
    return result


@router.post("/clients/import_csv")
async def import_csv(request: Request, file: UploadFile = File(...)):
    _check_auth(request)
    _require_db()

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("big5", errors="replace")

    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    header_map = {h.lower(): h for h in headers}
    imported = updated = skipped = 0

    factory = await _get_factory()

    async with factory() as session:
        existing_map: dict = {
            c.short_name: c
            for c in (await session.execute(select(Client))).scalars().all()
        }

        for row in reader:
            data = _map_row(header_map, row)
            if not data.get("short_name"):
                skipped += 1
                continue

            now = _now()
            existing = existing_map.get(data["short_name"])
            if existing:
                for k, v in data.items():
                    if k != "short_name" and v:
                        setattr(existing, k, v)
                existing.updated_at = now
                updated += 1
            else:
                new_client = Client(id=uuid.uuid4().hex, created_at=now, updated_at=now, **data)
                session.add(new_client)
                existing_map[data["short_name"]] = new_client
                imported += 1

        await session.commit()

    return {"status": "ok", "imported": imported, "updated": updated, "skipped": skipped}


# ── Users for AM/PM pickers ──────────────────────────────────

@router.get("/users")
async def list_crm_users():
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        rows = (await session.execute(
            select(User.username, User.avatar_url, User.email)
        )).all()

    return {"users": [
        {"username": r[0], "avatar_url": r[1] or "", "email": r[2] or ""}
        for r in rows
    ]}


# ── Project Helpers ─────────────────────────────────────────

def _to_project_dict(p, client_short_name: str = "") -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "client_id": p.client_id,
        "client_short_name": client_short_name,
        "status": p.status or "洽談中",
        "am_username": p.am_username or "",
        "pm_usernames": p.pm_usernames or [],
        "shoot_date": p.shoot_date.isoformat() if p.shoot_date else None,
        "folder_path": p.folder_path or "",
        "description": p.description or "",
        "notes": p.notes or "",
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


# ── Project Endpoints ───────────────────────────────────────

@router.get("/projects")
async def list_projects(
    q: str = Query(""),
    status: str = Query(""),
    client_id: str = Query(""),
    am: str = Query(""),
):
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        query = (
            select(CrmProject, Client.short_name.label("client_short_name"))
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .order_by(CrmProject.updated_at.desc())
        )
        if status:
            query = query.where(CrmProject.status == status)
        if client_id:
            query = query.where(CrmProject.client_id == client_id)
        if am:
            query = query.where(CrmProject.am_username == am)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(
                CrmProject.name.ilike(ql),
                CrmProject.description.ilike(ql),
            ))
        rows = (await session.execute(query)).all()

    return {
        "projects": [_to_project_dict(row[0], row[1] or "") for row in rows],
        "total": len(rows),
    }


@router.post("/projects")
async def create_project(req: CrmProjectPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    now = _now()
    shoot_dt = _parse_shoot_date(req.shoot_date)

    data = req.model_dump(exclude={"shoot_date"})
    project = CrmProject(id=uuid.uuid4().hex, shoot_date=shoot_dt,
                         created_at=now, updated_at=now, **data)

    async with factory() as session:
        # Verify client exists
        client = await session.get(Client, req.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="找不到指定的客戶")
        client_name = client.short_name

        session.add(project)
        await session.commit()
        await session.refresh(project)

    # Folder template copy (best-effort)
    warning = ""
    if req.folder_path:
        try:
            settings = _load_settings()
            template = settings.get("project_folder_template", "")
            if template and os.path.isdir(template) and not os.path.exists(req.folder_path):
                await asyncio.to_thread(shutil.copytree, template, req.folder_path)
        except Exception as e:
            warning = f"資料夾範本複製失敗: {e}"

    result = {"status": "ok", "project": _to_project_dict(project, client_name)}
    if warning:
        result["warning"] = warning
    return result


@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        client = await session.get(Client, project.client_id)
        client_name = client.short_name if client else ""

    return _to_project_dict(project, client_name)


@router.put("/projects/{project_id}")
async def update_project(project_id: str, req: CrmProjectPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    shoot_dt = _parse_shoot_date(req.shoot_date)

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

        # Verify client exists
        client = await session.get(Client, req.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="找不到指定的客戶")

        for k, v in req.model_dump(exclude={"shoot_date"}).items():
            setattr(project, k, v)
        project.shoot_date = shoot_dt
        project.updated_at = _now()
        await session.commit()
        await session.refresh(project)
        client_name = client.short_name

    return {"status": "ok", "project": _to_project_dict(project, client_name)}


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        await session.delete(project)
        await session.commit()
    return {"status": "ok"}


@router.patch("/projects/{project_id}/status")
async def update_project_status(project_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    body = await request.json()
    new_status = body.get("status", "")
    if new_status not in ("洽談中", "進行中", "已結案"):
        raise HTTPException(status_code=400, detail="無效的狀態值")

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        project.status = new_status
        project.updated_at = _now()
        await session.commit()
        await session.refresh(project)
        client = await session.get(Client, project.client_id)
        client_name = client.short_name if client else ""

    return {"status": "ok", "project": _to_project_dict(project, client_name)}
