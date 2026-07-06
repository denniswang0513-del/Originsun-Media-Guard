"""routers/crm/staff.py — 人力資源：人員庫 / CSV 匯入 / 履歷 / 作品集 /
Token 自編履歷 / 專案派工。

自 routers/api_crm.py 原樣搬移（純搬移，行為不變）。
staff_resume_pdf 內以 __file__ 推 templates 路徑的運算多包一層
os.path.dirname（檔案移深一層，維持原專案根目錄基準）。
"""
from __future__ import annotations

import csv
import io
import os
import uuid
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, Request, UploadFile, File, Query

from core.schemas import StaffPayload, ResumePayload, ProjectStaffPayload

from ._shared import (router, _check_auth, _require_db, _get_factory, _now,
                      STAFF_CREATED_VIA_ADMIN, _UPLOAD_BASE, _ALLOWED_IMG_EXT,
                      _verify_token_generic)

try:
    from ._shared import (select, or_,
                          Client, CrmProject, CrmStaff, CrmStaffPortfolio,
                          CrmProjectStaff, WEBSITE_TEAM_OVERRIDE_FIELDS)
except ImportError:  # DB 套件不存在的 agent 環境 — 行為同原檔 try/except
    pass

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
        # 官網呈現覆寫（與「官網管理 › 關於我們」團隊卡同步，寫同一批 crm_staff 欄位）
        "show_on_website": bool(s.show_on_website) if s.show_on_website is not None else False,
        "website_title": s.website_title or "",
        "website_photo_url": s.website_photo_url or "",
        "website_bio": s.website_bio or "",
        "website_sort_order": s.website_sort_order if s.website_sort_order is not None else 0,
        "created_via": s.created_via or STAFF_CREATED_VIA_ADMIN,
        "created_for_project_id": s.created_for_project_id,
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
    # name 在 schema 已放寬為 Optional（讓 PUT 可部分更新）；新增時這裡明確要求。
    if not (req.name or "").strip():
        raise HTTPException(status_code=422, detail="姓名為必填")
    factory = await _get_factory()
    now = _now()
    # exclude_unset：未送的官網覆寫欄位（Optional=None）交給 column default（False/0），
    # 不存 explicit None；正本欄位有自己的 schema 預設值，照常帶入。
    s = CrmStaff(id=uuid.uuid4().hex, created_at=now, updated_at=now,
                 **req.model_dump(exclude_unset=True))
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
        # exclude_unset：只寫前端「實際送出」的欄位。前端的 inline 編輯 / 新增 modal /
        # 官網呈現 section 各自只送自己那組欄位 — 部分更新時，沒送到的欄位（含正本
        # name/role/rates 與官網 website_* 覆寫）一律保持原值，不會被 None 蓋掉。
        patch = req.model_dump(exclude_unset=True)
        for k, v in patch.items():
            setattr(s, k, v)
        s.updated_at = _now()
        await session.commit()
        await session.refresh(s)
        result = _to_staff_dict(s)
    # 若這次更新動到任何官網呈現欄位 → 觸發官網 rebuild（與官網管理端團隊卡一致）。
    # 欄位集引用 db.models 的單一定義（與 admin_team 白名單同源）。
    # lazy import + 寬鬆 except：純 agent 環境沒有 services/website 或 DB 不可用時，
    # CRM 編輯絕不可因此失敗。
    if set(WEBSITE_TEAM_OVERRIDE_FIELDS) & patch.keys():
        try:
            from services.website import rebuild_service
            await rebuild_service.mark_dirty()
        except Exception:
            pass
    return {"status": "ok", "staff": result}


@router.delete("/staff/{staff_id}")
async def delete_staff(staff_id: str, request: Request):
    """刪除人員。同時清除 showcase.credits.entries 內所有此 staff_id 引用
    （保留 name snapshot，移除 staff_id + resume_url 避免 dangling reference）。
    """
    _check_auth(request)
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if not s:
            raise HTTPException(status_code=404, detail="找不到此人員")
        # Cascade：清除 showcase 引用，避免對外作品頁簡歷連結 404
        try:
            from services.website.credit_service import cleanup_staff_id_from_credits
            await cleanup_staff_id_from_credits(session, staff_id)
        except ImportError:
            # services/website/ 在純 agent 環境可能不存在 — 不阻擋刪除
            pass
        await session.delete(s)
        await session.commit()
    return {"status": "ok"}


@router.get("/staff/{staff_id}/projects")
async def get_staff_projects(staff_id: str):
    """查詢人員的專案紀錄（派工 + 演職員 credit 出現位置）。"""
    from collections import defaultdict
    _require_db()
    factory = await _get_factory()
    async with factory() as session:
        # 派工（既有）— crm_project_staff 沒對 (staff_id, project_id) 加 unique，
        # 同 staff 在同 project 多階段（前期/拍攝/後期）會有多筆，依派工 row id 留全。
        rows = (await session.execute(
            select(CrmProjectStaff, CrmProject.name.label("project_name"),
                   Client.short_name.label("client_name"))
            .outerjoin(CrmProject, CrmProject.id == CrmProjectStaff.project_id)
            .outerjoin(Client, Client.id == CrmProject.client_id)
            .where(CrmProjectStaff.staff_id == staff_id)
        )).all()
        assigned_pids = {r[0].project_id for r in rows}

        # showcase.credits 反查 — 委派 service 層（避免 lazy import 失敗時整個 endpoint 壞）
        try:
            from services.website.credit_service import find_projects_by_staff
            credit_rows = await find_projects_by_staff(session, staff_id)
        except ImportError:
            credit_rows = []

        credits_by_pid: dict[str, list[dict]] = defaultdict(list)
        for cr in credit_rows:
            credits_by_pid[cr["project_id"]].append({
                "role_zh": cr["role_zh"],
                "duty": cr["duty"],
            })

        # 派工每筆獨立保留（同專案多階段不合併）；credit_in_project 每筆派工都帶
        # 同樣的 project credit 列表（前端按 project_id 分組顯示時用）。
        projects = [{
            "id": r[0].id, "project_id": r[0].project_id,
            "project_name": r[1] or "", "client_name": r[2] or "",
            "role_in_project": r[0].role_in_project or "",
            "days": r[0].days, "cost": r[0].cost,
            "notes": r[0].notes or "",
            "credits_in_project": credits_by_pid.get(r[0].project_id, []),
        } for r in rows]

        # 沒派工但出現在 credit 的作品
        credit_only_pids = set(credits_by_pid.keys()) - assigned_pids
        credit_only = []
        if credit_only_pids:
            co_rows = (await session.execute(
                select(CrmProject.id, CrmProject.name, Client.short_name)
                .outerjoin(Client, Client.id == CrmProject.client_id)
                .where(CrmProject.id.in_(credit_only_pids))
            )).all()
            co_by_id = {r[0]: (r[1] or "", r[2] or "") for r in co_rows}
            for pid in credit_only_pids:
                name, client = co_by_id.get(pid, ("", ""))
                credit_only.append({
                    "project_id": pid,
                    "project_name": name,
                    "client_name": client,
                    "credits_in_project": credits_by_pid[pid],
                })

    return {"projects": projects, "credit_only_projects": credit_only}


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
    _base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

