"""routers/crm/projects.py — 專案管理 + 結案作業看板 + CSV 匯入。

自 routers/api_crm.py 原樣搬移（純搬移，行為不變）。
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import shutil
import uuid

from fastapi import HTTPException, Request, UploadFile, File, Query

from config import load_settings as _load_settings

from core.schemas import CrmProjectPayload, CrmProjectPatchPayload

from ._shared import (router, _check_auth, _check_website_auth, _require_db,
                      _get_factory, _now, _parse_shoot_date,
                      _auto_update_client_status, _seed_default_expenses)

try:
    from ._shared import (select, or_,
                          Client, CrmProject, CrmProjectCostGroup,
                          CrmProjectShowcase)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

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


# NOTE: 必須註冊在 /projects/{project_id} 之前 — 否則 "closing" 會被吃成 project_id。
_WEBSITE_PROD_STAGES = ("待製作", "製作中", "不上官網")


@router.get("/projects/closing")
async def list_closing_projects(request: Request):
    """結案作業看板 — 列出所有 status='結案作業' 專案 + 官網上線/製作狀態 + 完整度。
    （AM 把專案從「已結案」推進到「結案作業」才進此收件匣，避免歷史已結案全列。）

    stage 推導：public=True → '已上線'；否則看 website_prod_stage（製作中/不上官網），
    其餘（含 None）→ '待製作'。completeness 四項各是 bool（是否已備妥該素材）。
    """
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()

    async with factory() as session:
        rows = (await session.execute(
            select(CrmProject, Client.short_name.label("client_short_name"),
                   Client.full_name.label("client_full_name"))
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .where(CrmProject.status == "結案作業")
            .order_by(CrmProject.completion_date.desc().nullslast())
        )).all()

        items = []
        for p, short_name, full_name in rows:
            sc = await session.get(CrmProjectShowcase, p.id)
            if p.public:
                stage = "已上線"
            elif p.website_prod_stage in ("製作中", "不上官網"):
                stage = p.website_prod_stage
            else:
                stage = "待製作"
            video = bool((sc and sc.video_url) or p.public_youtube_id)
            images = bool(
                (sc and sc.gallery and len(sc.gallery) > 0)
                or p.public_featured_image
                or (sc and sc.cover_url)
            )
            process = bool(sc and sc.process_items and len(sc.process_items) > 0)
            credits = bool(
                (sc and sc.credits and len(sc.credits) > 0)
                or (p.public_credits and len(p.public_credits) > 0)
            )
            slug = (p.public_slug or "").strip() or (
                str(p.public_number) if p.public_number is not None else None
            )
            items.append({
                "id": p.id,
                "name": p.name,
                "client_name": short_name or full_name or "",
                "completion_date": p.completion_date.isoformat() if p.completion_date else None,
                "public": bool(p.public),
                "showcase_published": bool(sc.published) if sc else False,
                "stage": stage,
                "public_featured": bool(p.public_featured),
                "slug": slug,
                "completeness": {
                    "video": video,
                    "images": images,
                    "process": process,
                    "credits": credits,
                },
            })

    return {"items": items}


@router.patch("/projects/{project_id}/website-stage")
async def update_project_website_stage(project_id: str, request: Request):
    """設定已結案專案的官網製作階段（待製作/製作中/不上官網）。"""
    _check_website_auth(request)
    _require_db()
    factory = await _get_factory()

    body = await request.json()
    stage = (body.get("stage") or "").strip()
    if stage not in _WEBSITE_PROD_STAGES:
        raise HTTPException(status_code=400, detail="無效的製作階段")

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        project.website_prod_stage = stage
        project.updated_at = _now()
        await session.commit()

    return {"ok": True, "stage": stage}


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
        # 結案作業：專案轉為「結案作業」且尚未指定官網製作階段 → 預設「待製作」（進官網製作收件匣）
        if project.status == "結案作業" and project.website_prod_stage is None:
            project.website_prod_stage = "待製作"
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
    if new_status not in ("洽談中", "報價中", "進行中", "已結案", "結案作業"):
        raise HTTPException(status_code=400, detail="無效的狀態值")

    contract_amount = body.get("contract_amount")
    amount_receivable = body.get("amount_receivable")

    async with factory() as session:
        project = await session.get(CrmProject, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="找不到此專案")
        project.status = new_status
        # 結案作業：轉為「結案作業」且尚未指定官網製作階段 → 預設「待製作」（進官網製作收件匣）
        if new_status == "結案作業" and project.website_prod_stage is None:
            project.website_prod_stage = "待製作"
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

