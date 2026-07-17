"""api_me.py — 個人工作台 API（N0 個人帳號化）。

供獨立頁 /my.html 使用：登入者只看見「自己的」資料 —— own-scope 一律經
core.identity.resolve_current_staff（token → users.staff_id → crm_staff），
絕不接受 client 傳入的 staff_id / username。

權限 = 細粒度四 key（owner 在「使用者管理」逐帳號勾選控制哪些卡開放）：
    me_projects  我的專案（派工）        — 鍵 crm_project_staff.staff_id（乾淨）
    me_profile   我的個人資料/履歷        — 鍵 crm_staff.id（乾淨；PUT 白名單）
    me_todos     我的待辦（公布欄）       — 鍵 username（不需綁定人員檔案）
    me_finance   我的工時+請款（摘要）    — ⚠ name-match（Timesheet.staff_name /
                 CrmPaymentRequest.payee_name 是字串），單人 owner 階段安全；
                 全員推行前必須先回填 staff_id（見 ROADMAP N2）。

管理員（Lv3）經 grant_admin_all_modules 自動擁有四 key。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from sqlalchemy import func, select  # type: ignore

from core.auth import check_admin_or_module, grant_admin_all_modules
from core.db_guard import db_factory_or_503
from core.identity import resolve_current_staff
from core.schemas import MeProfileUpdate, MeTodoUpdate
from db.models import (BulletinItem, Client, CrmPaymentRequest, CrmProject,
                       CrmProjectStaff, Timesheet)

router = APIRouter(prefix="/api/v1/me", tags=["me"])

ME_MODULE_KEYS = ("me_projects", "me_profile", "me_todos", "me_finance")


def _allowed_sections(payload: dict) -> list:
    """依 token 算出此帳號開放的卡片 key。
    「admin 全開」交給 grant_admin_all_modules（RBAC 單一來源，勿在此重寫）。"""
    mods = grant_admin_all_modules(payload.get("access_level"), payload.get("modules") or [])
    return [k for k in ME_MODULE_KEYS if k in mods]


def _profile_dict(s) -> dict:
    """crm_staff 安全子集 — 不含費率/身分證/銀行/website_* 欄位。"""
    return {
        "name": s.name, "role": s.role or "",
        "phone": s.phone or "", "email": s.email or "",
        "address": s.address or "",
        "emergency_contact": s.emergency_contact or "",
        "portfolio_url": s.portfolio_url or "",
        "photo_url": s.photo_url or "",
        "bio": s.bio or "",
        "skills": s.skills or [], "education": s.education or [],
        "experience": s.experience or [], "awards": s.awards or [],
        "employment_type": s.employment_type or "",
        "hire_date": s.hire_date.strftime("%Y-%m-%d") if s.hire_date else "",
        "status": s.status or "在職",
    }


@router.get("/workspace")
async def my_workspace(request: Request):
    """個人工作台 bundle — 只回帳號有權限的區塊；未綁定人員檔案時
    人員鍵區塊（profile/projects/finance）回空並帶 bound=False。"""
    payload = check_admin_or_module(request, *ME_MODULE_KEYS)
    allowed = _allowed_sections(payload)
    ident = await resolve_current_staff(request)
    staff = ident["staff"]
    out = {
        "username": ident["username"],
        "staff_id": ident["staff_id"],
        "bound": staff is not None,
        "allowed": allowed,
    }
    factory = db_factory_or_503()
    async with factory() as session:
        if "me_profile" in allowed and staff is not None:
            out["profile"] = _profile_dict(staff)

        if "me_projects" in allowed and staff is not None:
            rows = (await session.execute(
                select(CrmProjectStaff, CrmProject.name, CrmProject.status,
                       Client.short_name)
                .outerjoin(CrmProject, CrmProject.id == CrmProjectStaff.project_id)
                .outerjoin(Client, Client.id == CrmProject.client_id)
                .where(CrmProjectStaff.staff_id == ident["staff_id"])
                .order_by(CrmProjectStaff.id.desc())
            )).all()
            out["projects"] = [{
                "project_id": r[0].project_id,
                "project_name": r[1] or "", "project_status": r[2] or "",
                "client_name": r[3] or "",
                "role_in_project": r[0].role_in_project or "",
                "phase": r[0].phase or "",
                "days": r[0].days, "actual_days": r[0].actual_days,
                "cost": r[0].cost, "payment_status": r[0].payment_status or "",
                "notes": r[0].notes or "",
            } for r in rows]

        if "me_todos" in allowed:
            # 鍵 username — 不需綁定人員檔案也能用
            rows = (await session.execute(
                select(BulletinItem)
                .where(BulletinItem.mine_filter(ident["username"] or ""))
                .where(BulletinItem.status != "done")
                .order_by(BulletinItem.pinned.desc(), BulletinItem.sort_order,
                          BulletinItem.created_at)
            )).scalars().all()
            out["todos"] = [{
                "id": o.id, "title": o.title, "note": o.note or "",
                "status": o.status, "priority": o.priority,
                "pinned": bool(o.pinned),
                "assigned_to_me": o.assignee_username == ident["username"],
                "created_at": o.created_at.isoformat() if o.created_at else None,
            } for o in rows]

        if "me_finance" in allowed and staff is not None:
            # ⚠ name-match（脆弱）：見模組 docstring。
            name = staff.name
            ts_rows = (await session.execute(
                select(Timesheet.project_name,
                       func.sum(Timesheet.hours), func.count(Timesheet.id),
                       func.max(Timesheet.work_date))
                .where(Timesheet.staff_name == name)
                .group_by(Timesheet.project_name)
                .order_by(func.max(Timesheet.work_date).desc())
            )).all()
            month_start = datetime.now().astimezone().replace(
                day=1, hour=0, minute=0, second=0, microsecond=0)
            month_hours = (await session.execute(
                select(func.coalesce(func.sum(Timesheet.hours), 0.0))
                .where(Timesheet.staff_name == name)
                .where(Timesheet.work_date >= month_start)
            )).scalar() or 0.0
            out["timesheet"] = {
                "total_hours": round(sum(r[1] or 0 for r in ts_rows), 1),
                "month_hours": round(month_hours, 1),
                "by_project": [{
                    "project_name": r[0] or "(空白)",
                    "hours": round(r[1] or 0, 1), "rows": r[2],
                    "last_entry": r[3].strftime("%Y-%m-%d") if r[3] else None,
                } for r in ts_rows[:10]],
            }

            _unpaid = CrmPaymentRequest.payment_status != "已付款"
            totals = (await session.execute(
                select(func.coalesce(func.sum(CrmPaymentRequest.amount), 0),
                       func.count(CrmPaymentRequest.id).filter(_unpaid),
                       func.coalesce(func.sum(CrmPaymentRequest.amount).filter(_unpaid), 0))
                .where(CrmPaymentRequest.payee_name == name)
            )).one()
            recent = (await session.execute(
                select(CrmPaymentRequest)
                .where(CrmPaymentRequest.payee_name == name)
                .order_by(CrmPaymentRequest.request_date.desc().nulls_last())
                .limit(5)
            )).scalars().all()
            out["payments"] = {
                "total_amount": int(totals[0] or 0),
                "unpaid_count": int(totals[1] or 0),
                "unpaid_amount": int(totals[2] or 0),
                # 摘要即可 — 不回 payee_id / 銀行資訊
                "recent": [{
                    "date": p.request_date.strftime("%Y-%m-%d") if p.request_date else "",
                    "amount": p.amount or 0, "summary": p.summary or "",
                    "status": p.payment_status or "",
                    "project": p.project_label or "",
                } for p in recent],
            }
    return out


@router.put("/profile")
async def update_my_profile(body: MeProfileUpdate, request: Request):
    """本人編輯自己的人員檔案 — 僅白名單欄位；target 一律由 token 解析。"""
    check_admin_or_module(request, "me_profile")
    ident = await resolve_current_staff(request)
    if ident["staff"] is None:
        raise HTTPException(status_code=409, detail="帳號尚未綁定人員檔案，請聯絡管理員")
    factory = db_factory_or_503()
    # 白名單 = MeProfileUpdate schema 本身（pydantic 已擋掉其他欄位），不另列清單
    data = body.model_dump(exclude_unset=True)
    async with factory() as session:
        from db.models import CrmStaff
        s = await session.get(CrmStaff, ident["staff_id"])
        if s is None:
            raise HTTPException(status_code=404, detail="人員檔案不存在")
        for k, v in data.items():
            if isinstance(v, str):
                v = v.strip() or None
            setattr(s, k, v)
        s.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(s)
        return {"status": "ok", "profile": _profile_dict(s)}


@router.put("/todos/{item_id}")
async def update_my_todo(item_id: str, body: MeTodoUpdate, request: Request):
    """本人待辦改狀態（todo/doing/done）— 只允許動「與我有關」的項目。"""
    payload = check_admin_or_module(request, "me_todos")
    if body.status not in ("todo", "doing", "done"):
        raise HTTPException(status_code=422, detail="status 需為 todo/doing/done")
    username = (payload or {}).get("sub") or ""
    factory = db_factory_or_503()
    async with factory() as session:
        obj = (await session.execute(
            select(BulletinItem).where(BulletinItem.id == item_id)
            .where(BulletinItem.mine_filter(username))
        )).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到項目或非你的待辦")
        obj.status = body.status
        obj.done_at = datetime.now() if body.status == "done" else None
        await session.commit()
    return {"status": "ok"}
