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
# Lazy annotations (PEP 563) — keeps function signatures parsing on agents
# without sqlalchemy / asyncpg installed. db.models classes referenced as
# type hints would otherwise NameError when the optional DB import branch
# fails (per the try/except below), preventing the whole CRM router from
# loading. See bug: agent 192.168.1.5 had this exact failure mode.
from __future__ import annotations

import asyncio
import csv
import io
import os
import re
import shutil
import uuid
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Query

from config import load_settings as _load_settings

import core.state as state

try:
    from sqlalchemy import select, or_, delete, update as sa_update
    from sqlalchemy.exc import IntegrityError
    from db.models import (Client, User, CrmProject, CrmQuotation, CrmQuotationItem,
                           CrmQuotationTemplate, CrmStaff, CrmStaffPortfolio,
                           CrmProjectStaff, CrmProjectExpense,
                           CrmInvoice, CrmPaymentRequest, CrmCashEntry,
                           CrmProjectCostLine, CrmCostLineTemplate,
                           CrmProjectCostGroup,
                           CrmProjectShowcase)
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


from core.schemas import (ClientPayload, CrmProjectPayload, CrmProjectPatchPayload,
                         ProjectExpensePayload,
                         ProjectExpensePatchPayload,
                         QuotationPayload, QuotationItemPayload, QuotationTemplatePayload,
                         StaffPayload, ProjectStaffPayload, InvoicePayload, PaymentRequestPayload,
                         CashEntryPayload, CostLinePayload, CostLineUpdatePayload,
                         CostGroupCreate, CostGroupUpdate, CostGroupDuplicate,
                         ResumePayload, PortfolioPayload, ShowcasePayload)


async def _auto_update_client_status(session, client_id: str):
    """根據專案數量自動更新客戶狀態：0=潛在客戶, 1=新客戶, 2+=舊客戶"""
    from sqlalchemy import func as _fn
    count = (await session.execute(
        select(_fn.count()).where(CrmProject.client_id == client_id)
    )).scalar() or 0
    client = await session.get(Client, client_id)
    if not client:
        return
    if count == 0:
        client.status = "潛在客戶"
    elif count == 1:
        client.status = "新客戶"
    else:
        client.status = "舊客戶"


# ── Endpoints ────────────────────────────────────────────────

@router.get("/clients")
async def list_clients(
    q: str = Query(""),
    status: str = Query(""),
    am: str = Query(""),
):
    _require_db()
    factory = await _get_factory()

    from sqlalchemy import func as _fn, case as _case

    # Subquery: per-client project stats (exclude 洽談中/報價中)
    active_filter = CrmProject.status.notin_(["洽談中", "報價中"])
    proj_sub = (
        select(
            CrmProject.client_id,
            _fn.count(_case((active_filter, 1))).label("project_count"),
            _fn.coalesce(_fn.sum(_case((active_filter, CrmProject.contract_amount), else_=0)), 0).label("total_contract"),
        )
        .group_by(CrmProject.client_id)
        .subquery()
    )

    async with factory() as session:
        query = (
            select(Client, proj_sub.c.project_count, proj_sub.c.total_contract)
            .outerjoin(proj_sub, proj_sub.c.client_id == Client.id)
            .order_by(Client.updated_at.desc())
        )
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
        rows = (await session.execute(query)).all()

    result = []
    for c, proj_count, total_contract in rows:
        d = _to_dict(c)
        d["project_count"] = proj_count or 0
        d["total_contract"] = total_contract or 0
        result.append(d)
    return {"clients": result, "total": len(result)}


@router.post("/clients")
async def create_client(req: ClientPayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    now = _now()
    async with factory() as session:
        # Check duplicate by full_name + tax_id
        full_name = (req.full_name or "").strip()
        tax_id = (req.tax_id or "").strip()
        if full_name or tax_id:
            dup = (await session.execute(
                select(Client).where(Client.full_name == full_name, Client.tax_id == tax_id)
            )).scalars().first()
            if dup:
                raise HTTPException(status_code=409,
                    detail=f"已存在相同抬頭＋統編的客戶「{dup.short_name}」")

        client = Client(id=uuid.uuid4().hex, created_at=now, updated_at=now, **req.model_dump())
        try:
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
        old_am = client.am_username
        for k, v in req.model_dump().items():
            setattr(client, k, v)
        client.updated_at = _now()
        await session.commit()
        await session.refresh(client)

        # Sync AM to active projects when client AM changed
        if req.am_username and req.am_username != old_am:
            await session.execute(
                sa_update(CrmProject)
                .where(CrmProject.client_id == client_id)
                .where(CrmProject.status.in_(["進行中", "報價中", "洽談中"]))
                .values(am_username=req.am_username)
            )
            await session.commit()

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
    "short_name":        ["客戶代稱", "name", "客戶名稱", "名稱", "客戶", "代稱", "簡稱"],
    "full_name":         ["全稱", "full_name", "抬頭", "legal_name", "發票抬頭", "公司名稱", "公司", "公司全稱", "公司抬頭"],
    "tax_id":            ["統編", "統一編號", "tax_id", "統編/身分證", "統編/身份證"],
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

    # Auto-detect delimiter
    first_line = text.split('\n', 1)[0].strip()
    dialect = None
    if first_line:
        try:
            dialect = csv.Sniffer().sniff(first_line, delimiters=',\t;')
        except csv.Error:
            pass
    reader = csv.DictReader(io.StringIO(text), dialect=dialect) if dialect else csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = headers
    header_map = {h.lower(): h for h in headers if h}
    imported = updated = skipped = 0

    factory = await _get_factory()

    async with factory() as session:
        # Build dedup index: (full_name, tax_id) → Client
        all_clients = (await session.execute(select(Client))).scalars().all()
        existing_keys = {(c.full_name or "", c.tax_id or "") for c in all_clients}

        for row in reader:
            data = _map_row(header_map, row)
            full_name = data.get("full_name", "").strip()
            tax_id = data.get("tax_id", "").strip()

            if not full_name and not tax_id:
                skipped += 1
                continue

            # Dedup by (抬頭, 統編)
            key = (full_name, tax_id)
            if key in existing_keys:
                skipped += 1
                continue

            # No short_name → use full_name
            if not data.get("short_name"):
                data["short_name"] = full_name or tax_id

            now = _now()
            new_client = Client(id=uuid.uuid4().hex, created_at=now, updated_at=now, **data)
            session.add(new_client)
            existing_keys.add(key)
            imported += 1

        await session.commit()

    result = {"status": "ok", "imported": imported, "updated": updated, "skipped": skipped}
    if skipped > 0 and imported == 0:
        result["hint"] = f"CSV 欄位：{[h for h in headers if h]}。需包含「抬頭」或「統編」欄位才能匯入。若全部重複則跳過。"
    return result


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
        "receipt_path": p.receipt_path or "",
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

        # Inherit AM from client if not explicitly set
        if not project.am_username and client.am_username:
            project.am_username = client.am_username

        session.add(project)
        await _auto_update_client_status(session, req.client_id)
        # 同時建立第一張子表「主表」+ 預設 10 個行政雜支類別 row。
        # 共用同一次 commit。
        main_group_id = uuid.uuid4().hex
        session.add(CrmProjectCostGroup(
            id=main_group_id, project_id=project.id, name="主表", sort_order=0,
        ))
        await _seed_default_expenses(session, project.id, main_group_id)
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
async def update_project(project_id: str, req: CrmProjectPatchPayload, request: Request):
    """Partial update — only fields the client included in the body are
    touched. The cell-by-cell auto-save sends just the dirty field, so a
    full-payload schema would 422 on missing name/client_id."""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()

    date_fields = {"shoot_date", "start_date", "completion_date"}
    update_data = req.model_dump(exclude_unset=True)

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

        # If client_id is changing, validate the new client exists.
        if "client_id" in update_data:
            new_client = await session.get(Client, update_data["client_id"])
            if not new_client:
                raise HTTPException(status_code=404, detail="找不到指定的客戶")

        for k, v in update_data.items():
            if k in date_fields:
                setattr(project, k, _parse_shoot_date(v))
            else:
                setattr(project, k, v)
        project.updated_at = _now()
        await session.commit()
        await session.refresh(project)
        client = await session.get(Client, project.client_id)
        client_name = client.short_name if client else ""

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
        client_id = project.client_id
        await session.delete(project)
        await _auto_update_client_status(session, client_id)
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

    contract_amount = body.get("contract_amount")
    amount_receivable = body.get("amount_receivable")

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        project.status = new_status
        if contract_amount is not None:
            project.contract_amount = int(contract_amount)
        if amount_receivable is not None:
            project.amount_receivable = int(amount_receivable)
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

    from sqlalchemy import func as sa_func, case

    async with factory() as session:
        price_col = sa_func.coalesce(CrmQuotation.final_price, CrmQuotation.total, 0)
        row = (await session.execute(
            select(
                sa_func.count().label("total_count"),
                sa_func.sum(case((CrmQuotation.status == "已寄送", 1), else_=0)).label("pending"),
                sa_func.sum(case((CrmQuotation.status == "已簽核", 1), else_=0)).label("signed"),
                sa_func.sum(case((CrmQuotation.created_at >= month_start, price_col), else_=0)).label("month_total"),
            )
        )).one()

    total_count = row.total_count or 0
    signed = row.signed or 0
    return {
        "month_total": row.month_total or 0, "pending_count": row.pending or 0,
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
    return {"quotations": [_to_quotation_dict(q) for q in rows], "total": len(rows)}


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
        "photo_url": s.photo_url or "",
        "bio": s.bio or "",
        "skills": s.skills or [],
        "education": s.education or [],
        "experience": s.experience or [],
        "awards": s.awards or [],
        "resume_visible": bool(s.resume_visible) if s.resume_visible is not None else False,
        "edit_token": s.edit_token or "",
        "resume_editable": bool(s.resume_editable) if s.resume_editable is not None else True,
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
    "name":          ["姓名", "收款人", "收款人姓名", "人員", "員工", "name"],
    "role":          ["職能", "角色", "職稱", "role"],
    "daily_rate":    ["日費", "daily_rate"],
    "hourly_rate":   ["時薪", "hourly_rate"],
    "phone":         ["電話", "聯絡電話", "手機", "phone"],
    "email":         ["email", "信箱", "電子信箱", "e-mail"],
    "id_number":     ["身分證", "身份證字號", "身分證字號", "身分證號碼", "id_number"],
    "address":       ["住址", "地址", "通訊地址", "address"],
    "bank_name":     ["銀行", "銀行名稱", "匯款銀行", "bank_name"],
    "bank_account":  ["帳號", "銀行帳號", "匯款帳號", "bank_account"],
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

    # Auto-detect delimiter (comma, tab, semicolon)
    first_line = text.split('\n', 1)[0].strip()
    dialect = None
    if first_line:
        try:
            dialect = csv.Sniffer().sniff(first_line, delimiters=',\t;')
        except csv.Error:
            pass
    reader = csv.DictReader(io.StringIO(text), dialect=dialect) if dialect else csv.DictReader(io.StringIO(text))
    headers = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = headers
    header_map = {h.lower(): h for h in headers if h}
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
    result = {"status": "ok", "imported": imported, "updated": updated, "skipped": skipped}
    if skipped > 0 and imported == 0 and updated == 0:
        result["hint"] = f"CSV 欄位：{headers}，未能匹配「姓名」欄位。請確認 CSV 包含「姓名」或「收款人」欄位。"
    return result


# ── Staff Resume / Portfolio Endpoints ─────────────────────

_UPLOAD_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


async def _save_image(file, dest_dir: str, filename: str) -> str:
    """Validate image extension, save to dest_dir/filename.ext, return relative URL."""
    ext = (os.path.splitext(file.filename or "img.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"{filename}{ext}")
    content = await file.read()
    with open(path, "wb") as fp:
        fp.write(content)
    # Return path relative to uploads/
    rel = os.path.relpath(path, os.path.dirname(_UPLOAD_BASE)).replace("\\", "/")
    return "/" + rel


def _to_portfolio_dict(p) -> dict:
    return {
        "id": p.id,
        "staff_id": p.staff_id,
        "title": p.title,
        "url": p.url or "",
        "thumbnail_url": p.thumbnail_url or "",
        "role_desc": p.role_desc or "",
        "sort_order": p.sort_order or 0,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.put("/staff/{staff_id}/resume")
async def update_staff_resume(staff_id: str, req: ResumePayload, request: Request):
    """更新人員履歷/簡歷資訊。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if not s:
            raise HTTPException(status_code=404, detail="找不到此人員")
        s.bio = req.bio
        s.skills = req.skills
        s.education = req.education
        s.experience = req.experience
        s.awards = req.awards
        s.resume_visible = req.resume_visible
        s.resume_editable = req.resume_editable
        s.updated_at = _now()
        await session.commit()
        await session.refresh(s)
    return {"status": "ok", "staff": _to_staff_dict(s)}


@router.post("/staff/{staff_id}/photo")
async def upload_staff_photo(staff_id: str, request: Request, file: UploadFile = File(...)):
    """上傳人員照片。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if not s:
            raise HTTPException(status_code=404, detail="找不到此人員")
    ext = (os.path.splitext(file.filename or "photo.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    if not staff_id.isalnum():
        raise HTTPException(status_code=400, detail="無效的人員 ID")
    upload_dir = os.path.join(_UPLOAD_BASE, "staff", staff_id)
    os.makedirs(upload_dir, exist_ok=True)
    # Remove old photos
    for f in os.listdir(upload_dir):
        if f.startswith("photo."):
            try:
                os.remove(os.path.join(upload_dir, f))
            except OSError:
                pass
    dest = os.path.join(upload_dir, f"photo{ext}")
    content = await file.read()
    with open(dest, "wb") as fp:
        fp.write(content)
    url = f"/uploads/staff/{staff_id}/photo{ext}"
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if s:
            s.photo_url = url
            s.updated_at = _now()
            await session.commit()
    return {"status": "ok", "photo_url": url}


@router.get("/staff/{staff_id}/portfolio")
async def list_staff_portfolio(staff_id: str, request: Request):
    """列出人員的作品集。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmStaffPortfolio)
            .where(CrmStaffPortfolio.staff_id == staff_id)
            .order_by(CrmStaffPortfolio.sort_order)
        )).scalars().all()
    return {"items": [_to_portfolio_dict(p) for p in rows]}


@router.post("/staff/{staff_id}/portfolio")
async def add_staff_portfolio(staff_id: str, request: Request,
                              title: str = Query(...), url: str = Query(...),
                              role_desc: str = Query(""), sort_order: int = Query(0),
                              thumbnail: Optional[UploadFile] = File(None)):
    """新增作品集項目（支援可選的縮圖上傳）。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    # Verify staff exists
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if not s:
            raise HTTPException(status_code=404, detail="找不到此人員")
    item_id = uuid.uuid4().hex
    thumbnail_url = ""
    if thumbnail and thumbnail.filename:
        ext = (os.path.splitext(thumbnail.filename)[1] or ".jpg").lower()
        if ext not in _ALLOWED_IMG_EXT:
            raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
        thumb_dir = os.path.join(_UPLOAD_BASE, "staff", staff_id, "portfolio")
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_path = os.path.join(thumb_dir, f"{item_id}{ext}")
        content = await thumbnail.read()
        with open(thumb_path, "wb") as fp:
            fp.write(content)
        thumbnail_url = f"/uploads/staff/{staff_id}/portfolio/{item_id}{ext}"
    p = CrmStaffPortfolio(
        id=item_id, staff_id=staff_id, title=title, url=url,
        thumbnail_url=thumbnail_url, role_desc=role_desc, sort_order=sort_order,
        created_at=_now(),
    )
    async with factory() as session:
        session.add(p)
        await session.commit()
        await session.refresh(p)
    return {"status": "ok", "item": _to_portfolio_dict(p)}


@router.put("/staff-portfolio/{item_id}")
async def update_staff_portfolio(item_id: str, request: Request,
                                 title: str = Query(...), url: str = Query(...),
                                 role_desc: str = Query(""), sort_order: int = Query(0),
                                 thumbnail: Optional[UploadFile] = File(None)):
    """更新作品集項目（支援可選的縮圖上傳）。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p = await session.get(CrmStaffPortfolio, item_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此作品集項目")
        p.title = title
        p.url = url
        p.role_desc = role_desc
        p.sort_order = sort_order
        if thumbnail and thumbnail.filename:
            ext = (os.path.splitext(thumbnail.filename)[1] or ".jpg").lower()
            if ext not in _ALLOWED_IMG_EXT:
                raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
            thumb_dir = os.path.join(_UPLOAD_BASE, "staff", p.staff_id, "portfolio")
            os.makedirs(thumb_dir, exist_ok=True)
            thumb_path = os.path.join(thumb_dir, f"{item_id}{ext}")
            content = await thumbnail.read()
            with open(thumb_path, "wb") as fp:
                fp.write(content)
            p.thumbnail_url = f"/uploads/staff/{p.staff_id}/portfolio/{item_id}{ext}"
        await session.commit()
        await session.refresh(p)
    return {"status": "ok", "item": _to_portfolio_dict(p)}


@router.delete("/staff-portfolio/{item_id}")
async def delete_staff_portfolio(item_id: str, request: Request):
    """刪除作品集項目。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        p = await session.get(CrmStaffPortfolio, item_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此作品集項目")
        await session.delete(p)
        await session.commit()
    return {"status": "ok"}


@router.get("/public/staff/{staff_id}/resume")
async def get_public_staff_resume(staff_id: str):
    """公開履歷頁面（無需認證），僅限 resume_visible=True 的人員。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if not s or not s.resume_visible:
            raise HTTPException(status_code=404, detail="找不到此人員的公開履歷")
        staff = _to_staff_dict(s)
        # Portfolio items
        rows = (await session.execute(
            select(CrmStaffPortfolio)
            .where(CrmStaffPortfolio.staff_id == staff_id)
            .order_by(CrmStaffPortfolio.sort_order)
        )).scalars().all()
        portfolio = [_to_portfolio_dict(p) for p in rows]
        # Project history
        proj_rows = (await session.execute(
            select(CrmProjectStaff, CrmProject.name.label("project_name"),
                   Client.short_name.label("client_name"))
            .outerjoin(CrmProject, CrmProject.id == CrmProjectStaff.project_id)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .where(CrmProjectStaff.staff_id == staff_id)
        )).all()
        projects = [{
            "project_name": r[1] or "", "client_name": r[2] or "",
            "role_in_project": r[0].role_in_project or "",
            "days": r[0].days,
        } for r in proj_rows]
    # Strip sensitive fields for public view
    for key in ("id_number", "address", "bank_name", "bank_account", "phone", "email"):
        staff.pop(key, None)
    return {"staff": staff, "portfolio": portfolio, "projects": projects}


@router.get("/staff/{staff_id}/resume-pdf")
async def staff_resume_pdf(staff_id: str):
    """匯出人員履歷 PDF（無需認證，僅限 resume_visible=True）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if not s or not s.resume_visible:
            raise HTTPException(status_code=404, detail="找不到此人員的公開履歷")
        staff = _to_staff_dict(s)
        # Portfolio items
        rows = (await session.execute(
            select(CrmStaffPortfolio)
            .where(CrmStaffPortfolio.staff_id == staff_id)
            .order_by(CrmStaffPortfolio.sort_order)
        )).scalars().all()
        portfolio = [_to_portfolio_dict(p) for p in rows]
        # Project history
        proj_rows = (await session.execute(
            select(CrmProjectStaff, CrmProject.name.label("project_name"),
                   Client.short_name.label("client_name"))
            .outerjoin(CrmProject, CrmProject.id == CrmProjectStaff.project_id)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .where(CrmProjectStaff.staff_id == staff_id)
        )).all()
        projects = [{
            "project_name": r[1] or "", "client_name": r[2] or "",
            "role_in_project": r[0].role_in_project or "",
            "days": r[0].days,
        } for r in proj_rows]
    # Strip sensitive fields
    for key in ("id_number", "address", "bank_name", "bank_account", "phone", "email"):
        staff.pop(key, None)

    # Render Jinja2 template → HTML string
    from jinja2 import Environment, FileSystemLoader, select_autoescape as _sa
    _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _env = Environment(loader=FileSystemLoader(os.path.join(_base, "templates")),
                       autoescape=_sa(["html"]))
    _tmpl = _env.get_template("resume_pdf.html")
    html_content = _tmpl.render(
        staff=staff, portfolio=portfolio, projects=projects,
        generated_at=datetime.now().strftime("%Y-%m-%d"),
    )

    # Write to temp file, convert to PDF via Playwright
    import tempfile
    tmp_fd, tmp_html = tempfile.mkstemp(suffix=".html", prefix="resume_")
    os.close(tmp_fd)
    with open(tmp_html, "w", encoding="utf-8") as f:
        f.write(html_content)

    tmp_pdf = tmp_html.replace(".html", ".pdf")
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            file_url = f"file:///{tmp_html.replace(os.sep, '/')}"
            await page.goto(file_url, wait_until="networkidle")
            await page.pdf(
                path=tmp_pdf, format="A4", print_background=True,
                margin={"top": "20mm", "bottom": "20mm",
                        "left": "15mm", "right": "15mm"},
            )
            await browser.close()
    except Exception as exc:
        # Clean up temp files on error
        for _f in (tmp_html, tmp_pdf):
            try:
                os.unlink(_f)
            except OSError:
                pass
        raise HTTPException(status_code=500,
                            detail=f"PDF 生成失敗：{exc}")
    finally:
        # Always remove the temp HTML
        try:
            os.unlink(tmp_html)
        except OSError:
            pass

    from starlette.responses import FileResponse
    from starlette.background import BackgroundTask
    safe_name = (staff.get("name") or "staff").replace(" ", "_")
    return FileResponse(
        tmp_pdf, media_type="application/pdf",
        filename=f"{safe_name}_Resume.pdf",
        background=BackgroundTask(lambda: os.unlink(tmp_pdf) if os.path.exists(tmp_pdf) else None),
    )


# ── Staff Self-Edit via Token ─────────────────────────────

async def _verify_token_generic(session, token: str, scope: str, model_cls, editable_attr: str, require_editable: bool = False):
    """Generic token verification for staff resume / showcase edit tokens."""
    from core.auth import verify_token
    payload = verify_token(token)
    if not payload or payload.get('scope') != scope:
        raise HTTPException(status_code=401, detail="無效的連結")
    obj = await session.get(model_cls, payload.get('sub', ''))
    if not obj or obj.edit_token != token:
        raise HTTPException(status_code=401, detail="連結已失效")
    if require_editable and not getattr(obj, editable_attr, True):
        raise HTTPException(status_code=403, detail="管理員已關閉編輯權限")
    return obj


async def _verify_edit_token(session, token: str, require_editable: bool = False):
    return await _verify_token_generic(session, token, 'resume_edit', CrmStaff, 'resume_editable', require_editable)


@router.post("/staff/{staff_id}/generate-edit-token")
async def generate_staff_edit_token(staff_id: str, request: Request):
    """產生人員自編履歷的永久連結 Token。"""
    _check_auth(request)
    _require_db()
    from core.auth import create_token
    token = create_token({"sub": staff_id, "scope": "resume_edit"}, expires_days=36500)
    factory = await _get_factory()
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if not s:
            raise HTTPException(status_code=404, detail="找不到此人員")
        s.edit_token = token
        s.updated_at = _now()
        await session.commit()
    return {"status": "ok", "token": token, "url": f"/staff-edit.html?token={token}"}


@router.get("/public/staff-edit/{token}")
async def get_staff_edit_data(token: str):
    """透過 Token 取得人員履歷資料（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await _verify_edit_token(session, token, require_editable=False)
        staff = _to_staff_dict(s)
        # Portfolio items
        rows = (await session.execute(
            select(CrmStaffPortfolio)
            .where(CrmStaffPortfolio.staff_id == s.id)
            .order_by(CrmStaffPortfolio.sort_order)
        )).scalars().all()
        portfolio = [_to_portfolio_dict(p) for p in rows]
    # Strip sensitive fields
    for key in ("id_number", "address", "bank_name", "bank_account",
                "phone", "email", "daily_rate", "hourly_rate"):
        staff.pop(key, None)
    return {"staff": staff, "portfolio": portfolio, "editable": bool(s.resume_editable) if s.resume_editable is not None else True}


@router.put("/public/staff-edit/{token}")
async def update_staff_edit_data(token: str, req: ResumePayload):
    """透過 Token 更新人員履歷資料（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await _verify_edit_token(session, token, require_editable=True)
        s.bio = req.bio
        s.skills = req.skills
        s.education = req.education
        s.experience = req.experience
        s.awards = req.awards
        # Do NOT allow changing resume_visible or resume_editable (admin-only)
        s.updated_at = _now()
        await session.commit()
    return {"status": "ok"}


@router.post("/public/staff-edit/{token}/photo")
async def upload_staff_edit_photo(token: str, file: UploadFile = File(...)):
    """透過 Token 上傳人員照片（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await _verify_edit_token(session, token, require_editable=True)
        staff_id = s.id
    ext = (os.path.splitext(file.filename or "photo.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    if not staff_id.isalnum():
        raise HTTPException(status_code=400, detail="無效的人員 ID")
    upload_dir = os.path.join(_UPLOAD_BASE, "staff", staff_id)
    os.makedirs(upload_dir, exist_ok=True)
    for f in os.listdir(upload_dir):
        if f.startswith("photo."):
            try:
                os.remove(os.path.join(upload_dir, f))
            except OSError:
                pass
    dest = os.path.join(upload_dir, f"photo{ext}")
    content = await file.read()
    with open(dest, "wb") as fp:
        fp.write(content)
    url = f"/uploads/staff/{staff_id}/photo{ext}"
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if s:
            s.photo_url = url
            s.updated_at = _now()
            await session.commit()
    return {"status": "ok", "photo_url": url}


@router.post("/public/staff-edit/{token}/portfolio")
async def add_staff_edit_portfolio(token: str,
                                   title: str = Query(...), url: str = Query(...),
                                   role_desc: str = Query(""), sort_order: int = Query(0),
                                   thumbnail: Optional[UploadFile] = File(None)):
    """透過 Token 新增作品集項目（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await _verify_edit_token(session, token, require_editable=True)
        staff_id = s.id
    item_id = uuid.uuid4().hex
    thumbnail_url = ""
    if thumbnail and thumbnail.filename:
        ext = (os.path.splitext(thumbnail.filename)[1] or ".jpg").lower()
        if ext not in _ALLOWED_IMG_EXT:
            raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
        thumb_dir = os.path.join(_UPLOAD_BASE, "staff", staff_id, "portfolio")
        os.makedirs(thumb_dir, exist_ok=True)
        thumb_path = os.path.join(thumb_dir, f"{item_id}{ext}")
        content = await thumbnail.read()
        with open(thumb_path, "wb") as fp:
            fp.write(content)
        thumbnail_url = f"/uploads/staff/{staff_id}/portfolio/{item_id}{ext}"
    p = CrmStaffPortfolio(
        id=item_id, staff_id=staff_id, title=title, url=url,
        thumbnail_url=thumbnail_url, role_desc=role_desc, sort_order=sort_order,
        created_at=_now(),
    )
    async with factory() as session:
        session.add(p)
        await session.commit()
        await session.refresh(p)
    return {"status": "ok", "item": _to_portfolio_dict(p)}


@router.delete("/public/staff-edit/{token}/portfolio/{item_id}")
async def delete_staff_edit_portfolio(token: str, item_id: str):
    """透過 Token 刪除作品集項目（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await _verify_edit_token(session, token, require_editable=True)
        p = await session.get(CrmStaffPortfolio, item_id)
        if not p:
            raise HTTPException(status_code=404, detail="找不到此作品集項目")
        if p.staff_id != s.id:
            raise HTTPException(status_code=403, detail="無權限刪除此作品集項目")
        await session.delete(p)
        await session.commit()
    return {"status": "ok"}


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
async def list_project_expenses(project_id: str, group_id: Optional[str] = Query(None)):
    """列出專案雜支。
    - 無 group_id：回整個專案的雜支 + 多一層 grouped_by_group
    - 有 group_id：只回該子表的雜支"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        q = select(CrmProjectExpense).where(CrmProjectExpense.project_id == project_id)
        if group_id:
            q = q.where(CrmProjectExpense.cost_group_id == group_id)
        rows = (await session.execute(q)).scalars().all()

        groups = (await session.execute(
            select(CrmProjectCostGroup)
            .where(CrmProjectCostGroup.project_id == project_id)
            .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        )).scalars().all()

    def _e_to_dict(e):
        return {
            "id": e.id, "category": e.category,
            "cost_group_id": e.cost_group_id or "",
            "estimated": e.estimated, "actual": e.actual,
            "sub_item": e.sub_item or "", "payee": e.payee or "",
            "advance_id": e.advance_id or "",
            "receipt_url": e.receipt_url or "", "notes": e.notes or "",
            "created_at": _fmt_date(e.created_at),
        }

    expenses = [_e_to_dict(e) for e in rows]
    grouped_by_group = [{
        "group_id": g.id, "group_name": g.name,
        "shoot_date": _fmt_date(g.shoot_date),
        "expenses": [_e_to_dict(e) for e in rows if e.cost_group_id == g.id],
    } for g in groups]
    return {"expenses": expenses, "grouped_by_group": grouped_by_group}


async def _create_expense(session, project_id: str, req, advance_id=None, payee_override=None):
    """建立專案雜支的共用 helper。"""
    cost_group_id = await _resolve_target_group(session, project_id, req.cost_group_id)
    e = CrmProjectExpense(
        id=uuid.uuid4().hex, project_id=project_id,
        cost_group_id=cost_group_id,
        category=req.category, estimated=req.estimated,
        actual=req.actual, sub_item=req.sub_item or None,
        payee=payee_override or req.payee or None,
        advance_id=advance_id or req.advance_id or None,
        notes=req.notes, created_at=_now(),
    )
    session.add(e)
    await session.commit()
    return e


@router.post("/advance/{advance_id}/expenses")
async def add_advance_expense(advance_id: str, req: ProjectExpensePayload):
    """公開端點：透過預支款 ID 登記支出（不需登入）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        adv = await session.get(CrmPaymentRequest, advance_id)
        if not adv or not adv.is_advance:
            raise HTTPException(status_code=404, detail="找不到此預支款")
        if not adv.project_id:
            raise HTTPException(status_code=400, detail="此預支款未綁定專案")
        e = await _create_expense(session, adv.project_id, req, advance_id=advance_id, payee_override=adv.payee_name)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}}


@router.post("/public/projects/{project_id}/expenses")
async def add_public_project_expense(project_id: str, req: ProjectExpensePayload):
    """公開端點：透過專案 ID 登記雜支（不需登入）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        e = await _create_expense(session, project_id, req)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}}


@router.get("/public/cost-groups/{group_id}/info")
async def get_public_cost_group_info(group_id: str):
    """公開端點：取得子表 + 所屬專案資訊（不需登入，供 /group-expense.html 使用）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        proj = await session.get(CrmProject, g.project_id)
    return {
        "group": {
            "id": g.id, "name": g.name,
            "shoot_date": _fmt_date(g.shoot_date),
            "notes": g.notes or "",
            "budget_amount": g.budget_amount,
            "misc_budget_amount": g.misc_budget_amount,
        },
        "project": {"id": proj.id, "name": proj.name} if proj else None,
    }


@router.get("/public/cost-groups/{group_id}/expenses")
async def list_public_cost_group_expenses(group_id: str):
    """公開端點：列出該子表最近 20 筆已登記雜支（前端只渲染 10 筆，多撈一些保留彈性）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        rows = (await session.execute(
            select(CrmProjectExpense)
            .where(CrmProjectExpense.cost_group_id == group_id)
            .order_by(CrmProjectExpense.created_at.desc())
            .limit(20)
        )).scalars().all()
    return {"expenses": [{
        "id": e.id, "category": e.category, "actual": e.actual,
        "sub_item": e.sub_item or "", "payee": e.payee or "",
        "created_at": _fmt_date(e.created_at),
    } for e in rows]}


@router.post("/public/cost-groups/{group_id}/expenses")
async def add_public_cost_group_expense(group_id: str, req: ProjectExpensePayload):
    """公開端點：登記雜支到指定子表（強制 cost_group_id = URL 參數，防呼叫端注入）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        req.cost_group_id = group_id  # 覆寫 payload 以強制歸屬
        e = await _create_expense(session, g.project_id, req)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}, "project_id": g.project_id}


@router.post("/public/cost-groups/{group_id}/receipts/{expense_id}")
async def upload_public_cost_group_receipt(group_id: str, expense_id: str, file: UploadFile = File(...)):
    """公開端點：上傳收據到指定子表的 expense。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        e = await session.get(CrmProjectExpense, expense_id)
        if not e or e.cost_group_id != group_id:
            raise HTTPException(status_code=404, detail="此雜支不屬於此子表")
    return await _save_receipt(g.project_id, expense_id, file)


@router.get("/public/projects/{project_id}/info")
async def get_public_project_info(project_id: str):
    """公開端點：取得專案名稱 + 子表列表（不需登入，供公開雜支頁選子表）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        groups = (await session.execute(
            select(CrmProjectCostGroup)
            .where(CrmProjectCostGroup.project_id == project_id)
            .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        )).scalars().all()
    return {
        "id": proj.id, "name": proj.name,
        "cost_groups": [{
            "id": g.id, "name": g.name,
            "shoot_date": _fmt_date(g.shoot_date),
        } for g in groups],
    }


@router.get("/public/projects/{project_id}/expenses")
async def list_public_project_expenses(project_id: str):
    """公開端點：列出專案雜支（不需登入）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectExpense).where(CrmProjectExpense.project_id == project_id)
        )).scalars().all()
    return {"expenses": [{
        "id": e.id, "category": e.category, "actual": e.actual,
        "sub_item": e.sub_item or "", "payee": e.payee or "",
        "created_at": _fmt_date(e.created_at),
    } for e in rows]}


@router.post("/projects/{project_id}/expenses")
async def add_project_expense(project_id: str, req: ProjectExpensePayload, request: Request):
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        e = await _create_expense(session, project_id, req)
    return {"status": "ok", "expense_id": e.id, "expense": {"id": e.id}}


@router.patch("/project-expenses/{expense_id}")
async def patch_project_expense(expense_id: str, req: ProjectExpensePatchPayload, request: Request):
    """部分更新雜支欄位（供前端 inline edit 使用）。僅動 payload 有給的欄位。"""
    _check_auth(request)
    _require_db()
    data = req.model_dump(exclude_unset=True)  # 只要有帶就進，None 也算
    if not data:
        return {"status": "ok"}
    factory = await _get_factory()
    _NULLABLE_TEXT = {"sub_item", "payee", "notes", "advance_id", "cost_group_id"}
    async with factory() as session:
        e = await session.get(CrmProjectExpense, expense_id)
        if not e:
            raise HTTPException(status_code=404, detail="找不到此雜支")
        for key, val in data.items():
            if key in _NULLABLE_TEXT and val == "":
                val = None
            setattr(e, key, val)
        await session.commit()
    return {"status": "ok"}


@router.patch("/project-expenses/link-advance")
async def link_expenses_to_advance(request: Request):
    """批次綁定/解除雜支與預支款。body: {expense_ids: [...], advance_id: "..." 或 ""}"""
    _check_auth(request)
    _require_db()
    body = await request.json()
    expense_ids = body.get("expense_ids", [])
    advance_id = body.get("advance_id")
    if not expense_ids:
        raise HTTPException(status_code=400, detail="缺少 expense_ids")
    factory = await _get_factory()
    async with factory() as session:
        for eid in expense_ids:
            e = await session.get(CrmProjectExpense, eid)
            if e:
                e.advance_id = advance_id if advance_id else None
        await session.commit()
    return {"status": "ok", "linked": len(expense_ids)}


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
        e.sub_item = req.sub_item or None
        e.payee = req.payee or None
        e.advance_id = req.advance_id or None
        e.notes = req.notes
        if req.cost_group_id:
            e.cost_group_id = req.cost_group_id
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


# ── Per-Project Receipt Storage ────────────────────────────

@router.post("/projects/{project_id}/receipts/{expense_id}")
async def upload_project_receipt(project_id: str, expense_id: str, request: Request, file: UploadFile = File(...)):
    """上傳收據到專案收據資料夾。"""
    _check_auth(request)
    return await _save_receipt(project_id, expense_id, file)


@router.post("/public/projects/{project_id}/receipts/{expense_id}")
async def upload_project_receipt_public(project_id: str, expense_id: str, file: UploadFile = File(...)):
    """公開端點：上傳收據。"""
    return await _save_receipt(project_id, expense_id, file)


async def _save_receipt(project_id: str, expense_id: str, file: UploadFile):
    import re as _re
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
        exp = await session.get(CrmProjectExpense, expense_id)
        if not exp:
            raise HTTPException(status_code=404, detail="找不到此支出")

        # Determine save path
        base = proj.receipt_path if proj.receipt_path else os.path.join(
            os.getcwd(), "uploads", "receipts", proj.name or project_id)
        os.makedirs(base, exist_ok=True)

        # Build filename: date_category_subitem_payee_id.ext
        date_str = exp.created_at.strftime("%Y%m%d") if exp.created_at else "nodate"
        cat = _re.sub(r'[\\/:*?"<>|]', '', exp.category or "misc")
        sub = _re.sub(r'[\\/:*?"<>|]', '', exp.sub_item or "")
        payee = _re.sub(r'[\\/:*?"<>|]', '', exp.payee or "")
        parts = [date_str, cat]
        if sub:
            parts.append(sub)
        if payee:
            parts.append(payee)
        parts.append(expense_id[:8])
        ext = os.path.splitext(file.filename or ".jpg")[1]
        filename = "_".join(parts) + ext

        filepath = os.path.join(base, filename)
        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)

        # Save receipt_url to expense
        exp.receipt_url = filepath
        await session.commit()

    return {"status": "ok", "path": filepath, "filename": filename}


@router.get("/projects/{project_id}/receipts")
async def list_project_receipts(project_id: str, request: Request):
    """列出專案收據資料夾內的所有檔案。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        proj = await session.get(CrmProject, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="找不到此專案")
    base = proj.receipt_path if proj.receipt_path else os.path.join(
        os.getcwd(), "uploads", "receipts", proj.name or project_id)
    if not os.path.isdir(base):
        return {"receipts": [], "path": base}
    files = []
    for fn in sorted(os.listdir(base)):
        fp = os.path.join(base, fn)
        if os.path.isfile(fp):
            files.append({"filename": fn, "path": fp, "size": os.path.getsize(fp)})
    return {"receipts": files, "path": base}


@router.get("/receipt-file")
async def serve_receipt(path: str = Query(""), request: Request = None):
    """提供收據檔案下載/檢視（限定 uploads/ 或專案 receipt_path）。"""
    if not path or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="檔案不存在")
    abs_path = os.path.abspath(path)
    uploads_dir = os.path.abspath(os.path.join(os.getcwd(), "uploads"))
    if not abs_path.startswith(uploads_dir):
        # 檢查是否在某個專案的 receipt_path 內
        _require_db()
        factory = await _get_factory()
        async with factory() as session:
            rows = (await session.execute(
                select(CrmProject.receipt_path).where(CrmProject.receipt_path.isnot(None))
            )).scalars().all()
        if not any(rp and abs_path.startswith(os.path.abspath(rp)) for rp in rows):
            raise HTTPException(status_code=403, detail="無權存取此路徑")
    from starlette.responses import FileResponse
    return FileResponse(path)


@router.get("/projects/{project_id}/financial-summary")
async def project_financial_summary(project_id: str):
    """專案財務摘要：含稅/未稅/毛利/雜支/外包 預估vs實際。
    多子表後另附：allocated_budget_sum / groups_count / groups_missing_budget_count。"""
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

        costline_row = (await session.execute(
            select(sa_func.coalesce(sa_func.sum(CrmProjectCostLine.estimated_amount), 0),
                   sa_func.coalesce(sa_func.sum(CrmProjectCostLine.actual_amount), 0))
            .where(CrmProjectCostLine.project_id == project_id)
        )).first()
        costline_estimated = costline_row[0] if costline_row else 0
        costline_actual = costline_row[1] if costline_row else 0

        # 跨子表彙總（預算分配）— 單次聚合，不 hydrate ORM 物件
        from sqlalchemy import case as _case
        g_row = (await session.execute(
            select(
                sa_func.coalesce(sa_func.sum(
                    sa_func.coalesce(CrmProjectCostGroup.budget_amount, 0)
                    + sa_func.coalesce(CrmProjectCostGroup.misc_budget_amount, 0)
                ), 0),
                sa_func.count(CrmProjectCostGroup.id),
                sa_func.coalesce(sa_func.sum(_case(
                    (CrmProjectCostGroup.budget_amount.is_(None)
                     & CrmProjectCostGroup.misc_budget_amount.is_(None), 1),
                    else_=0,
                )), 0),
            )
            .where(CrmProjectCostGroup.project_id == project_id)
        )).first()
        allocated_budget_sum = int(g_row[0] or 0) if g_row else 0
        groups_count = int(g_row[1] or 0) if g_row else 0
        groups_missing_budget_count = int(g_row[2] or 0) if g_row else 0

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
        "misc_budget": misc_budget, "misc_budget_pct": project.misc_budget_pct or 5,
        "outsource_budget": outsource_budget,
        "expense_estimated": expense_estimated, "expense_actual": expense_actual,
        "staff_estimated": staff_estimated, "staff_actual": staff_actual,
        "total_cost": total_cost, "actual_profit": actual_profit, "profit_rate": profit_rate,
        "payment_status": project.payment_status or "未到帳",
        "amount_receivable": project.amount_receivable,
        "amount_received": project.amount_received,
        "transfer_fee": project.transfer_fee,
        "costline_estimated": costline_estimated,
        "costline_actual": costline_actual,
        # 多子表擴充
        "allocated_budget_sum": allocated_budget_sum,
        "groups_count": groups_count,
        "groups_missing_budget_count": groups_missing_budget_count,
    }


# ── Phase helpers ────────────────────────────────────────────
PHASE_ORDER = ("前期製作", "現場拍攝", "後期製作")


def _fmt_date(dt) -> Optional[str]:
    """Datetime → 'YYYY-MM-DD'，None 時回 None。"""
    return dt.isoformat()[:10] if dt else None


def _phase_group(lines: list) -> list:
    """[{phase, lines}, ...]，PHASE_ORDER 優先排序，其他 phase 追加末尾。"""
    from collections import defaultdict
    buckets = defaultdict(list)
    for ln in lines:
        buckets[ln["phase"]].append(ln)
    seen = set()
    result = []
    for p in PHASE_ORDER:
        if buckets.get(p):
            result.append({"phase": p, "lines": buckets[p]})
            seen.add(p)
    for p, ls in buckets.items():
        if p not in seen and ls:
            result.append({"phase": p, "lines": ls})
    return result


# ── Cost Line Default Templates ─────────────────────────────
_COST_LINE_DEFAULTS = [
    ("前期製作", "製片/專案管理", 0), ("前期製作", "導演", 1),
    ("前期製作", "腳本", 2), ("前期製作", "視覺設計", 3),
    ("前期製作", "分鏡圖", 4), ("前期製作", "其他", 5),
    ("現場拍攝", "動態攝影", 0), ("現場拍攝", "平面攝影", 1),
    ("現場拍攝", "攝影助理", 2), ("現場拍攝", "燈光師", 3),
    ("現場拍攝", "收音師", 4), ("現場拍攝", "美術", 5),
    ("現場拍攝", "服裝", 6), ("現場拍攝", "梳化", 7),
    ("現場拍攝", "翻譯", 8), ("現場拍攝", "其他", 9),
    ("後期製作", "剪輯", 0), ("後期製作", "調光", 1),
    ("後期製作", "混音", 2), ("後期製作", "視覺包裝", 3),
    ("後期製作", "動態設計", 4), ("後期製作", "錄音", 5),
    ("後期製作", "配音", 6), ("後期製作", "翻譯", 7),
    ("後期製作", "其他", 8),
]

# Default 行政雜支 categories — auto-seeded as $0 rows on each new
# (project, cost-group) pair so users can just fill amounts in instead
# of clicking "+ 新增雜支" first. Kept aligned with EXPENSE_CATEGORIES
# in frontend/tabs/crm/crm-projects-state.js — the inline-edit dropdown
# must offer every seeded category.
_EXPENSE_CATEGORY_DEFAULTS = [
    "交通", "住宿", "飲食", "提案", "器材", "其他",
]


async def _seed_default_expenses(session, project_id: str, cost_group_id: str) -> int:
    """Insert one $0 expense row per default category not yet present
    on (project_id, cost_group_id). Caller commits. Idempotent."""
    rows = (await session.execute(
        select(CrmProjectExpense.category).where(
            CrmProjectExpense.project_id == project_id,
            CrmProjectExpense.cost_group_id == cost_group_id,
        )
    )).scalars().all()
    existing = set(rows)
    now = _now()
    added = 0
    for cat in _EXPENSE_CATEGORY_DEFAULTS:
        if cat in existing:
            continue
        session.add(CrmProjectExpense(
            id=uuid.uuid4().hex,
            project_id=project_id, cost_group_id=cost_group_id,
            category=cat, estimated=0, actual=0,
            created_at=now,
        ))
        added += 1
    return added


# ── Quotation → Cost Line mapping helpers ──────────────────────
_QUOTE_GROUP_TO_PHASE = {
    "前期": "前期��作", "拍攝": "現場拍攝", "現場": "現場拍攝",
    "後製": "後期製作", "後期": "後期製作",
}
_QUOTE_UNIT_MAP = {"天": "日"}


def _map_group_to_phase(group_name: str) -> str:
    g = (group_name or "").strip()
    for keyword, phase in _QUOTE_GROUP_TO_PHASE.items():
        if keyword in g:
            return phase
    return "前期製作"


def _cost_line_to_dict(line, staff_map: dict) -> dict:
    est_staff = staff_map.get(line.estimated_staff_id or "", {})
    act_staff = staff_map.get(line.actual_staff_id or "", {})
    return {
        "id": line.id, "project_id": line.project_id,
        "phase": line.phase, "item_name": line.item_name,
        "sort_order": line.sort_order,
        "estimated_unit_price": line.estimated_unit_price,
        "estimated_quantity": line.estimated_quantity,
        "estimated_unit_type": line.estimated_unit_type or "",
        "estimated_amount": line.estimated_amount,
        "estimated_staff_id": line.estimated_staff_id or "",
        "estimated_staff_name": est_staff.get("name", ""),
        "estimated_notes": line.estimated_notes or "",
        "actual_unit_price": line.actual_unit_price,
        "actual_quantity": line.actual_quantity,
        "actual_unit_type": line.actual_unit_type or "",
        "actual_amount": line.actual_amount,
        "actual_staff_id": line.actual_staff_id or "",
        "actual_staff_name": act_staff.get("name", ""),
        "actual_notes": line.actual_notes or "",
    }


# ── Project Cost Lines (成本估算) Endpoints ──────────────────

@router.get("/projects/{project_id}/cost-lines")
async def list_project_cost_lines(project_id: str, group_id: Optional[str] = Query(None)):
    """回傳成本估算明細，按 phase 分組。
    - 無 group_id：回傳整個專案 + 多一層 grouped_by_group
    - 有 group_id：只回傳該子表
    舊 `grouped` 欄位保留給向後相容（= 當前 group_id 或全專案彙總）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        q = select(CrmProjectCostLine).where(CrmProjectCostLine.project_id == project_id)
        if group_id:
            q = q.where(CrmProjectCostLine.cost_group_id == group_id)
        rows = (await session.execute(
            q.order_by(CrmProjectCostLine.phase, CrmProjectCostLine.sort_order)
        )).scalars().all()

        staff_ids = set()
        for r in rows:
            if r.estimated_staff_id: staff_ids.add(r.estimated_staff_id)
            if r.actual_staff_id:    staff_ids.add(r.actual_staff_id)
        staff_map = {}
        if staff_ids:
            staff_rows = (await session.execute(
                select(CrmStaff).where(CrmStaff.id.in_(list(staff_ids)))
            )).scalars().all()
            staff_map = {s.id: {"name": s.name, "role": s.role} for s in staff_rows}

        # 指定單組 → 不需要列出所有子表（grouped_by_group 留空即可）
        groups = []
        if not group_id:
            groups = (await session.execute(
                select(CrmProjectCostGroup)
                .where(CrmProjectCostGroup.project_id == project_id)
                .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
            )).scalars().all()

    lines = [_cost_line_to_dict(r, staff_map) for r in rows]
    grouped_by_group = [{
        "group_id": g.id, "group_name": g.name,
        "shoot_date": _fmt_date(g.shoot_date),
        "sort_order": g.sort_order,
        "phases": _phase_group([_cost_line_to_dict(r, staff_map) for r in rows if r.cost_group_id == g.id]),
    } for g in groups]

    return {
        "cost_lines": lines,
        "grouped": _phase_group(lines),  # backward-compat (frontend crm-projects-cost.js 仍在讀)
        "grouped_by_group": grouped_by_group,
    }


@router.post("/projects/{project_id}/cost-lines/init")
async def init_project_cost_lines(project_id: str, request: Request):
    """用預設清單初始化成本項目（跳過已存在的）。
    預設目標為主表（第一張子表）；可傳 body `{"cost_group_id": "..."}` 指定。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        target_gid = await _resolve_target_group(session, project_id, body.get("cost_group_id"))
        existing = (await session.execute(
            select(CrmProjectCostLine.phase, CrmProjectCostLine.item_name)
            .where(CrmProjectCostLine.project_id == project_id,
                   CrmProjectCostLine.cost_group_id == target_gid)
        )).all()
        existing_set = {(r[0], r[1]) for r in existing}

        added = 0
        for phase, item_name, sort_order in _COST_LINE_DEFAULTS:
            if (phase, item_name) in existing_set:
                continue
            session.add(CrmProjectCostLine(
                id=uuid.uuid4().hex, project_id=project_id, cost_group_id=target_gid,
                phase=phase, item_name=item_name, sort_order=sort_order,
            ))
            added += 1
        # Also back-fill the 10 default 行政雜支 categories — legacy projects
        # created before this feature won't have them.
        added_exp = await _seed_default_expenses(session, project_id, target_gid)
        await session.commit()
    return {"status": "ok", "added": added, "added_expenses": added_exp,
            "cost_group_id": target_gid}


@router.post("/projects/{project_id}/cost-lines")
async def add_project_cost_line(project_id: str, req: CostLinePayload, request: Request):
    """新增單一自訂成本項目。必須指定 cost_group_id，否則自動歸入主表。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        target_gid = await _resolve_target_group(session, project_id, req.cost_group_id)
        est_amt = req.estimated_amount
        if req.estimated_unit_price and req.estimated_quantity:
            est_amt = req.estimated_unit_price * req.estimated_quantity
        act_amt = req.actual_amount
        if req.actual_unit_price and req.actual_quantity:
            act_amt = req.actual_unit_price * req.actual_quantity
        line = CrmProjectCostLine(
            id=uuid.uuid4().hex, project_id=project_id, cost_group_id=target_gid,
            phase=req.phase, item_name=req.item_name, sort_order=req.sort_order,
            estimated_unit_price=req.estimated_unit_price,
            estimated_quantity=req.estimated_quantity,
            estimated_unit_type=req.estimated_unit_type or None,
            estimated_amount=est_amt,
            estimated_staff_id=req.estimated_staff_id or None,
            estimated_notes=req.estimated_notes,
            actual_unit_price=req.actual_unit_price,
            actual_quantity=req.actual_quantity,
            actual_unit_type=req.actual_unit_type or None,
            actual_amount=act_amt,
            actual_staff_id=req.actual_staff_id or None,
            actual_notes=req.actual_notes,
        )
        session.add(line)
        await session.commit()
    return {"status": "ok", "id": line.id, "cost_group_id": target_gid}


@router.put("/project-cost-lines/{line_id}")
async def update_project_cost_line(line_id: str, req: CostLineUpdatePayload, request: Request):
    """部分更新成本項目。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        line = await session.get(CrmProjectCostLine, line_id)
        if not line:
            raise HTTPException(status_code=404, detail="找不到此成本項目")
        # exclude_unset (not exclude_none): keeps explicit nulls so frontend
        # can clear a numeric field by sending {"estimated_quantity": null}.
        # exclude_none would silently drop the null and the field would never
        # update — user sees "saved" status but the DB never changed.
        update_data = req.model_dump(exclude_unset=True)
        for fld in ("estimated_staff_id", "actual_staff_id"):
            if fld in update_data and update_data[fld] == "":
                update_data[fld] = None
        for key, value in update_data.items():
            setattr(line, key, value)
        # Auto-calculate amount = unit_price × quantity
        if "estimated_unit_price" in update_data or "estimated_quantity" in update_data:
            up = line.estimated_unit_price or 0
            qty = line.estimated_quantity or 0
            line.estimated_amount = up * qty if (up and qty) else None
        if "actual_unit_price" in update_data or "actual_quantity" in update_data:
            up = line.actual_unit_price or 0
            qty = line.actual_quantity or 0
            line.actual_amount = up * qty if (up and qty) else None
        from sqlalchemy import func as _fn
        line.updated_at = _fn.now()
        await session.commit()
    return {"status": "ok"}


@router.delete("/projects/{project_id}/cost-lines/phase")
async def delete_project_cost_phase(project_id: str, request: Request):
    """刪除指定 phase 的所有成本項目。
    支援 body 或 query 的 `group_id` / `cost_group_id` — 有指定時只刪該子表的該 phase。
    未指定：刪該專案全部子表的此 phase（向後相容）。"""
    _check_auth(request)
    _require_db()
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    phase = body.get("phase") or request.query_params.get("phase", "")
    gid = body.get("group_id") or body.get("cost_group_id") or request.query_params.get("group_id")
    if not phase:
        raise HTTPException(status_code=400, detail="需指定 phase")
    factory = await _get_factory()
    async with factory() as session:
        q = delete(CrmProjectCostLine).where(
            CrmProjectCostLine.project_id == project_id,
            CrmProjectCostLine.phase == phase,
        )
        if gid:
            q = q.where(CrmProjectCostLine.cost_group_id == gid)
        await session.execute(q)
        await session.commit()
    return {"status": "ok"}


@router.delete("/project-cost-lines/{line_id}")
async def delete_project_cost_line(line_id: str, request: Request):
    """刪除成本項目。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        line = await session.get(CrmProjectCostLine, line_id)
        if not line:
            raise HTTPException(status_code=404, detail="找不到此成本項目")
        await session.delete(line)
        await session.commit()
    return {"status": "ok"}


# ── Cost Line Templates (成本估算範本) Endpoints ─────────────

@router.get("/cost-line-templates")
async def list_cost_line_templates():
    """列出所有成本估算範本。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmCostLineTemplate).order_by(CrmCostLineTemplate.created_at.desc())
        )).scalars().all()
    return {"templates": [{
        "id": t.id, "name": t.name, "items": t.items or [],
        "item_count": len(t.items or []),
        "created_at": t.created_at.isoformat() if t.created_at else None,
    } for t in rows]}


@router.post("/cost-line-templates")
async def create_cost_line_template(request: Request):
    """從指定專案建立成本估算範本。"""
    _check_auth(request)
    _require_db()
    body = await request.json()
    name = body.get("name", "").strip()
    project_id = body.get("project_id", "")
    if not name:
        raise HTTPException(status_code=400, detail="範本名稱不可為空")
    factory = await _get_factory()
    async with factory() as session:
        # Support creating from defaults or from a project
        if not project_id or project_id == "__defaults__":
            items = [{"phase": p, "item_name": n, "sort_order": s} for p, n, s in _COST_LINE_DEFAULTS]
            tpl = CrmCostLineTemplate(id=uuid.uuid4().hex, name=name, items=items)
            session.add(tpl)
            await session.commit()
            return {"status": "ok", "id": tpl.id, "item_count": len(items)}
        rows = (await session.execute(
            select(CrmProjectCostLine)
            .where(CrmProjectCostLine.project_id == project_id)
            .order_by(CrmProjectCostLine.phase, CrmProjectCostLine.sort_order)
        )).scalars().all()
        items = [{"phase": r.phase, "item_name": r.item_name, "sort_order": r.sort_order} for r in rows]
        tpl = CrmCostLineTemplate(id=uuid.uuid4().hex, name=name, items=items)
        session.add(tpl)
        await session.commit()
    return {"status": "ok", "id": tpl.id, "item_count": len(items)}


@router.post("/projects/{project_id}/cost-lines/apply-template")
async def apply_cost_line_template(project_id: str, request: Request):
    """套用範本到指定子表（覆蓋該子表既有成本項目）。
    body: `{template_id, cost_group_id?}` — cost_group_id 未給則套到主表。
    不動任何雜支（範本只定義成本結構）。"""
    _check_auth(request)
    _require_db()
    body = await request.json()
    template_id = body.get("template_id", "")
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")

        target_gid = await _resolve_target_group(session, project_id, body.get("cost_group_id"))

        if template_id == "__default__":
            items = [{"phase": p, "item_name": n, "sort_order": s} for p, n, s in _COST_LINE_DEFAULTS]
        else:
            tpl = await session.get(CrmCostLineTemplate, template_id)
            if not tpl:
                raise HTTPException(status_code=404, detail="找不到此範本")
            items = tpl.items or []

        new_lines = [
            {"phase": it["phase"], "item_name": it["item_name"], "sort_order": it.get("sort_order", 0)}
            for it in items if it.get("phase") and it.get("item_name")
        ]
        added = await _replace_group_cost_lines(session, project_id, target_gid, new_lines)
        await session.commit()
    return {"status": "ok", "added": added, "cost_group_id": target_gid}


@router.post("/projects/{project_id}/cost-lines/import-from-quotation")
async def import_cost_lines_from_quotation(project_id: str, request: Request):
    """從報價單匯入成本項目到指定子表（覆蓋該子表既有項目，填入預估欄位）。
    body: `{quotation_id, cost_group_id?}` — cost_group_id 未給則匯入到主表。
    不動任何雜支。"""
    _check_auth(request)
    _require_db()
    body = await request.json()
    quotation_id = body.get("quotation_id", "")
    if not quotation_id:
        raise HTTPException(status_code=400, detail="缺少 quotation_id")
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        quotation = await session.get(CrmQuotation, quotation_id)
        if not quotation or quotation.project_id != project_id:
            raise HTTPException(status_code=400, detail="報價不屬於此專案")

        target_gid = await _resolve_target_group(session, project_id, body.get("cost_group_id"))

        items = (await session.execute(
            select(CrmQuotationItem)
            .where(CrmQuotationItem.quotation_id == quotation_id)
            .order_by(CrmQuotationItem.sort_order)
        )).scalars().all()

        new_lines = []
        for it in items:
            desc = (it.description or "").strip()
            if not desc:
                continue
            unit = (it.unit or "式").strip()
            new_lines.append({
                "phase": _map_group_to_phase(it.group_name),
                "item_name": desc,
                "sort_order": it.sort_order or 0,
                "estimated_unit_price": it.unit_price,
                "estimated_quantity": it.quantity,
                "estimated_unit_type": _QUOTE_UNIT_MAP.get(unit, unit),
                "estimated_amount": it.amount,
                "estimated_notes": it.note or None,
            })
        added = await _replace_group_cost_lines(session, project_id, target_gid, new_lines)
        await session.commit()
    return {"status": "ok", "added": added, "cost_group_id": target_gid}


@router.put("/cost-line-templates/{template_id}")
async def update_cost_line_template(template_id: str, request: Request):
    """修改範本名稱。"""
    _check_auth(request)
    _require_db()
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="名稱不可為空")
    factory = await _get_factory()
    async with factory() as session:
        tpl = await session.get(CrmCostLineTemplate, template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail="找不到此範本")
        tpl.name = name
        await session.commit()
    return {"status": "ok"}


@router.delete("/cost-line-templates/{template_id}")
async def delete_cost_line_template(template_id: str, request: Request):
    """刪除成本估算範本。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        tpl = await session.get(CrmCostLineTemplate, template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail="找不到此範本")
        await session.delete(tpl)
        await session.commit()
    return {"status": "ok"}


# ── Cost Groups (成本子表) Endpoints ─────────────────────────

async def _get_first_group_id(session, project_id: str) -> Optional[str]:
    """專案的首張子表 id（依 sort_order + created_at）。純查詢，無副作用。"""
    return (await session.execute(
        select(CrmProjectCostGroup.id)
        .where(CrmProjectCostGroup.project_id == project_id)
        .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        .limit(1)
    )).scalar_one_or_none()


async def _resolve_target_group(session, project_id: str, supplied: Optional[str]) -> str:
    """回傳目標子表 id：供給者優先，否則取首張（主表）。
    若專案完全沒有子表（migration 漏網 / create_project 失敗），
    自我修復建立主表（flush，不 commit，交由外層 commit）。"""
    if supplied:
        return supplied
    gid = await _get_first_group_id(session, project_id)
    if gid:
        return gid
    gid = uuid.uuid4().hex
    session.add(CrmProjectCostGroup(id=gid, project_id=project_id, name="主表", sort_order=0))
    await session.flush()
    return gid


async def _replace_group_cost_lines(session, project_id: str, group_id: str, new_lines: list) -> int:
    """清空目標子表 cost_lines 並插入 new_lines；回傳新增筆數。
    new_lines 為 dict list，dict 內容是 CrmProjectCostLine 的 kwargs
    （不含 id/project_id/cost_group_id）。呼叫端自行 commit。"""
    await session.execute(
        delete(CrmProjectCostLine).where(
            CrmProjectCostLine.project_id == project_id,
            CrmProjectCostLine.cost_group_id == group_id,
        )
    )
    for kw in new_lines:
        session.add(CrmProjectCostLine(
            id=uuid.uuid4().hex, project_id=project_id, cost_group_id=group_id, **kw
        ))
    return len(new_lines)


async def _compute_group_summary(session, group_id: str) -> dict:
    """彙總單一子表：cost_estimated / cost_actual / expense_estimated / expense_actual。"""
    from sqlalchemy import func as _fn
    cl_row = (await session.execute(
        select(_fn.coalesce(_fn.sum(CrmProjectCostLine.estimated_amount), 0),
               _fn.coalesce(_fn.sum(CrmProjectCostLine.actual_amount), 0),
               _fn.count(CrmProjectCostLine.id))
        .where(CrmProjectCostLine.cost_group_id == group_id)
    )).first()
    ex_row = (await session.execute(
        select(_fn.coalesce(_fn.sum(CrmProjectExpense.estimated), 0),
               _fn.coalesce(_fn.sum(CrmProjectExpense.actual), 0),
               _fn.count(CrmProjectExpense.id))
        .where(CrmProjectExpense.cost_group_id == group_id)
    )).first()
    cost_est = int(cl_row[0] or 0) if cl_row else 0
    cost_act = int(cl_row[1] or 0) if cl_row else 0
    cl_count = int(cl_row[2] or 0) if cl_row else 0
    exp_est = int(ex_row[0] or 0) if ex_row else 0
    exp_act = int(ex_row[1] or 0) if ex_row else 0
    exp_count = int(ex_row[2] or 0) if ex_row else 0
    return {
        "cost_estimated": cost_est, "cost_actual": cost_act,
        "expense_estimated": exp_est, "expense_actual": exp_act,
        "total_estimated": cost_est + exp_est,
        "total_actual": cost_act + exp_act,
        "cost_lines_count": cl_count,
        "expenses_count": exp_count,
    }


def _cost_group_to_dict(g, summary: Optional[dict] = None) -> dict:
    total_budget = (g.budget_amount or 0) + (g.misc_budget_amount or 0)
    d = {
        "id": g.id, "project_id": g.project_id, "name": g.name,
        "shoot_date": _fmt_date(g.shoot_date),
        "notes": g.notes or "",
        "sort_order": g.sort_order,
        "budget_amount": g.budget_amount,
        "misc_budget_amount": g.misc_budget_amount,
        "profit_target_pct": g.profit_target_pct,
        "total_budget": total_budget if (g.budget_amount is not None or g.misc_budget_amount is not None) else None,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "updated_at": g.updated_at.isoformat() if g.updated_at else None,
    }
    if summary is not None:
        d["summary"] = summary
        # usage_pct: budget 未設時回 None；設了才算
        if total_budget > 0:
            d["usage_pct"] = round(summary["total_actual"] / total_budget * 100)
        else:
            d["usage_pct"] = None
    return d


@router.get("/projects/{project_id}/cost-groups")
async def list_project_cost_groups(project_id: str):
    """列出指定專案的所有子表 + 每組 summary。純讀取：依賴
    migration + create_project 保證主表存在，避免 GET 寫入。"""
    _require_db()
    factory = await _get_factory()
    from sqlalchemy import func as _fn
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectCostGroup)
            .where(CrmProjectCostGroup.project_id == project_id)
            .order_by(CrmProjectCostGroup.sort_order, CrmProjectCostGroup.created_at)
        )).scalars().all()
        if not rows:
            return {"cost_groups": []}

        # 單次 GROUP BY 聚合，避免 O(groups) 次子查詢
        cl_rows = (await session.execute(
            select(CrmProjectCostLine.cost_group_id,
                   _fn.coalesce(_fn.sum(CrmProjectCostLine.estimated_amount), 0),
                   _fn.coalesce(_fn.sum(CrmProjectCostLine.actual_amount), 0),
                   _fn.count(CrmProjectCostLine.id))
            .where(CrmProjectCostLine.project_id == project_id)
            .group_by(CrmProjectCostLine.cost_group_id)
        )).all()
        cl_map = {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in cl_rows}

        ex_rows = (await session.execute(
            select(CrmProjectExpense.cost_group_id,
                   _fn.coalesce(_fn.sum(CrmProjectExpense.estimated), 0),
                   _fn.coalesce(_fn.sum(CrmProjectExpense.actual), 0),
                   _fn.count(CrmProjectExpense.id))
            .where(CrmProjectExpense.project_id == project_id)
            .group_by(CrmProjectExpense.cost_group_id)
        )).all()
        ex_map = {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in ex_rows}

    result = []
    for g in rows:
        ce, ca, cc = cl_map.get(g.id, (0, 0, 0))
        ee, ea, ec = ex_map.get(g.id, (0, 0, 0))
        summary = {
            "cost_estimated": ce, "cost_actual": ca,
            "expense_estimated": ee, "expense_actual": ea,
            "total_estimated": ce + ee, "total_actual": ca + ea,
            "cost_lines_count": cc, "expenses_count": ec,
        }
        result.append(_cost_group_to_dict(g, summary))
    return {"cost_groups": result}


@router.post("/projects/{project_id}/cost-groups")
async def create_cost_group(project_id: str, req: CostGroupCreate, request: Request):
    """新增子表。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        shoot_date = _parse_shoot_date(req.shoot_date) if req.shoot_date else None
        g = CrmProjectCostGroup(
            id=uuid.uuid4().hex, project_id=project_id,
            name=req.name.strip(), shoot_date=shoot_date, notes=req.notes or None,
            sort_order=req.sort_order,
            budget_amount=req.budget_amount, misc_budget_amount=req.misc_budget_amount,
            profit_target_pct=req.profit_target_pct,
        )
        session.add(g)
        await _seed_default_expenses(session, project_id, g.id)
        await session.commit()
        await session.refresh(g)
        summary = await _compute_group_summary(session, g.id)
    return {"status": "ok", "cost_group": _cost_group_to_dict(g, summary)}


@router.put("/cost-groups/{group_id}")
async def update_cost_group(group_id: str, req: CostGroupUpdate, request: Request):
    """更新子表（名稱/拍攝日/備註/排序/預算/毛利率），PATCH 風格只動非 None 欄位。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        data = req.model_dump(exclude_none=True)
        if "name" in data:
            g.name = data["name"].strip() or g.name
        if "shoot_date" in data:
            g.shoot_date = _parse_shoot_date(data["shoot_date"]) if data["shoot_date"] else None
        if "notes" in data:
            g.notes = data["notes"] or None
        for fld in ("sort_order", "budget_amount", "misc_budget_amount", "profit_target_pct"):
            if fld in data:
                setattr(g, fld, data[fld])
        from sqlalchemy import func as _fn
        g.updated_at = _fn.now()
        await session.commit()
        await session.refresh(g)
        summary = await _compute_group_summary(session, g.id)
    return {"status": "ok", "cost_group": _cost_group_to_dict(g, summary)}


@router.delete("/cost-groups/{group_id}")
async def delete_cost_group(group_id: str, request: Request):
    """刪除子表（cascade cost_lines + expenses）。若只剩 1 張子表則禁止。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        from sqlalchemy import func as _fn
        count = (await session.execute(
            select(_fn.count(CrmProjectCostGroup.id))
            .where(CrmProjectCostGroup.project_id == g.project_id)
        )).scalar_one()
        if count <= 1:
            raise HTTPException(status_code=400, detail="至少需保留一張子表")
        # cascade cost_lines + expenses（receipt 實體檔目前保留，不做磁碟清理）
        await session.execute(delete(CrmProjectCostLine).where(CrmProjectCostLine.cost_group_id == group_id))
        await session.execute(delete(CrmProjectExpense).where(CrmProjectExpense.cost_group_id == group_id))
        await session.delete(g)
        await session.commit()
    return {"status": "ok"}


@router.post("/cost-groups/{group_id}/duplicate")
async def duplicate_cost_group(group_id: str, req: CostGroupDuplicate, request: Request):
    """複製整張子表（含 cost_lines；結算值清空），雜支不複製。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        src = await session.get(CrmProjectCostGroup, group_id)
        if not src:
            raise HTTPException(status_code=404, detail="找不到此子表")
        shoot_date = _parse_shoot_date(req.shoot_date) if req.shoot_date else None
        # 新 group 的 sort_order 放最後
        from sqlalchemy import func as _fn
        max_sort = (await session.execute(
            select(_fn.coalesce(_fn.max(CrmProjectCostGroup.sort_order), 0))
            .where(CrmProjectCostGroup.project_id == src.project_id)
        )).scalar_one()
        new_g = CrmProjectCostGroup(
            id=uuid.uuid4().hex, project_id=src.project_id,
            name=req.name.strip(), shoot_date=shoot_date,
            notes=src.notes, sort_order=(max_sort or 0) + 1,
            budget_amount=src.budget_amount, misc_budget_amount=src.misc_budget_amount,
            profit_target_pct=src.profit_target_pct,
        )
        session.add(new_g)
        # 複製 cost_lines（結算欄位清空）
        src_lines = (await session.execute(
            select(CrmProjectCostLine)
            .where(CrmProjectCostLine.cost_group_id == group_id)
            .order_by(CrmProjectCostLine.phase, CrmProjectCostLine.sort_order)
        )).scalars().all()
        for l in src_lines:
            session.add(CrmProjectCostLine(
                id=uuid.uuid4().hex, project_id=src.project_id, cost_group_id=new_g.id,
                phase=l.phase, item_name=l.item_name, sort_order=l.sort_order,
                estimated_unit_price=l.estimated_unit_price,
                estimated_quantity=l.estimated_quantity,
                estimated_unit_type=l.estimated_unit_type,
                estimated_amount=l.estimated_amount,
                estimated_staff_id=l.estimated_staff_id,
                estimated_notes=l.estimated_notes,
                # 結算欄位不複製
            ))
        await _seed_default_expenses(session, src.project_id, new_g.id)
        await session.commit()
        await session.refresh(new_g)
        summary = await _compute_group_summary(session, new_g.id)
    return {"status": "ok", "cost_group": _cost_group_to_dict(new_g, summary), "lines_copied": len(src_lines)}


@router.get("/cost-groups/{group_id}/summary")
async def get_cost_group_summary(group_id: str):
    """單一子表的儀表板資料。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        g = await session.get(CrmProjectCostGroup, group_id)
        if not g:
            raise HTTPException(status_code=404, detail="找不到此子表")
        summary = await _compute_group_summary(session, group_id)
    return {"cost_group": _cost_group_to_dict(g, summary)}


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
        "recipient": getattr(inv, 'recipient', '') or "",
        "recipient_phone": getattr(inv, 'recipient_phone', '') or "",
        "recipient_address": getattr(inv, 'recipient_address', '') or "",
        "notes": inv.notes or "",
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
    }


# ── Invoice Endpoints ───────────────────────────────────────

@router.get("/invoices")
async def list_invoices(
    q: str = Query(""), payment_type: str = Query(""),
    category: str = Query(""), project_id: str = Query(""),
    issue_status: str = Query(""),
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
        if issue_status:
            query = query.where(CrmInvoice.issue_status == issue_status)
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
        "category": p.category or "",
        "payee_name": p.payee_name or "", "payee_id": p.payee_id or "",
        "payee_type": p.payee_type or "",
        "needs_invoice": p.needs_invoice, "invoice_number": p.invoice_number or "",
        "invoice_amount": p.invoice_amount,
        "project_id": p.project_id or "", "project_name": project_name,
        "project_label": p.project_label or "",
        "payment_date": p.payment_date.isoformat() if p.payment_date else None,
        "payment_status": p.payment_status or "未付款",
        "planned_month": p.planned_month or "",
        "advance_by": p.advance_by or "",
        "is_advance": p.is_advance or 0,
        "advance_returned": p.advance_returned or 0,
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
            if payment_status == "應付款":
                query = query.where(CrmPaymentRequest.payment_status.in_(["應付款", "未付款"]))
            else:
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


@router.get("/payments/advances")
async def list_advance_payments(returned: int = -1, project_id: str = Query("")):
    """列出預支款。returned=-1=全部，0=未結清，1=已結清。project_id 可過濾特定專案。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        from sqlalchemy import func as sa_func
        q = select(CrmPaymentRequest).where(CrmPaymentRequest.is_advance == 1)
        if project_id:
            q = q.where(CrmPaymentRequest.project_id == project_id)
        rows = (await session.execute(q.order_by(CrmPaymentRequest.created_at.desc()))).scalars().all()

        result = []
        for p in rows:
            # Calculate expenses by this payee in this project
            expense_total = 0
            exp_sum = (await session.execute(
                select(sa_func.coalesce(sa_func.sum(CrmProjectExpense.actual), 0))
                .where(CrmProjectExpense.advance_id == p.id)
            )).scalar() or 0
            expense_total = exp_sum

            # Get project name
            project_name = p.project_label or ""
            if p.project_id and not project_name:
                proj = await session.get(CrmProject, p.project_id)
                if proj:
                    project_name = proj.name

            amt = p.amount or 0
            # 發款: 收支明細中關聯此預支的支出合計 = 預支金額 → 已發款
            cash_pay_total = (await session.execute(
                select(sa_func.coalesce(sa_func.sum(CrmCashEntry.expense), 0))
                .where(CrmCashEntry.advance_payment_id == p.id)
                .where(CrmCashEntry.expense > 0)
            )).scalar() or 0
            is_paid = (cash_pay_total >= amt > 0)
            # 收款: 收支明細中關聯此預支的收入 > 0 → 已收款
            cash_return_total = (await session.execute(
                select(sa_func.coalesce(sa_func.sum(CrmCashEntry.deposit), 0))
                .where(CrmCashEntry.advance_payment_id == p.id)
                .where(CrmCashEntry.deposit > 0)
            )).scalar() or 0
            is_returned = cash_return_total > 0
            # 餘額 = 預支金額 − 支出 − 已收回
            balance = amt - expense_total - cash_return_total
            # 關聯的收支明細（發款/收款記錄）
            linked_cash = (await session.execute(
                select(CrmCashEntry)
                .where(CrmCashEntry.advance_payment_id == p.id)
                .order_by(CrmCashEntry.entry_date)
            )).scalars().all()
            cash_entries = [{
                "id": c.id,
                "entry_date": c.entry_date.isoformat()[:10] if c.entry_date else None,
                "summary": c.summary or "",
                "deposit": c.deposit or 0,
                "expense": c.expense or 0,
                "type": "收款" if (c.deposit or 0) > 0 else "發款",
            } for c in linked_cash]
            # 完成: 已發款 + 已收款 + 餘額為零
            is_settled = is_paid and is_returned and balance == 0
            result.append({
                "id": p.id,
                "payee_name": p.payee_name or "",
                "amount": amt,
                "project_id": p.project_id or "",
                "project_name": project_name,
                "payment_date": p.payment_date.isoformat() if p.payment_date else None,
                "request_date": p.request_date.isoformat() if p.request_date else None,
                "payment_status": p.payment_status or "應付款",
                "is_paid": is_paid,
                "is_returned": is_returned,
                "is_settled": is_settled,
                "expense_total": expense_total,
                "balance": balance,
                "cash_entries": cash_entries,
            })
    # Post-filter by settled status
    if returned == 0:
        result = [r for r in result if not r["is_settled"]]
    elif returned == 1:
        result = [r for r in result if r["is_settled"]]
    return {"advances": result}


@router.patch("/payments/batch-month")
async def batch_update_month(request: Request):
    """更新請款單的 planned_month。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("payment_ids", [])
    planned_month = body.get("planned_month", "")
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 payment_ids")

    async with factory() as session:
        updated = 0
        for pid in ids:
            p = await session.get(CrmPaymentRequest, pid)
            if p:
                p.planned_month = planned_month
                p.updated_at = _now()
                updated += 1
        await session.commit()
    return {"status": "ok", "updated": updated}


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
            if not p:
                continue
            if p.payment_status != "已付款":
                p.payment_status = "已付款"
                p.payment_date = pay_date
                p.updated_at = _now()
                updated += 1
            elif p.payment_date != pay_date:
                p.payment_date = pay_date
                p.updated_at = _now()
                updated += 1
        await session.commit()
    return {"status": "ok", "updated": updated}


@router.patch("/payments/batch-unpay")
async def batch_unpay(request: Request):
    """將已付款的請款單改回應付款。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("payment_ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 payment_ids")

    async with factory() as session:
        updated = 0
        for pid in ids:
            p = await session.get(CrmPaymentRequest, pid)
            if p and p.payment_status == "已付款":
                p.payment_status = "應付款"
                p.payment_date = None
                p.updated_at = _now()
                updated += 1
        await session.commit()
    return {"status": "ok", "updated": updated}


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

def _to_cash_dict(e, project_name: str = "", invoice_title: str = "") -> dict:
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
        "invoice_id": e.invoice_id or "",
        "bank_fee": e.bank_fee,
        "advance_payment_id": e.advance_payment_id or "",
        "invoice_title": invoice_title,
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
            select(CrmCashEntry, CrmProject.name.label("pn"), CrmInvoice.title.label("inv_title"))
            .outerjoin(CrmProject, CrmProject.id == CrmCashEntry.project_id)
            .outerjoin(CrmInvoice, CrmInvoice.id == CrmCashEntry.invoice_id)
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
    return {"entries": [_to_cash_dict(r[0], r[1] or "", r[2] or "") for r in rows], "total": len(rows)}


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
        if req.invoice_id and req.deposit:
            inv = await session.get(CrmInvoice, req.invoice_id)
            if inv:
                inv.payment_status = "已收款"
        await session.commit()
    return {"status": "ok", "entry_id": e.id, "entry": {"id": e.id}}


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
        old_invoice_id = e.invoice_id
        for k, v in req.model_dump(exclude=date_fields).items():
            setattr(e, k, v)
        for k, v in dates.items():
            setattr(e, k, v)
        e.updated_at = _now()
        if req.invoice_id and req.deposit:
            inv = await session.get(CrmInvoice, req.invoice_id)
            if inv:
                inv.payment_status = "已收款"
        if old_invoice_id and old_invoice_id != req.invoice_id:
            old_inv = await session.get(CrmInvoice, old_invoice_id)
            if old_inv and old_inv.payment_status == "已收款":
                old_inv.payment_status = "未收款"
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
        if e.invoice_id and e.deposit:
            inv = await session.get(CrmInvoice, e.invoice_id)
            if inv and inv.payment_status == "已收款":
                inv.payment_status = "未收款"
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
    """請款彙總，按收款人分組。month=all 或空=全部應付款；month=YYYY-MM=該月。"""
    _require_db()
    factory = await _get_factory()

    filter_all = (not month) or month == "all"

    async with factory() as session:
        query = (
            select(CrmPaymentRequest, CrmStaff.id_number, CrmStaff.bank_name, CrmStaff.bank_account)
            .outerjoin(CrmStaff, CrmStaff.name == CrmPaymentRequest.payee_name)
            .where(or_(CrmPaymentRequest.is_advance == 0, CrmPaymentRequest.is_advance.is_(None)))
            .order_by(CrmPaymentRequest.request_date)
        )

        if not filter_all:
            parts = month.split("-")
            try:
                year, mon = int(parts[0]), int(parts[1])
            except (IndexError, ValueError):
                raise HTTPException(status_code=400, detail="month 格式無效，請使用 YYYY-MM")
            from calendar import monthrange
            try:
                start = datetime(year, mon, 1, tzinfo=timezone.utc)
            except ValueError:
                raise HTTPException(status_code=400, detail="month 格式無效，請使用 YYYY-MM")
            _, last_day = monthrange(year, mon)
            end = datetime(year, mon, last_day, 23, 59, 59, tzinfo=timezone.utc)
            query = query.where(or_(
                CrmPaymentRequest.planned_month == month,
                CrmPaymentRequest.request_date.between(start, end),
            ))

        if status == "已付款":
            query = query.where(CrmPaymentRequest.payment_status == "已付款")
        elif status == "all":
            pass  # 全部狀態，不加篩選
        elif not status or status == "應付款":
            # 預設：應付款（含舊資料的「未付款」）
            query = query.where(CrmPaymentRequest.payment_status.in_(["應付款", "未付款"]))

        rows = (await session.execute(query)).all()

    payee_groups: dict = {}
    seen_ids: set = set()
    for p, staff_id_number, staff_bank_name, staff_bank_account in rows:
        if p.id in seen_ids:
            continue
        seen_ids.add(p.id)
        name = p.payee_name or "未指定"
        if name not in payee_groups:
            payee_groups[name] = {
                "payee_name": name,
                "payee_id": p.payee_id or staff_id_number or "",
                "bank_name": staff_bank_name or "",
                "bank_account": staff_bank_account or "",
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
            "payment_date": p.payment_date.strftime("%Y-%m-%d") if p.payment_date else "",
            "planned_month": p.planned_month or "",
        })

    payees = sorted(payee_groups.values(), key=lambda x: x["total_amount"], reverse=True)
    return {"month": month or "all", "payees": payees, "grand_total": sum(pg["total_amount"] for pg in payees)}


@router.get("/receivables/summary")
async def receivables_summary(status: str = Query("")):
    """應收帳款彙總：已開立發票按客戶（company_name）分組。status=未收款/已收款/空=全部。"""
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        query = (
            select(CrmInvoice, CrmProject.name.label("pn"),
                   Client.tax_id.label("c_tax_id"), Client.payment_info, Client.payment_note)
            .outerjoin(CrmProject, CrmProject.id == CrmInvoice.project_id)
            .outerjoin(Client, Client.short_name == CrmInvoice.company_name)
            .where(CrmInvoice.issue_status == "已開立")
            .order_by(CrmInvoice.invoice_date.desc())
        )
        if status:
            query = query.where(CrmInvoice.payment_status == status)
        else:
            query = query.where(CrmInvoice.payment_status.notin_(["已收款", "作廢"]))
        rows = (await session.execute(query)).all()

    client_groups: dict = {}
    seen_ids: set = set()
    for inv, proj_name, c_tax_id, c_payment_info, c_payment_note in rows:
        if inv.id in seen_ids:
            continue
        seen_ids.add(inv.id)
        name = inv.company_name or "未指定"
        if name not in client_groups:
            client_groups[name] = {
                "company_name": name,
                "tax_id": inv.tax_id or c_tax_id or "",
                "payment_info": c_payment_info or "",
                "payment_note": c_payment_note or "",
                "total_amount": 0, "items": [],
            }
        client_groups[name]["total_amount"] += inv.amount_total or 0
        days = 0
        if inv.invoice_date:
            days = (_now() - inv.invoice_date).days
        client_groups[name]["items"].append({
            "id": inv.id,
            "title": inv.title or "",
            "invoice_number": inv.invoice_number or "",
            "invoice_date": inv.invoice_date.strftime("%Y/%m/%d") if inv.invoice_date else "",
            "amount_total": inv.amount_total or 0,
            "payment_status": inv.payment_status or "",
            "project_name": proj_name or "",
            "days_since_issued": days,
            "category": inv.category or "",
        })

    clients = sorted(client_groups.values(), key=lambda x: x["total_amount"], reverse=True)
    return {"clients": clients, "grand_total": sum(c["total_amount"] for c in clients)}


@router.patch("/invoices/batch-receive")
async def batch_receive(request: Request):
    """批次標記發票為已收款。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    ids = body.get("invoice_ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="請提供 invoice_ids")

    async with factory() as session:
        updated = 0
        for iid in ids:
            inv = await session.get(CrmInvoice, iid)
            if inv and inv.payment_status != "已收款":
                inv.payment_status = "已收款"
                inv.updated_at = _now()
                updated += 1
        await session.commit()
    return {"status": "ok", "updated": updated}


# ── Project Showcase / Delivery Endpoints ──────────────────

def _showcase_dir(project_id: str) -> str:
    d = os.path.join(_UPLOAD_BASE, "projects", project_id, "showcase")
    os.makedirs(d, exist_ok=True)
    return d


def _to_showcase_dict(s) -> dict:
    return {
        "id": s.id,
        "cover_url": s.cover_url or "",
        "description": s.description or "",
        "video_url": s.video_url or "",
        "gallery": s.gallery or [],
        "process_mode": s.process_mode or "gallery",
        "process_items": s.process_items or [],
        "credits": s.credits or [],
        "tags": s.tags or [],
        "slug": s.slug or "",
        "published": bool(s.published),
        "published_at": s.published_at.isoformat() if s.published_at else None,
        "edit_token": s.edit_token or "",
        "editable": bool(s.editable) if s.editable is not None else True,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    }


async def _verify_showcase_edit_token(session, token: str, require_editable: bool = False):
    return await _verify_token_generic(session, token, 'showcase_edit', CrmProjectShowcase, 'editable', require_editable)


# Phase M-W Showcase 雙向支援：
#   - Sync hook：Showcase 儲存時鏡像 5 欄到 crm_projects.public_*
#     (Astro 官網讀 public_*，編輯在 Showcase 端；hook 讓資料一致)
#   - Showcase edit token：CRM 完稿 Tab 與官網管理 Tab iframe 共用同一把鑰匙

SHOWCASE_EDIT_SCOPE = "showcase_edit"
SHOWCASE_EDIT_EXPIRES_DAYS = 36500  # ~100 年，實務上永久


async def _sync_showcase_to_public(session, sc) -> None:
    """把 Showcase 可鏡像欄位同步到 crm_projects.public_*（呼叫者統一 commit）。

    同步範圍（5 欄）：
      sc.description  → public_description
      sc.slug         → public_slug
      sc.video_url    → public_youtube_id（parse YT ID）
      sc.published    → public + public_published_at

    不同步 title/client/year — 這些讓 Astro read-time 從 project.name / client
    動態 join，避免雙寫需要刷新的複雜度。
    """
    project = await session.get(CrmProject, sc.id)
    if not project:
        return
    project.public_description = sc.description or None
    project.public_slug = sc.slug or None
    # Lazy import — services/website/ is NAS-only and not in OTA AGENT_DIRS,
    # so a top-level import would crash agent boot and 404 every CRM endpoint.
    # Showcase publish only fires on master where the file is present.
    from services.website.notion_service import _extract_youtube_id
    yt_id = _extract_youtube_id(sc.video_url or "")
    if yt_id:
        project.public_youtube_id = yt_id
    project.public = bool(sc.published)
    project.public_published_at = sc.published_at if sc.published else None


async def _mint_showcase_edit_token(
    session, project_id: str, *, reuse_existing: bool = False,
) -> tuple[str, CrmProjectShowcase]:
    """取得/產生 Showcase edit token，upsert CrmProjectShowcase row。

    Args:
        reuse_existing: True 時如果 sc.edit_token 已存在就重用（冪等），不動 updated_at；
                        False 時永遠產新 token 覆寫（CRM 完稿 Tab「重新產生分享連結」的語義）。

    回傳 (token, showcase_row)。不 commit，caller 統一 commit。
    新建 showcase 時才設 editable=True；既有 row 保留 CRM 管理員手動設的 editable 值。
    """
    from core.auth import create_token
    sc = await session.get(CrmProjectShowcase, project_id)
    if sc and reuse_existing and sc.edit_token:
        return sc.edit_token, sc
    token = create_token({"sub": project_id, "scope": SHOWCASE_EDIT_SCOPE},
                         expires_days=SHOWCASE_EDIT_EXPIRES_DAYS)
    if not sc:
        sc = CrmProjectShowcase(id=project_id, edit_token=token, editable=True)
        session.add(sc)
    else:
        sc.edit_token = token
        sc.updated_at = _now()
    return token, sc


@router.get("/projects/{project_id}/showcase")
async def get_project_showcase(project_id: str, request: Request):
    """取得或建立專案 Showcase（1:1 關聯）。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            sc = CrmProjectShowcase(id=project_id)
            session.add(sc)
            await session.commit()
            await session.refresh(sc)
        return _to_showcase_dict(sc)


@router.put("/projects/{project_id}/showcase")
async def update_project_showcase(project_id: str, req: ShowcasePayload, request: Request):
    """更新專案 Showcase 欄位。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            sc = CrmProjectShowcase(id=project_id)
            session.add(sc)
        sc.description = req.description
        sc.video_url = req.video_url
        sc.credits = req.credits
        sc.tags = req.tags
        sc.process_mode = req.process_mode
        sc.slug = req.slug or None
        sc.updated_at = _now()
        await _sync_showcase_to_public(session, sc)
        await session.commit()
        await session.refresh(sc)
    return _to_showcase_dict(sc)


@router.post("/projects/{project_id}/showcase/cover")
async def upload_showcase_cover(project_id: str, request: Request, file: UploadFile = File(...)):
    """上傳 Showcase 封面圖。"""
    _check_auth(request)
    _require_db()
    ext = (os.path.splitext(file.filename or "cover.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    sdir = _showcase_dir(project_id)
    # Remove old covers
    for f in os.listdir(sdir):
        if f.startswith("cover."):
            os.remove(os.path.join(sdir, f))
    dest = os.path.join(sdir, f"cover{ext}")
    content = await file.read()
    with open(dest, "wb") as fp:
        fp.write(content)
    url = f"/uploads/projects/{project_id}/showcase/cover{ext}"
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            sc = CrmProjectShowcase(id=project_id, cover_url=url)
            session.add(sc)
        else:
            sc.cover_url = url
            sc.updated_at = _now()
        await session.commit()
    return {"status": "ok", "cover_url": url}


@router.post("/projects/{project_id}/showcase/gallery")
async def upload_showcase_gallery(project_id: str, request: Request,
                                  file: UploadFile = File(...),
                                  caption: str = Query("")):
    """上傳 Showcase 圖庫圖片。"""
    _check_auth(request)
    _require_db()
    ext = (os.path.splitext(file.filename or "img.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    sdir = _showcase_dir(project_id)
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(sdir, fname)
    content = await file.read()
    with open(dest, "wb") as fp:
        fp.write(content)
    url = f"/uploads/projects/{project_id}/showcase/{fname}"
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            sc = CrmProjectShowcase(id=project_id, gallery=[{"url": url, "caption": caption}])
            session.add(sc)
        else:
            gallery = list(sc.gallery or [])
            gallery.append({"url": url, "caption": caption})
            sc.gallery = gallery
            sc.updated_at = _now()
        await session.commit()
        await session.refresh(sc)
    return {"status": "ok", "gallery": sc.gallery or []}


@router.delete("/projects/{project_id}/showcase/gallery/{index}")
async def delete_showcase_gallery(project_id: str, index: int, request: Request):
    """刪除 Showcase 圖庫中指定索引的項目。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            raise HTTPException(status_code=404, detail="尚未建立 Showcase")
        gallery = list(sc.gallery or [])
        if index < 0 or index >= len(gallery):
            raise HTTPException(status_code=400, detail="索引超出範圍")
        removed = gallery.pop(index)
        # Try to delete the file
        file_url = removed.get("url", "")
        if file_url.startswith("/uploads/"):
            fpath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 file_url.lstrip("/").replace("/", os.sep))
            if os.path.isfile(fpath):
                os.remove(fpath)
        sc.gallery = gallery
        sc.updated_at = _now()
        await session.commit()
    return {"status": "ok", "gallery": gallery}


@router.post("/projects/{project_id}/showcase/process")
async def upload_showcase_process(project_id: str, request: Request,
                                  file: UploadFile = File(...),
                                  caption: str = Query(""),
                                  phase: str = Query(""),
                                  item_type: str = Query("image")):
    """上傳 Showcase 製程項目圖片。"""
    _check_auth(request)
    _require_db()
    ext = (os.path.splitext(file.filename or "img.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    sdir = _showcase_dir(project_id)
    fname = f"process_{uuid.uuid4().hex}{ext}"
    dest = os.path.join(sdir, fname)
    content = await file.read()
    with open(dest, "wb") as fp:
        fp.write(content)
    url = f"/uploads/projects/{project_id}/showcase/{fname}"
    item = {"type": item_type, "url": url, "caption": caption, "phase": phase, "video_url": ""}
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            sc = CrmProjectShowcase(id=project_id, process_items=[item])
            session.add(sc)
        else:
            items = list(sc.process_items or [])
            items.append(item)
            sc.process_items = items
            sc.updated_at = _now()
        await session.commit()
        await session.refresh(sc)
    return {"status": "ok", "process_items": sc.process_items or []}


@router.delete("/projects/{project_id}/showcase/process/{index}")
async def delete_showcase_process(project_id: str, index: int, request: Request):
    """刪除 Showcase 製程項目中指定索引的項目。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            raise HTTPException(status_code=404, detail="尚未建立 Showcase")
        items = list(sc.process_items or [])
        if index < 0 or index >= len(items):
            raise HTTPException(status_code=400, detail="索引超出範圍")
        removed = items.pop(index)
        file_url = removed.get("url", "")
        if file_url.startswith("/uploads/"):
            fpath = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 file_url.lstrip("/").replace("/", os.sep))
            if os.path.isfile(fpath):
                os.remove(fpath)
        sc.process_items = items
        sc.updated_at = _now()
        await session.commit()
    return {"status": "ok", "process_items": items}


@router.post("/projects/{project_id}/showcase/publish")
async def toggle_showcase_publish(project_id: str, request: Request):
    """切換 Showcase 發布狀態。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc:
            raise HTTPException(status_code=404, detail="尚未建立 Showcase")
        sc.published = not sc.published
        if sc.published:
            sc.published_at = _now()
        sc.updated_at = _now()
        await _sync_showcase_to_public(session, sc)
        await session.commit()
        await session.refresh(sc)
    return _to_showcase_dict(sc)


@router.get("/projects/{project_id}/showcase/credits/auto")
async def auto_showcase_credits(project_id: str, request: Request):
    """自動從專案派工生成 credits 列表。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectStaff).where(CrmProjectStaff.project_id == project_id)
        )).scalars().all()
        credits = []
        for ps in rows:
            staff = await session.get(CrmStaff, ps.staff_id)
            if not staff:
                continue
            resume_url = ""
            if staff.resume_visible:
                resume_url = f"/resume.html?id={staff.id}"
            credits.append({
                "name": staff.name,
                "role": ps.role_in_project or staff.role or "",
                "staff_id": staff.id,
                "resume_url": resume_url,
            })
    return {"credits": credits}


@router.post("/projects/{project_id}/showcase/generate-edit-token")
async def generate_showcase_edit_token(project_id: str, request: Request):
    """產生 Showcase 外部編輯永久連結 Token（永遠產新 token，為分享連結「重發」語義）。"""
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        token, _sc = await _mint_showcase_edit_token(session, project_id, reuse_existing=False)
        await session.commit()
    return {"status": "ok", "token": token, "url": f"/showcase-edit.html?token={token}"}


# ── Showcase Public Endpoints ──────────────────────────────

@router.get("/public/showcase/{project_id}")
async def get_public_showcase(project_id: str):
    """公開 Showcase 頁面資料（無需認證），僅限已發布。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        if not sc or not sc.published:
            raise HTTPException(status_code=404, detail="找不到此作品展示")
        proj = await session.get(CrmProject, project_id)
        client_name = ""
        project_name = ""
        if proj:
            project_name = proj.name or ""
            client = await session.get(Client, proj.client_id) if proj.client_id else None
            client_name = client.short_name if client else ""
        data = _to_showcase_dict(sc)
        # Strip internal fields
        data.pop("edit_token", None)
        data.pop("editable", None)
        data["project_name"] = project_name
        data["client_name"] = client_name
    return data


@router.get("/public/showcase-edit/{token}")
async def get_showcase_edit_data(token: str):
    """透過 Token 取得 Showcase 編輯資料（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=False)
        proj = await session.get(CrmProject, sc.id)
        project_name = proj.name if proj else ""
        data = _to_showcase_dict(sc)
        # Strip financial / internal fields
        data.pop("edit_token", None)
        data["project_name"] = project_name
    return data


@router.put("/public/showcase-edit/{token}")
async def update_showcase_edit_data(token: str, request: Request):
    """透過 Token 更新 Showcase 資料（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    body = await request.json()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        if "description" in body:
            sc.description = body["description"]
        if "credits" in body:
            sc.credits = body["credits"]
        if "tags" in body:
            sc.tags = body["tags"]
        if "process_mode" in body:
            sc.process_mode = body["process_mode"]
        if "process_items" in body:
            sc.process_items = body["process_items"]
        if "video_url" in body:
            sc.video_url = body["video_url"]
        if "slug" in body:
            sc.slug = body["slug"] or None
        sc.updated_at = _now()
        await _sync_showcase_to_public(session, sc)
        await session.commit()
    return {"status": "ok"}


@router.post("/public/showcase-edit/{token}/gallery")
async def upload_showcase_edit_gallery(token: str, file: UploadFile = File(...),
                                       caption: str = Query("")):
    """透過 Token 上傳 Showcase 圖庫圖片（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        project_id = sc.id
    ext = (os.path.splitext(file.filename or "img.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    sdir = _showcase_dir(project_id)
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(sdir, fname)
    content = await file.read()
    with open(dest, "wb") as fp:
        fp.write(content)
    url = f"/uploads/projects/{project_id}/showcase/{fname}"
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        gallery = list(sc.gallery or [])
        gallery.append({"url": url, "caption": caption})
        sc.gallery = gallery
        sc.updated_at = _now()
        await session.commit()
        await session.refresh(sc)
    return {"status": "ok", "gallery": sc.gallery or []}


@router.post("/public/showcase-edit/{token}/process")
async def upload_showcase_edit_process(token: str, file: UploadFile = File(...),
                                       caption: str = Query(""),
                                       phase: str = Query(""),
                                       item_type: str = Query("image")):
    """透過 Token 上傳 Showcase 製程項目圖片（無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        sc = await _verify_showcase_edit_token(session, token, require_editable=True)
        project_id = sc.id
    ext = (os.path.splitext(file.filename or "img.jpg")[1] or ".jpg").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail=f"不支援的圖片格式：{ext}")
    sdir = _showcase_dir(project_id)
    fname = f"process_{uuid.uuid4().hex}{ext}"
    dest = os.path.join(sdir, fname)
    content = await file.read()
    with open(dest, "wb") as fp:
        fp.write(content)
    url = f"/uploads/projects/{project_id}/showcase/{fname}"
    item = {"type": item_type, "url": url, "caption": caption, "phase": phase, "video_url": ""}
    async with factory() as session:
        sc = await session.get(CrmProjectShowcase, project_id)
        items = list(sc.process_items or [])
        items.append(item)
        sc.process_items = items
        sc.updated_at = _now()
        await session.commit()
        await session.refresh(sc)
    return {"status": "ok", "process_items": sc.process_items or []}


# ── Site API (for future website) ──────────────────────────

@router.get("/public/site/works")
async def list_published_works():
    """列出所有已發布的 Showcase（供官網使用，無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmProjectShowcase)
            .where(CrmProjectShowcase.published == True)  # noqa: E712
            .order_by(CrmProjectShowcase.published_at.desc())
        )).scalars().all()
        works = []
        for sc in rows:
            proj = await session.get(CrmProject, sc.id)
            client_name = ""
            project_name = ""
            if proj:
                project_name = proj.name or ""
                client = await session.get(Client, proj.client_id) if proj.client_id else None
                client_name = client.short_name if client else ""
            works.append({
                "id": sc.id,
                "project_name": project_name,
                "client_name": client_name,
                "cover_url": sc.cover_url or "",
                "slug": sc.slug or "",
                "tags": sc.tags or [],
                "description": sc.description or "",
                "video_url": sc.video_url or "",
                "published_at": sc.published_at.isoformat() if sc.published_at else None,
            })
    return {"works": works}


@router.get("/public/site/team")
async def list_public_team():
    """列出所有公開履歷的人員（供官網使用，無需認證）。"""
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmStaff)
            .where(CrmStaff.resume_visible == True)  # noqa: E712
            .order_by(CrmStaff.name)
        )).scalars().all()
        team = []
        for s in rows:
            team.append({
                "id": s.id,
                "name": s.name,
                "role": s.role or "",
                "photo_url": s.photo_url or "",
            })
    return {"team": team}
