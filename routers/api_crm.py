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
    from sqlalchemy import select, or_, delete
    from sqlalchemy.exc import IntegrityError
    from db.models import (Client, User, CrmProject, CrmQuotation, CrmQuotationItem,
                           CrmQuotationTemplate, CrmStaff, CrmProjectStaff, CrmProjectExpense,
                           CrmInvoice, CrmPaymentRequest, CrmCashEntry)
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


from core.schemas import (ClientPayload, CrmProjectPayload, ProjectExpensePayload,
                         QuotationPayload, QuotationItemPayload, QuotationTemplatePayload,
                         StaffPayload, ProjectStaffPayload, InvoicePayload, PaymentRequestPayload,
                         CashEntryPayload)


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
        "id": p.id, "name": p.name,
        "client_id": p.client_id, "client_short_name": client_short_name,
        "status": p.status or "洽談中",
        "am_username": p.am_username or "", "pm_usernames": p.pm_usernames or [],
        "shoot_date": p.shoot_date.isoformat() if p.shoot_date else None,
        "start_date": p.start_date.isoformat() if p.start_date else None,
        "completion_date": p.completion_date.isoformat() if p.completion_date else None,
        "project_type": p.project_type or "",
        "folder_path": p.folder_path or "",
        "description": p.description or "", "notes": p.notes or "",
        "contract_amount": p.contract_amount,
        "tax_rate": p.tax_rate, "profit_target_pct": p.profit_target_pct,
        "misc_budget_pct": p.misc_budget_pct,
        "payment_status": p.payment_status or "未到帳",
        "amount_receivable": p.amount_receivable,
        "amount_received": p.amount_received,
        "transfer_fee": p.transfer_fee,
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
    date_fields = {"shoot_date", "start_date", "completion_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}

    data = req.model_dump(exclude=date_fields)
    project = CrmProject(id=uuid.uuid4().hex, **dates,
                         created_at=now, updated_at=now, **data)

    async with factory() as session:
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

    date_fields = {"shoot_date", "start_date", "completion_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

        client = await session.get(Client, req.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="找不到指定的客戶")

        for k, v in req.model_dump(exclude=date_fields).items():
            setattr(project, k, v)
        for k, v in dates.items():
            setattr(project, k, v)
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
    if new_status not in ("洽談中", "報價中", "進行中", "已結案"):
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


# ── Project CSV Import ──────────────────────────────────────

_PROJECT_COL_MAP = {
    "name":           ["專案名稱", "name", "案名", "專案", "project_name", "project"],
    "client_name":    ["客戶", "客戶代稱", "client", "client_name"],
    "status":         ["狀態", "status"],
    "am_username":    ["am", "業務", "am_username"],
    "shoot_date":     ["拍攝日期", "拍攝日", "shoot_date", "拍攝"],
    "folder_path":    ["資料夾", "folder", "folder_path", "路徑"],
    "description":    ["說明", "描述", "description"],
    "notes":          ["備註", "notes"],
}


def _map_project_row(header_map: dict, row: dict) -> dict:
    result = {}
    for field, aliases in _PROJECT_COL_MAP.items():
        for alias in aliases:
            orig = header_map.get(alias.lower())
            if orig is not None:
                val = row.get(orig, "").strip()
                if val:
                    result[field] = val
                break
    return result


@router.post("/projects/import_csv")
async def import_projects_csv(request: Request, file: UploadFile = File(...)):
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
        clients = (await session.execute(select(Client))).scalars().all()
        client_map = {c.short_name: c.id for c in clients}

        existing_map: dict = {
            p.name: p
            for p in (await session.execute(select(CrmProject))).scalars().all()
        }

        for row in reader:
            data = _map_project_row(header_map, row)
            if not data.get("name"):
                skipped += 1
                continue

            client_name = data.pop("client_name", "")
            client_id = client_map.get(client_name, "")
            shoot_dt = _parse_shoot_date(data.pop("shoot_date", None))

            now = _now()
            existing = existing_map.get(data["name"])
            if existing:
                for k, v in data.items():
                    if k != "name" and v:
                        setattr(existing, k, v)
                if client_id:
                    existing.client_id = client_id
                if shoot_dt:
                    existing.shoot_date = shoot_dt
                existing.updated_at = now
                updated += 1
            else:
                if not client_id:
                    skipped += 1
                    continue
                proj_data = {k: v for k, v in data.items() if k != "name"}
                new_proj = CrmProject(
                    id=uuid.uuid4().hex, name=data["name"],
                    client_id=client_id, shoot_date=shoot_dt,
                    created_at=now, updated_at=now, **proj_data,
                )
                session.add(new_proj)
                existing_map[data["name"]] = new_proj
                imported += 1

        await session.commit()

    return {"status": "ok", "imported": imported, "updated": updated, "skipped": skipped}


# ── Quotation Helpers ───────────────────────────────────────

def _calc_quotation(items_data: list, discount: int, tax_rate: int):
    """Calculate subtotal/tax/total from items."""
    for it in items_data:
        it["amount"] = it["quantity"] * it["unit_price"]
    subtotal = sum(it["amount"] for it in items_data)
    taxable = max(subtotal - discount, 0)
    tax_amount = int(taxable * tax_rate / 100)
    total = taxable + tax_amount
    return subtotal, tax_amount, total


def _to_quotation_dict(q, items=None, project_name="", client_short_name="") -> dict:
    return {
        "id": q.id, "project_id": q.project_id,
        "project_name": project_name, "client_short_name": client_short_name,
        "version": q.version, "status": q.status or "草稿",
        "quote_date": q.quote_date.isoformat() if q.quote_date else None,
        "valid_until": q.valid_until.isoformat() if q.valid_until else None,
        "subtotal": q.subtotal, "discount": q.discount,
        "tax_rate": q.tax_rate, "tax_amount": q.tax_amount,
        "total": q.total, "final_price": q.final_price,
        "payment_stages": q.payment_stages or [], "terms": q.terms or "",
        "items": items or [],
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "updated_at": q.updated_at.isoformat() if q.updated_at else None,
    }


def _item_to_dict(it) -> dict:
    return {
        "id": it.id, "group_name": it.group_name or "",
        "sort_order": it.sort_order, "description": it.description,
        "unit": it.unit, "quantity": it.quantity,
        "unit_price": it.unit_price, "amount": it.amount,
        "internal_cost": it.internal_cost, "note": it.note or "",
    }


async def _load_items(session, quotation_id: str) -> list:
    result = await session.execute(
        select(CrmQuotationItem)
        .where(CrmQuotationItem.quotation_id == quotation_id)
        .order_by(CrmQuotationItem.sort_order)
    )
    return [_item_to_dict(it) for it in result.scalars().all()]


async def _save_items(session, quotation_id: str, items: list):
    """Delete existing items, insert new ones. Returns items_data for calc."""
    await session.execute(delete(CrmQuotationItem).where(CrmQuotationItem.quotation_id == quotation_id))

    items_data = []
    for i, it in enumerate(items):
        d = it.model_dump() if hasattr(it, 'model_dump') else dict(it)
        d["amount"] = d["quantity"] * d["unit_price"]
        items_data.append(d)
        session.add(CrmQuotationItem(
            id=uuid.uuid4().hex, quotation_id=quotation_id, sort_order=i,
            group_name=d.get("group_name", ""), description=d["description"],
            unit=d.get("unit", "式"), quantity=d["quantity"],
            unit_price=d["unit_price"], amount=d["amount"],
            internal_cost=d.get("internal_cost", 0), note=d.get("note", ""),
        ))
    return items_data


# ── Quotation Endpoints ─────────────────────────────────────

@router.get("/quotations")
async def list_all_quotations(
    q: str = Query(""), status: str = Query(""),
    client_id: str = Query(""), am: str = Query(""),
):
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        query = (
            select(CrmQuotation, CrmProject.name.label("pn"), Client.short_name.label("cn"))
            .outerjoin(CrmProject, CrmProject.id == CrmQuotation.project_id)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .order_by(CrmQuotation.updated_at.desc())
        )
        if status:
            query = query.where(CrmQuotation.status == status)
        if client_id:
            query = query.where(CrmProject.client_id == client_id)
        if am:
            query = query.where(CrmProject.am_username == am)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(CrmProject.name.ilike(ql), Client.short_name.ilike(ql)))
        rows = (await session.execute(query)).all()

    return {
        "quotations": [_to_quotation_dict(r[0], project_name=r[1] or "", client_short_name=r[2] or "") for r in rows],
        "total": len(rows),
    }


@router.get("/quotations/stats")
async def quotation_stats():
    _require_db()
    factory = await _get_factory()
    now = _now()
    month_start = datetime(now.year, now.month, 1, tzinfo=now.tzinfo)

    async with factory() as session:
        all_q = (await session.execute(select(CrmQuotation))).scalars().all()

    month_total = sum(
        (q.final_price if q.final_price is not None else q.total) or 0
        for q in all_q if q.created_at and q.created_at >= month_start
    )
    pending = sum(1 for q in all_q if q.status == "已寄送")
    signed = sum(1 for q in all_q if q.status == "已簽核")
    total_count = len(all_q)

    return {
        "month_total": month_total, "pending_count": pending,
        "sign_rate": round(signed / total_count * 100) if total_count > 0 else 0,
        "total_count": total_count,
    }


@router.get("/projects/{project_id}/quotations")
async def list_project_quotations(project_id: str):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmQuotation).where(CrmQuotation.project_id == project_id)
            .order_by(CrmQuotation.version.desc())
        )).scalars().all()
    return {"quotations": [_to_quotation_dict(q) for q in rows]}


@router.post("/projects/{project_id}/quotations")
async def create_quotation(project_id: str, req: QuotationPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    now = _now()

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

        from sqlalchemy import func as sa_func
        max_ver = (await session.execute(
            select(sa_func.max(CrmQuotation.version)).where(CrmQuotation.project_id == project_id)
        )).scalar()
        version = (max_ver or 0) + 1

        q_id = uuid.uuid4().hex
        items_data = await _save_items(session, q_id, req.items)
        subtotal, tax_amount, total = _calc_quotation(items_data, req.discount, req.tax_rate)

        q = CrmQuotation(
            id=q_id, project_id=project_id, version=version,
            status=req.status, quote_date=_parse_shoot_date(req.quote_date),
            valid_until=_parse_shoot_date(req.valid_until),
            subtotal=subtotal, discount=req.discount, tax_rate=req.tax_rate,
            tax_amount=tax_amount, total=total, final_price=req.final_price,
            payment_stages=req.payment_stages or None, terms=req.terms,
            created_at=now, updated_at=now,
        )
        session.add(q)
        await session.commit()
        await session.refresh(q)
        loaded_items = await _load_items(session, q.id)

    return {"status": "ok", "quotation": _to_quotation_dict(q, items=loaded_items)}


@router.get("/quotations/{quotation_id}")
async def get_quotation(quotation_id: str):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        q = await session.get(CrmQuotation, quotation_id)
        if not q:
            raise HTTPException(status_code=404, detail="找不到此報價")
        items = await _load_items(session, q.id)
        project = await session.get(CrmProject, q.project_id)
        pn = project.name if project else ""
        client = await session.get(Client, project.client_id) if project else None
        cn = client.short_name if client else ""
    return _to_quotation_dict(q, items=items, project_name=pn, client_short_name=cn)


@router.put("/quotations/{quotation_id}")
async def update_quotation(quotation_id: str, req: QuotationPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        q = await session.get(CrmQuotation, quotation_id)
        if not q:
            raise HTTPException(status_code=404, detail="找不到此報價")

        items_data = await _save_items(session, q.id, req.items)
        subtotal, tax_amount, total = _calc_quotation(items_data, req.discount, req.tax_rate)

        q.status = req.status
        q.quote_date = _parse_shoot_date(req.quote_date)
        q.valid_until = _parse_shoot_date(req.valid_until)
        q.subtotal = subtotal
        q.discount = req.discount
        q.tax_rate = req.tax_rate
        q.tax_amount = tax_amount
        q.total = total
        q.final_price = req.final_price
        q.payment_stages = req.payment_stages or None
        q.terms = req.terms
        q.updated_at = _now()
        await session.commit()
        await session.refresh(q)
        loaded_items = await _load_items(session, q.id)

    return {"status": "ok", "quotation": _to_quotation_dict(q, items=loaded_items)}


@router.delete("/quotations/{quotation_id}")
async def delete_quotation(quotation_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        q = await session.get(CrmQuotation, quotation_id)
        if not q:
            raise HTTPException(status_code=404, detail="找不到此報價")
        await session.execute(delete(CrmQuotationItem).where(CrmQuotationItem.quotation_id == q.id))
        await session.delete(q)
        await session.commit()
    return {"status": "ok"}


# ── Quotation Template Endpoints ────────────────────────────

@router.get("/quotation-templates")
async def list_templates():
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmQuotationTemplate).order_by(CrmQuotationTemplate.updated_at.desc())
        )).scalars().all()
    return {"templates": [{
        "id": t.id, "name": t.name, "description": t.description or "",
        "tax_rate": t.tax_rate, "terms": t.terms or "",
        "payment_stages": t.payment_stages or [],
        "items": t.items or [],
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in rows]}


@router.post("/quotation-templates")
async def create_template(req: QuotationTemplatePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    now = _now()
    items = [it.model_dump() for it in req.items]
    t = CrmQuotationTemplate(
        id=uuid.uuid4().hex, name=req.name, description=req.description,
        tax_rate=req.tax_rate, terms=req.terms,
        payment_stages=req.payment_stages or None,
        items=items, created_at=now, updated_at=now,
    )
    async with factory() as session:
        session.add(t)
        try:
            await session.commit()
        except IntegrityError:
            raise HTTPException(status_code=409, detail=f"範本「{req.name}」已存在")
    return {"status": "ok", "template": {"id": t.id, "name": t.name}}


@router.put("/quotation-templates/{template_id}")
async def update_template(template_id: str, req: QuotationTemplatePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        t = await session.get(CrmQuotationTemplate, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="找不到此範本")
        t.name = req.name
        t.description = req.description
        t.tax_rate = req.tax_rate
        t.terms = req.terms
        t.payment_stages = req.payment_stages or None
        t.items = [it.model_dump() for it in req.items]
        t.updated_at = _now()
        await session.commit()
    return {"status": "ok"}


@router.delete("/quotation-templates/{template_id}")
async def delete_template(template_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        t = await session.get(CrmQuotationTemplate, template_id)
        if not t:
            raise HTTPException(status_code=404, detail="找不到此範本")
        await session.delete(t)
        await session.commit()
    return {"status": "ok"}


# ── Staff Helpers ───────────────────────────────────────────

def _to_staff_dict(s) -> dict:
    return {
        "id": s.id, "name": s.name, "role": s.role or "",
        "daily_rate": s.daily_rate, "hourly_rate": s.hourly_rate,
        "phone": s.phone or "", "email": s.email or "",
        "id_number": s.id_number or "", "address": s.address or "",
        "bank_name": s.bank_name or "",
        "bank_account": s.bank_account or "", "portfolio_url": s.portfolio_url or "",
        "status": s.status or "在職", "notes": s.notes or "",
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


# ── Staff Endpoints ─────────────────────────────────────────

@router.get("/staff")
async def list_staff(q: str = Query(""), role: str = Query(""), status: str = Query("")):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        query = select(CrmStaff).order_by(CrmStaff.name)
        if role:
            query = query.where(CrmStaff.role == role)
        if status:
            query = query.where(CrmStaff.status == status)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(CrmStaff.name.ilike(ql), CrmStaff.role.ilike(ql)))
        rows = (await session.execute(query)).scalars().all()
    return {"staff": [_to_staff_dict(s) for s in rows], "total": len(rows)}


@router.post("/staff")
async def create_staff(req: StaffPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    now = _now()
    s = CrmStaff(id=uuid.uuid4().hex, created_at=now, updated_at=now, **req.model_dump())
    async with factory() as session:
        session.add(s)
        await session.commit()
        await session.refresh(s)
    return {"status": "ok", "staff": _to_staff_dict(s)}


@router.get("/staff/{staff_id}")
async def get_staff(staff_id: str):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if not s:
            raise HTTPException(status_code=404, detail="找不到此人員")
    return _to_staff_dict(s)


@router.put("/staff/{staff_id}")
async def update_staff(staff_id: str, req: StaffPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if not s:
            raise HTTPException(status_code=404, detail="找不到此人員")
        for k, v in req.model_dump().items():
            setattr(s, k, v)
        s.updated_at = _now()
        await session.commit()
        await session.refresh(s)
    return {"status": "ok", "staff": _to_staff_dict(s)}


@router.delete("/staff/{staff_id}")
async def delete_staff(staff_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if not s:
            raise HTTPException(status_code=404, detail="找不到此人員")
        await session.delete(s)
        await session.commit()
    return {"status": "ok"}


@router.get("/staff/{staff_id}/projects")
async def get_staff_projects(staff_id: str):
    """查詢人員的專案執行紀錄。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectStaff, CrmProject.name.label("project_name"),
                   Client.short_name.label("client_name"))
            .outerjoin(CrmProject, CrmProject.id == CrmProjectStaff.project_id)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .where(CrmProjectStaff.staff_id == staff_id)
        )).all()
    return {"projects": [{
        "id": r[0].id, "project_id": r[0].project_id,
        "project_name": r[1] or "", "client_name": r[2] or "",
        "role_in_project": r[0].role_in_project or "",
        "days": r[0].days, "cost": r[0].cost,
        "notes": r[0].notes or "",
    } for r in rows]}


# ── Staff CSV Import ────────────────────────────────────────

_STAFF_COL_MAP = {
    "name":          ["姓名", "收款人", "name"],
    "role":          ["職能", "角色", "role"],
    "daily_rate":    ["日費", "daily_rate"],
    "hourly_rate":   ["時薪", "hourly_rate"],
    "phone":         ["電話", "phone"],
    "email":         ["email", "信箱"],
    "id_number":     ["身分證", "身份證字號", "身分證字號", "id_number"],
    "address":       ["住址", "地址", "address"],
    "bank_name":     ["銀行", "bank_name"],
    "bank_account":  ["帳號", "bank_account"],
    "portfolio_url": ["作品集", "portfolio_url"],
    "status":        ["狀態", "status"],
    "notes":         ["備註", "notes"],
}


def _map_staff_row(header_map: dict, row: dict) -> dict:
    data = {}
    for field, aliases in _STAFF_COL_MAP.items():
        for alias in aliases:
            orig = header_map.get(alias.lower())
            if orig and row.get(orig, "").strip():
                val = row[orig].strip()
                data[field] = int(val) if field in ("daily_rate", "hourly_rate") and val.isdigit() else val
                break
    return data


@router.post("/staff/import_csv")
async def import_staff_csv(request: Request, file: UploadFile = File(...)):
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
        existing_map = {s.name: s for s in (await session.execute(select(CrmStaff))).scalars().all()}
        for row in reader:
            data = _map_staff_row(header_map, row)
            if not data.get("name"):
                skipped += 1
                continue
            now = _now()
            existing = existing_map.get(data["name"])
            if existing:
                for k, v in data.items():
                    if k != "name" and v:
                        setattr(existing, k, v)
                existing.updated_at = now
                updated += 1
            else:
                s = CrmStaff(id=uuid.uuid4().hex, created_at=now, updated_at=now, **data)
                session.add(s)
                existing_map[data["name"]] = s
                imported += 1
        await session.commit()
    return {"status": "ok", "imported": imported, "updated": updated, "skipped": skipped}


# ── Project Staff (派工) Endpoints ──────────────────────────

@router.get("/projects/{project_id}/staff")
async def list_project_staff(project_id: str):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectStaff, CrmStaff.name.label("staff_name"), CrmStaff.role.label("staff_role"),
                   CrmStaff.daily_rate.label("default_rate"))
            .outerjoin(CrmStaff, CrmStaff.id == CrmProjectStaff.staff_id)
            .where(CrmProjectStaff.project_id == project_id)
        )).all()
    return {"staff": [{
        "id": r[0].id, "project_id": r[0].project_id, "staff_id": r[0].staff_id,
        "staff_name": r[1] or "", "staff_role": r[2] or "",
        "role_in_project": r[0].role_in_project or "", "days": r[0].days,
        "rate": r[0].rate_override if r[0].rate_override is not None else (r[3] or 0),
        "cost": r[0].cost, "notes": r[0].notes or "",
    } for r in rows]}


@router.post("/projects/{project_id}/staff")
async def add_project_staff(project_id: str, req: ProjectStaffPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        staff = await session.get(CrmStaff, req.staff_id)
        if not staff:
            raise HTTPException(status_code=404, detail="找不到此人員")
        rate = req.rate_override if req.rate_override is not None else staff.daily_rate
        cost = req.days * rate
        ps = CrmProjectStaff(
            id=uuid.uuid4().hex, project_id=project_id, staff_id=req.staff_id,
            role_in_project=req.role_in_project, days=req.days,
            rate_override=req.rate_override, cost=cost, notes=req.notes,
        )
        session.add(ps)
        await session.commit()
    return {"status": "ok", "cost": cost}


@router.put("/project-staff/{ps_id}")
async def update_project_staff(ps_id: str, req: ProjectStaffPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        ps = await session.get(CrmProjectStaff, ps_id)
        if not ps:
            raise HTTPException(status_code=404, detail="找不到此派工紀錄")
        staff = await session.get(CrmStaff, req.staff_id)
        rate = req.rate_override if req.rate_override is not None else (staff.daily_rate if staff else 0)
        ps.staff_id = req.staff_id
        ps.role_in_project = req.role_in_project
        ps.days = req.days
        ps.rate_override = req.rate_override
        ps.cost = req.days * rate
        ps.notes = req.notes
        await session.commit()
    return {"status": "ok", "cost": ps.cost}


@router.delete("/project-staff/{ps_id}")
async def delete_project_staff(ps_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        ps = await session.get(CrmProjectStaff, ps_id)
        if not ps:
            raise HTTPException(status_code=404, detail="找不到此派工紀錄")
        await session.delete(ps)
        await session.commit()
    return {"status": "ok"}


@router.get("/projects/{project_id}/cost-summary")
async def project_cost_summary(project_id: str):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectStaff).where(CrmProjectStaff.project_id == project_id)
        )).scalars().all()
    total_cost = sum(r.cost for r in rows)
    return {"total_cost": total_cost, "staff_count": len(rows)}


# ── Project Expense (雜支) Endpoints ────────────────────────

@router.get("/projects/{project_id}/expenses")
async def list_project_expenses(project_id: str):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectExpense).where(CrmProjectExpense.project_id == project_id)
        )).scalars().all()
    return {"expenses": [{
        "id": e.id, "category": e.category,
        "estimated": e.estimated, "actual": e.actual,
        "receipt_url": e.receipt_url or "", "notes": e.notes or "",
    } for e in rows]}


@router.post("/projects/{project_id}/expenses")
async def add_project_expense(project_id: str, req: ProjectExpensePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = CrmProjectExpense(
            id=uuid.uuid4().hex, project_id=project_id,
            category=req.category, estimated=req.estimated,
            actual=req.actual, notes=req.notes,
        )
        session.add(e)
        await session.commit()
    return {"status": "ok", "expense_id": e.id}


@router.put("/project-expenses/{expense_id}")
async def update_project_expense(expense_id: str, req: ProjectExpensePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        e.category = req.category
        e.estimated = req.estimated
        e.actual = req.actual
        e.notes = req.notes
        await session.commit()
    return {"status": "ok"}


@router.delete("/project-expenses/{expense_id}")
async def delete_project_expense(expense_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        await session.delete(e)
        await session.commit()
    return {"status": "ok"}


@router.post("/project-expenses/{expense_id}/receipt")
async def upload_expense_receipt(expense_id: str, request: Request, file: UploadFile = File(...)):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    ext = os.path.splitext(file.filename or "img.jpg")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf"):
        raise HTTPException(status_code=400, detail="不支援的檔案格式")

    upload_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads", "receipts")
    os.makedirs(upload_dir, exist_ok=True)
    filename = f"{expense_id}{ext}"
    filepath = os.path.join(upload_dir, filename)

    content = await file.read()
    import pathlib
    await asyncio.to_thread(pathlib.Path(filepath).write_bytes, content)

    receipt_url = f"/uploads/receipts/{filename}"
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        e.receipt_url = receipt_url
        await session.commit()

    return {"status": "ok", "receipt_url": receipt_url}


@router.get("/projects/{project_id}/financial-summary")
async def project_financial_summary(project_id: str):
    """專案財務摘要：含稅/未稅/毛利/雜支/外包 預估vs實際。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

        from sqlalchemy import func as sa_func

        exp_row = (await session.execute(
            select(sa_func.coalesce(sa_func.sum(CrmProjectExpense.estimated), 0),
                   sa_func.coalesce(sa_func.sum(CrmProjectExpense.actual), 0))
            .where(CrmProjectExpense.project_id == project_id)
        )).first()
        expense_estimated = exp_row[0] if exp_row else 0
        expense_actual = exp_row[1] if exp_row else 0

        staff_row = (await session.execute(
            select(sa_func.coalesce(sa_func.sum(CrmProjectStaff.cost), 0),
                   sa_func.coalesce(sa_func.sum(sa_func.coalesce(CrmProjectStaff.actual_cost, CrmProjectStaff.cost)), 0))
            .where(CrmProjectStaff.project_id == project_id)
        )).first()
        staff_estimated = staff_row[0] if staff_row else 0
        staff_actual = staff_row[1] if staff_row else 0

    contract = project.contract_amount or 0
    tax_rate = project.tax_rate or 5
    ex_tax = round(contract / (1 + tax_rate / 100))
    profit_target = int(ex_tax * (project.profit_target_pct or 20) / 100)
    misc_budget = int(ex_tax * (project.misc_budget_pct or 5) / 100)
    outsource_budget = ex_tax - profit_target - misc_budget

    total_cost = expense_actual + staff_actual
    actual_profit = ex_tax - total_cost
    profit_rate = round(actual_profit / ex_tax * 100) if ex_tax > 0 else 0

    return {
        "contract_amount": contract, "ex_tax": ex_tax,
        "profit_target": profit_target, "profit_target_pct": project.profit_target_pct or 20,
        "misc_budget": misc_budget, "outsource_budget": outsource_budget,
        "expense_estimated": expense_estimated, "expense_actual": expense_actual,
        "staff_estimated": staff_estimated, "staff_actual": staff_actual,
        "total_cost": total_cost, "actual_profit": actual_profit, "profit_rate": profit_rate,
        "payment_status": project.payment_status or "未到帳",
        "amount_receivable": project.amount_receivable,
        "amount_received": project.amount_received,
        "transfer_fee": project.transfer_fee,
    }


# ── Invoice Helpers ─────────────────────────────────────────

def _to_invoice_dict(inv, project_name: str = "") -> dict:
    return {
        "id": inv.id, "payment_type": inv.payment_type or "收款",
        "payment_status": inv.payment_status or "", "issue_status": inv.issue_status or "",
        "invoice_number": inv.invoice_number or "", "title": inv.title or "",
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "applicant": inv.applicant or "", "category": inv.category or "專案",
        "invoice_kind": inv.invoice_kind or "", "amount_ex_tax": inv.amount_ex_tax,
        "amount_total": inv.amount_total, "tax_amount": inv.tax_amount,
        "commission": inv.commission, "company_name": inv.company_name or "",
        "tax_id": inv.tax_id or "", "item_type": inv.item_type or "",
        "project_id": inv.project_id or "", "project_name": project_name,
        "notes": inv.notes or "",
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
    }


# ── Invoice Endpoints ───────────────────────────────────────

@router.get("/invoices")
async def list_invoices(
    q: str = Query(""), payment_type: str = Query(""),
    category: str = Query(""), project_id: str = Query(""),
):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        query = (
            select(CrmInvoice, CrmProject.name.label("pn"))
            .outerjoin(CrmProject, CrmProject.id == CrmInvoice.project_id)
            .order_by(CrmInvoice.invoice_date.desc())
        )
        if payment_type:
            query = query.where(CrmInvoice.payment_type == payment_type)
        if category:
            query = query.where(CrmInvoice.category == category)
        if project_id:
            query = query.where(CrmInvoice.project_id == project_id)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(
                CrmInvoice.title.ilike(ql), CrmInvoice.company_name.ilike(ql),
                CrmInvoice.invoice_number.ilike(ql),
            ))
        rows = (await session.execute(query)).all()
    return {
        "invoices": [_to_invoice_dict(r[0], r[1] or "") for r in rows],
        "total": len(rows),
    }


@router.post("/invoices")
async def create_invoice(req: InvoicePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    now = _now()
    inv = CrmInvoice(
        id=uuid.uuid4().hex, invoice_date=_parse_shoot_date(req.invoice_date),
        created_at=now, updated_at=now,
        **req.model_dump(exclude={"invoice_date"}),
    )
    async with factory() as session:
        session.add(inv)
        await session.commit()
        await session.refresh(inv)
    return {"status": "ok", "invoice": _to_invoice_dict(inv)}


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        pn = ""
        if inv.project_id:
            p = await session.get(CrmProject, inv.project_id)
            pn = p.name if p else ""
    return _to_invoice_dict(inv, pn)


@router.put("/invoices/{invoice_id}")
async def update_invoice(invoice_id: str, req: InvoicePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        for k, v in req.model_dump(exclude={"invoice_date"}).items():
            setattr(inv, k, v)
        inv.invoice_date = _parse_shoot_date(req.invoice_date)
        inv.updated_at = _now()
        await session.commit()
    return {"status": "ok"}


@router.delete("/invoices/{invoice_id}")
async def delete_invoice(invoice_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        inv = await session.get(CrmInvoice, invoice_id)
        if not inv:
            raise HTTPException(status_code=404, detail="找不到此發票")
        await session.delete(inv)
        await session.commit()
    return {"status": "ok"}


# ── Invoice CSV Import ──────────────────────────────────────

_INVOICE_COL_MAP = {
    "payment_type":   ["款項狀態", "payment_type"],
    "issue_status":   ["開立狀態", "issue_status"],
    "invoice_number": ["發票編號", "invoice_number"],
    "invoice_date":   ["填表時間", "invoice_date", "日期"],
    "title":          ["名稱", "title", "案件名稱"],
    "applicant":      ["申請人", "applicant"],
    "category":       ["類別", "category"],
    "invoice_kind":   ["發票種類", "invoice_kind"],
    "amount_ex_tax":  ["未稅價", "amount_ex_tax"],
    "amount_total":   ["發票金額", "amount_total"],
    "tax_amount":     ["稅額", "tax_amount"],
    "commission":     ["代開應區", "commission"],
    "company_name":   ["抬頭", "company_name"],
    "tax_id":         ["統編", "tax_id"],
    "item_type":      ["品項", "item_type"],
}

_INVOICE_INT_FIELDS = {"amount_ex_tax", "amount_total", "tax_amount", "commission"}


def _map_invoice_row(header_map: dict, row: dict) -> dict:
    data = {}
    for field, aliases in _INVOICE_COL_MAP.items():
        for alias in aliases:
            orig = header_map.get(alias.lower())
            if orig and row.get(orig, "").strip():
                val = row[orig].strip()
                if field in _INVOICE_INT_FIELDS:
                    val = int(float(val)) if val.replace('.', '').replace('-', '').isdigit() else 0
                data[field] = val
                break
    # Derive payment_status from payment_type
    pt = data.get("payment_type", "")
    if "收" in pt:
        data["payment_status"] = "已收款"
        data["payment_type"] = "收款"
    elif "付" in pt:
        data["payment_status"] = "已付款"
        data["payment_type"] = "付款"
    elif "作廢" in pt:
        data["payment_status"] = "作廢"
    return data


@router.post("/invoices/import_csv")
async def import_invoices_csv(request: Request, file: UploadFile = File(...)):
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
    imported = skipped = 0
    factory = await _get_factory()

    async with factory() as session:
        for row in reader:
            data = _map_invoice_row(header_map, row)
            if not data.get("title"):
                skipped += 1
                continue
            now = _now()
            inv_date = _parse_shoot_date(data.pop("invoice_date", None))
            inv = CrmInvoice(id=uuid.uuid4().hex, invoice_date=inv_date,
                             created_at=now, updated_at=now, **data)
            session.add(inv)
            imported += 1
        await session.commit()
    return {"status": "ok", "imported": imported, "skipped": skipped}


# ── Payment Request Helpers ─────────────────────────────────

def _to_payment_dict(p, project_name: str = "") -> dict:
    return {
        "id": p.id, "request_date": p.request_date.isoformat() if p.request_date else None,
        "amount": p.amount, "summary": p.summary or "",
        "category": p.category or "專案外包",
        "payee_name": p.payee_name or "", "payee_id": p.payee_id or "",
        "payee_type": p.payee_type or "",
        "needs_invoice": p.needs_invoice, "invoice_number": p.invoice_number or "",
        "invoice_amount": p.invoice_amount,
        "project_id": p.project_id or "", "project_name": project_name,
        "project_label": p.project_label or "",
        "payment_date": p.payment_date.isoformat() if p.payment_date else None,
        "payment_status": p.payment_status or "未付款",
        "notes": p.notes or "",
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


# ── Payment Request Endpoints ──────────────────────────────

@router.get("/payments")
async def list_payments(
    q: str = Query(""), category: str = Query(""),
    payment_status: str = Query(""), project_id: str = Query(""),
):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        query = (
            select(CrmPaymentRequest, CrmProject.name.label("pn"))
            .outerjoin(CrmProject, CrmProject.id == CrmPaymentRequest.project_id)
            .order_by(CrmPaymentRequest.request_date.desc())
        )
        if category:
            query = query.where(CrmPaymentRequest.category == category)
        if payment_status:
            query = query.where(CrmPaymentRequest.payment_status == payment_status)
        if project_id:
            query = query.where(CrmPaymentRequest.project_id == project_id)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(
                CrmPaymentRequest.summary.ilike(ql),
                CrmPaymentRequest.payee_name.ilike(ql),
            ))
        rows = (await session.execute(query)).all()
    return {
        "payments": [_to_payment_dict(r[0], r[1] or "") for r in rows],
        "total": len(rows),
    }


@router.post("/payments")
async def create_payment(req: PaymentRequestPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    now = _now()
    date_fields = {"request_date", "payment_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    data = req.model_dump(exclude=date_fields)
    p = CrmPaymentRequest(id=uuid.uuid4().hex, **dates, created_at=now, updated_at=now, **data)
    async with factory() as session:
        session.add(p)
        await session.commit()
    return {"status": "ok", "payment": _to_payment_dict(p)}


@router.get("/payments/{payment_id}")
async def get_payment(payment_id: str):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p = await session.get(CrmPaymentRequest, payment_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此請款單")
        pn = ""
        if p.project_id:
            proj = await session.get(CrmProject, p.project_id)
            pn = proj.name if proj else ""
    return _to_payment_dict(p, pn)


@router.put("/payments/{payment_id}")
async def update_payment(payment_id: str, req: PaymentRequestPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    date_fields = {"request_date", "payment_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    async with factory() as session:
        p = await session.get(CrmPaymentRequest, payment_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此請款單")
        for k, v in req.model_dump(exclude=date_fields).items():
            setattr(p, k, v)
        for k, v in dates.items():
            setattr(p, k, v)
        p.updated_at = _now()
        await session.commit()
    return {"status": "ok"}


@router.delete("/payments/{payment_id}")
async def delete_payment(payment_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p = await session.get(CrmPaymentRequest, payment_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此請款單")
        await session.delete(p)
        await session.commit()
    return {"status": "ok"}


# ── Payment CSV Import ──────────────────────────────────────

_PAYMENT_COL_MAP = {
    "request_date":   ["日期", "request_date"],
    "amount":         ["請款", "金額", "amount"],
    "summary":        ["摘要", "summary"],
    "category":       ["項目", "類別", "category"],
    "payee_combined": ["收款人", "payee"],
    "payee_type":     ["狀態", "payee_type"],
    "invoice_number": ["發票號碼", "invoice_number"],
    "project_label":  ["專案標籤", "project_label"],
    "payment_date":   ["付款日", "payment_date"],
    "payment_status": ["付款狀態", "payment_status"],
    "notes":          ["附註", "備註", "notes"],
}


def _map_payment_row(header_map: dict, row: dict) -> dict:
    data = {}
    for field, aliases in _PAYMENT_COL_MAP.items():
        for alias in aliases:
            orig = header_map.get(alias.lower())
            if orig and row.get(orig, "").strip():
                val = row[orig].strip()
                if field == "amount":
                    val = int(float(val.replace(",", ""))) if val.replace(",", "").replace(".", "").replace("-", "").isdigit() else 0
                data[field] = val
                break
    # Parse combined payee field: "姓名_身分證" or just "姓名"
    combined = data.pop("payee_combined", "")
    if combined:
        parts = combined.split("_", 1)
        data["payee_name"] = parts[0]
        if len(parts) > 1:
            data["payee_id"] = parts[1]
    return data


@router.post("/payments/import_csv")
async def import_payments_csv(request: Request, file: UploadFile = File(...)):
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
    imported = skipped = 0
    factory = await _get_factory()

    async with factory() as session:
        for row in reader:
            data = _map_payment_row(header_map, row)
            if not data.get("summary") and not data.get("payee_name"):
                skipped += 1
                continue
            if not data.get("summary"):
                data["summary"] = data.get("payee_name", "")
            now = _now()
            req_date = _parse_shoot_date(data.pop("request_date", None))
            pay_date = _parse_shoot_date(data.pop("payment_date", None))
            p = CrmPaymentRequest(
                id=uuid.uuid4().hex, request_date=req_date, payment_date=pay_date,
                created_at=now, updated_at=now, **data,
            )
            session.add(p)
            imported += 1
        await session.commit()
    return {"status": "ok", "imported": imported, "skipped": skipped}


# ── Cash Entry (收支明細) ───────────────────────────────────

def _to_cash_dict(e, project_name: str = "") -> dict:
    return {
        "id": e.id,
        "entry_date": e.entry_date.isoformat() if e.entry_date else None,
        "expense": e.expense, "claim": e.claim, "deposit": e.deposit,
        "summary": e.summary or "", "note": e.note or "",
        "category": e.category or "", "item": e.item or "",
        "sub_item": e.sub_item or "", "payee": e.payee or "",
        "status": e.status or "",
        "has_invoice": e.has_invoice, "invoice_number": e.invoice_number or "",
        "project_label": e.project_label or "", "project_id": e.project_id or "",
        "project_name": project_name,
        "payment_date": e.payment_date.isoformat() if e.payment_date else None,
        "payment_status": e.payment_status or "",
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/cash-entries")
async def list_cash_entries(
    q: str = Query(""), category: str = Query(""),
    item: str = Query(""), project_id: str = Query(""),
):
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        query = (
            select(CrmCashEntry, CrmProject.name.label("pn"))
            .outerjoin(CrmProject, CrmProject.id == CrmCashEntry.project_id)
            .order_by(CrmCashEntry.entry_date.desc())
        )
        if category:
            query = query.where(CrmCashEntry.category == category)
        if item:
            query = query.where(CrmCashEntry.item == item)
        if project_id:
            query = query.where(CrmCashEntry.project_id == project_id)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(CrmCashEntry.summary.ilike(ql), CrmCashEntry.payee.ilike(ql)))
        rows = (await session.execute(query)).all()
    return {"entries": [_to_cash_dict(r[0], r[1] or "") for r in rows], "total": len(rows)}


@router.post("/cash-entries")
async def create_cash_entry(req: CashEntryPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    date_fields = {"entry_date", "payment_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    data = req.model_dump(exclude=date_fields)
    e = CrmCashEntry(id=uuid.uuid4().hex, **dates, created_at=_now(), **data)
    async with factory() as session:
        session.add(e)
        await session.commit()
    return {"status": "ok", "entry_id": e.id}


@router.put("/cash-entries/{entry_id}")
async def update_cash_entry(entry_id: str, req: CashEntryPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    date_fields = {"entry_date", "payment_date"}
    dates = {f: _parse_shoot_date(getattr(req, f)) for f in date_fields}
    async with factory() as session:
        e = await session.get(CrmCashEntry, entry_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此收支紀錄")
        for k, v in req.model_dump(exclude=date_fields).items():
            setattr(e, k, v)
        for k, v in dates.items():
            setattr(e, k, v)
        await session.commit()
    return {"status": "ok"}


@router.delete("/cash-entries/{entry_id}")
async def delete_cash_entry(entry_id: str, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await session.get(CrmCashEntry, entry_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此收支紀錄")
        await session.delete(e)
        await session.commit()
    return {"status": "ok"}


_CASH_COL_MAP = {
    "entry_date": ["日期"], "expense": ["支出"], "claim": ["請款"],
    "deposit": ["存入"], "summary": ["摘要"], "note": ["附註"],
    "category": ["類別"], "item": ["項目"], "sub_item": ["子項目"],
    "payee": ["收款人"], "status": ["狀態"],
    "invoice_number": ["發票號碼"], "project_label": ["專案標籤"],
    "payment_date": ["付款日"], "payment_status": ["付款狀態"],
}
_CASH_INT_FIELDS = {"expense", "claim", "deposit"}


def _map_cash_row(header_map: dict, row: dict) -> dict:
    data = {}
    for field, aliases in _CASH_COL_MAP.items():
        for alias in aliases:
            orig = header_map.get(alias.lower())
            if orig and row.get(orig, "").strip():
                val = row[orig].strip()
                if field in _CASH_INT_FIELDS:
                    clean = val.replace(",", "").replace("(", "").replace(")", "")
                    data[field] = int(float(clean)) if clean.replace(".", "").replace("-", "").isdigit() else 0
                else:
                    data[field] = val
                break
    return data


@router.post("/cash-entries/import_csv")
async def import_cash_csv(request: Request, file: UploadFile = File(...)):
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
    imported = skipped = 0
    factory = await _get_factory()

    async with factory() as session:
        for row in reader:
            data = _map_cash_row(header_map, row)
            if not data.get("summary"):
                skipped += 1
                continue
            entry_date = _parse_shoot_date(data.pop("entry_date", None))
            pay_date = _parse_shoot_date(data.pop("payment_date", None))
            e = CrmCashEntry(id=uuid.uuid4().hex, entry_date=entry_date,
                             payment_date=pay_date, created_at=_now(), **data)
            session.add(e)
            imported += 1
        await session.commit()
    return {"status": "ok", "imported": imported, "skipped": skipped}


# ── Accounts Payable (應付帳款) ─────────────────────────────

@router.get("/payables/summary")
async def payables_summary(month: str = Query(""), status: str = Query("")):
    """當月請款彙總，按收款人分組，帶入銀行資訊。status=未付款 or 已付款 or 空=全部。"""
    _require_db()
    factory = await _get_factory()

    if not month:
        now = _now()
        month = f"{now.year}-{now.month:02d}"
    parts = month.split("-")
    year, mon = int(parts[0]), int(parts[1])
    from calendar import monthrange
    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    _, last_day = monthrange(year, mon)
    end = datetime(year, mon, last_day, 23, 59, 59, tzinfo=timezone.utc)

    async with factory() as session:
        query = (
            select(CrmPaymentRequest)
            .where(CrmPaymentRequest.request_date >= start)
            .where(CrmPaymentRequest.request_date <= end)
            .order_by(CrmPaymentRequest.request_date)
        )
        if status:
            query = query.where(CrmPaymentRequest.payment_status == status)
        rows = (await session.execute(query)).scalars().all()

        staff_map = {s.name: s for s in (await session.execute(select(CrmStaff))).scalars().all()}

    payee_groups = {}
    for p in rows:
        name = p.payee_name or "未指定"
        if name not in payee_groups:
            staff = staff_map.get(name)
            payee_groups[name] = {
                "payee_name": name,
                "payee_id": p.payee_id or (staff.id_number if staff else "") or "",
                "bank_name": (staff.bank_name if staff else "") or "",
                "bank_account": (staff.bank_account if staff else "") or "",
                "total_amount": 0, "items": [],
            }
        payee_groups[name]["total_amount"] += p.amount or 0
        payee_groups[name]["items"].append({
            "id": p.id,
            "date": p.request_date.strftime("%Y/%m/%d") if p.request_date else "",
            "amount": p.amount or 0,
            "summary": p.summary or "",
            "category": p.category or "",
            "payment_status": p.payment_status or "",
        })

    payees = sorted(payee_groups.values(), key=lambda x: x["payee_name"])
    return {"month": month, "payees": payees, "grand_total": sum(pg["total_amount"] for pg in payees)}


@router.patch("/payments/batch-pay")
async def batch_pay(request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("payment_ids", [])
    pay_date = _parse_shoot_date(body.get("payment_date", "")) or _now()
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 payment_ids")

    async with factory() as session:
        updated = 0
        for pid in ids:
            p = await session.get(CrmPaymentRequest, pid)
            if p and p.payment_status != "已付款":
                p.payment_status = "已付款"
                p.payment_date = pay_date
                p.updated_at = _now()
                updated += 1
        await session.commit()
    return {"status": "ok", "updated": updated}
