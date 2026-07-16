"""routers/crm/clients.py — 客戶管理 + CSV 匯入 + AM/PM 使用者列表。

自 routers/api_crm.py 原樣搬移（純搬移，行為不變）。
"""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import HTTPException, Request, UploadFile, File, Query

from core.schemas import ClientPayload

from ._shared import (router, _check_auth, _require_db, _get_factory,
                      _now, _to_dict, _auto_update_client_status,
                      _CLIENT_TIER_EXCLUDE_STATUSES)

try:
    from ._shared import (select, or_, sa_update, IntegrityError,
                          Client, CrmProject, User)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

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

    # Subquery: per-client project stats (exclude not-yet-won 階段；
    # 口徑集中在 _shared._CLIENT_TIER_EXCLUDE_STATUSES，與客戶分級同源)
    active_filter = CrmProject.status.notin_(_CLIENT_TIER_EXCLUDE_STATUSES)
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
        # status 由「客戶分級」自動管理（_auto_update_client_status）。客戶編輯表單
        # 不送 status，若照 ClientPayload 預設值（'潛在客戶'）寫回會把分級洗掉 ——
        # 這正是「改業務後客戶屬性跳掉」的成因。排除它，再依專案數重算。
        for k, v in req.model_dump(exclude={"status"}).items():
            setattr(client, k, v)
        client.updated_at = _now()
        await _auto_update_client_status(session, client_id)  # 重算分級（保留手動『暫停合作』）
        await session.commit()
        await session.refresh(client)

        # Sync AM to active projects when client AM changed
        if req.am_username and req.am_username != old_am:
            await session.execute(
                sa_update(CrmProject)
                .where(CrmProject.client_id == client_id)
                .where(CrmProject.status.in_(["投標", "開發", "洽詢", "提案", "製作"]))
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

