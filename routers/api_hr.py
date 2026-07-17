"""api_hr.py — 人事管理：出缺勤（請假）API（N-hr H2 極簡版）。

範圍（HR_FIN_PLAN H2）：申請 + 核可 + 年度特休額度。不做打卡鐘/薪資/勞健保。
審核 = 三欄形慣例（status + approved_by + approved_at，照 api_portal 前例）：
核准寫核可人+時間戳、狀態離開已核准即清空、重複核准 409。
特休餘額即時算（core.hr_logic.leave_balance），不另存 ledger。

權限：check_admin_or_module(request, 'hr_leave')。員工自助端在 routers/api_me.py。
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from sqlalchemy import func, select  # type: ignore

from core.auth import check_admin_or_module
from core.db_guard import db_factory_or_503
from core.hr_logic import (ANNUAL_TYPE, LEAVE_STATUSES, leave_balance,
                           leave_to_dict, parse_ymd, validate_leave)
from core.schemas import AnnualLeaveSet, LeaveCreate, LeaveUpdate
from db.models import CrmStaff, HrLeaveRequest

router = APIRouter(prefix="/api/v1/hr", tags=["hr"])


def new_leave_request(staff_id: str, staff_name: str, body, created_by: str):
    """parse + validate + 建構待審單（本檔代登 / api_me 自助 共用）；驗證失敗 raise 422。"""
    start, end = parse_ymd(body.start_date), parse_ymd(body.end_date)
    err = validate_leave(body.leave_type, start, end, body.days)
    if err:
        raise HTTPException(status_code=422, detail=err)
    return HrLeaveRequest(
        id=uuid.uuid4().hex[:12],
        staff_id=staff_id, staff_name=staff_name,
        leave_type=body.leave_type, start_date=start, end_date=end,
        days=body.days, reason=(body.reason or "").strip() or None,
        status="待審", created_by=created_by,
    )


def apply_leave_status(obj, new_status: str, actor: str) -> None:
    """審核狀態轉換：核准寫人+時間戳、離開已核准清空、重複核准 409。
    （本檔區域函式；N2/N3 沿用的是三欄形慣例，不是這支函式。）"""
    if new_status not in LEAVE_STATUSES:
        raise HTTPException(status_code=422, detail=f"status 需為：{'/'.join(LEAVE_STATUSES)}")
    if new_status == "已核准":
        if obj.status == "已核准":
            raise HTTPException(status_code=409, detail="此單已核准過")
        obj.approved_by = actor
        obj.approved_at = datetime.now(timezone.utc)
    elif obj.status == "已核准":
        obj.approved_by = None
        obj.approved_at = None
    obj.status = new_status


async def approved_annual_used(session, staff_ids: list, year: int) -> dict:
    """各 staff 當年度已核准特休合計（start_date 落在該年）。回 {staff_id: days}。"""
    if not staff_ids:
        return {}
    y0, y1 = datetime(year, 1, 1), datetime(year + 1, 1, 1)
    rows = (await session.execute(
        select(HrLeaveRequest.staff_id, func.coalesce(func.sum(HrLeaveRequest.days), 0.0))
        .where(HrLeaveRequest.staff_id.in_(staff_ids))
        .where(HrLeaveRequest.leave_type == ANNUAL_TYPE)
        .where(HrLeaveRequest.status == "已核准")
        .where(HrLeaveRequest.start_date >= y0)
        .where(HrLeaveRequest.start_date < y1)
        .group_by(HrLeaveRequest.staff_id)
    )).all()
    return {sid: float(total or 0) for sid, total in rows}


@router.get("/leave")
async def list_leave(request: Request, status: str = "", staff_id: str = "", year: int = 0):
    check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        stmt = select(HrLeaveRequest)
        if status:
            stmt = stmt.where(HrLeaveRequest.status == status)
        if staff_id:
            stmt = stmt.where(HrLeaveRequest.staff_id == staff_id)
        if year:
            stmt = (stmt.where(HrLeaveRequest.start_date >= datetime(year, 1, 1))
                        .where(HrLeaveRequest.start_date < datetime(year + 1, 1, 1)))
        rows = (await session.execute(
            stmt.order_by(HrLeaveRequest.start_date.desc().nulls_last()).limit(500)
        )).scalars().all()
        return {"items": [leave_to_dict(r) for r in rows], "total": len(rows)}


@router.post("/leave")
async def create_leave(body: LeaveCreate, request: Request):
    """管理端建立/代登（員工自助走 POST /api/v1/me/leave）。"""
    payload = check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        staff = await session.get(CrmStaff, body.staff_id)
        if staff is None:
            raise HTTPException(status_code=404, detail="人員不存在")
        obj = new_leave_request(staff.id, staff.name, body, (payload or {}).get("sub"))
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
        return leave_to_dict(obj)


@router.put("/leave/{leave_id}")
async def update_leave(leave_id: str, body: LeaveUpdate, request: Request):
    payload = check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    data = body.model_dump(exclude_unset=True)
    async with factory() as session:
        obj = await session.get(HrLeaveRequest, leave_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到請假單")
        # 欄位編輯（先套欄位再驗證整體）
        if "leave_type" in data and data["leave_type"]:
            obj.leave_type = data["leave_type"]
        if "start_date" in data:
            obj.start_date = parse_ymd(data["start_date"]) or obj.start_date
        if "end_date" in data:
            obj.end_date = parse_ymd(data["end_date"]) or obj.end_date
        if "days" in data and data["days"] is not None:
            obj.days = data["days"]
        if "reason" in data:
            obj.reason = (data["reason"] or "").strip() or None
        err = validate_leave(obj.leave_type, obj.start_date, obj.end_date, obj.days)
        if err:
            raise HTTPException(status_code=422, detail=err)
        if data.get("status"):
            apply_leave_status(obj, data["status"], (payload or {}).get("sub") or "")
        await session.commit()
        await session.refresh(obj)
        return leave_to_dict(obj)


@router.delete("/leave/{leave_id}")
async def delete_leave(leave_id: str, request: Request):
    check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await session.get(HrLeaveRequest, leave_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到請假單")
        await session.delete(obj)
        await session.commit()
    return {"deleted": leave_id}


@router.get("/leave/quota")
async def leave_quota(request: Request, year: int = 0):
    """在職人員的特休額度總覽：{annual, used(當年已核准特休), remaining}。"""
    check_admin_or_module(request, "hr_leave")
    year = year or datetime.now().year
    factory = db_factory_or_503()
    async with factory() as session:
        staff_rows = (await session.execute(
            select(CrmStaff).where(CrmStaff.status == "在職").order_by(CrmStaff.name)
        )).scalars().all()
        used_map = await approved_annual_used(session, [s.id for s in staff_rows], year)
        return {"year": year, "staff": [{
            "staff_id": s.id, "name": s.name, "role": s.role or "",
            **leave_balance(s.annual_leave_days, used_map.get(s.id, 0.0)),
        } for s in staff_rows]}


@router.put("/staff/{staff_id}/annual_leave")
async def set_annual_leave(staff_id: str, body: AnnualLeaveSet, request: Request):
    """設定年度特休額度（天）。"""
    check_admin_or_module(request, "hr_leave")
    if body.annual_leave_days is not None and body.annual_leave_days < 0:
        raise HTTPException(status_code=422, detail="額度不可為負")
    factory = db_factory_or_503()
    async with factory() as session:
        s = await session.get(CrmStaff, staff_id)
        if s is None:
            raise HTTPException(status_code=404, detail="人員不存在")
        s.annual_leave_days = body.annual_leave_days
        await session.commit()
    return {"status": "ok", "staff_id": staff_id, "annual_leave_days": body.annual_leave_days}
