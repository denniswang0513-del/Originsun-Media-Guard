"""api_hr.py — 人事管理：假勤（請假單／時數帳／假日表）管理端 API。

2026-09-07 假勤重整（docs/LEAVE_PLAN.md §7.5）：
  · 狀態轉換各走專屬端點：approve（FIFO 扣時數帳）／reject（理由必填）／cancel_decide（消假）；
    PUT 只改欄位，帶 status 一律 422。
  · 時數帳（hr_leave_credits ＋ hr_leave_allocations）：餘額不存快照，services.leave_service 每次重算。
  · 假日表 hr_holidays：行政院行事曆 CSV 匯入（core.leave_logic.parse_gov_calendar_csv）。
  · 舊端點 /leave/quota、/staff/{id}/annual_leave 保留給舊分頁（crm_staff.annual_leave_days 那條線）。

權限：check_admin_or_module(request, 'hr_leave')。員工自助端在 routers/api_me.py。
"""
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from sqlalchemy import func, select  # type: ignore

from core.auth import check_admin_or_module
from core.db_guard import db_factory_or_503
from core.hr_logic import ANNUAL_TYPE, day_iso, leave_balance, parse_ymd, tw_day
from core.leave_logic import (ALL_LEAVE_TYPES, CREDIT_KINDS, CREDIT_SOURCES, HOLIDAY_KINDS, HOURS_PER_DAY,
                              LEDGER_TYPES, NOTICE_DAYS, PARTS, annual_days_for, as_date, hours_to_days,
                              parse_gov_calendar_csv, vocab as leave_vocab, working_hours)
from core.schemas import (AnnualLeaveSet, CreditCreate, HolidayCreate, HolidayImport, LeaveCancel,
                          LeaveCancelDecide, LeaveCreate, LeaveReject, LeaveUpdate)
from db.models import CrmStaff, HrHoliday, HrLeaveAllocation, HrLeaveCredit, HrLeaveRequest
from services import leave_service

router = APIRouter(prefix="/api/v1/hr", tags=["hr"])


def _actor(payload) -> str:
    return (payload or {}).get("sub") or ""


async def _notify_result(result: str, d: dict, note: str = "") -> None:
    """核准／退回／消假決定 → 貼 Google Chat 群（best-effort；精簡 agent 可能沒帶 notifier）。"""
    try:
        from notifier import notify_tab_async
        await notify_tab_async(
            "leave_result", result=result,
            staff_name=d["staff_name"], leave_type=d["leave_type"],
            start=d["start_date"], end=d["end_date"], hours=f"{float(d['hours'] or 0):g}",
            note=f"\n說明：{note}" if note else "",
        )
    except Exception:
        pass


async def _get_leave(session, leave_id: str) -> HrLeaveRequest:
    obj = await session.get(HrLeaveRequest, leave_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="找不到請假單")
    return obj


async def approved_annual_used(session, staff_ids: list, year: int) -> dict:
    """各 staff 當年度已核准特休合計（start_date 落在該年）。回 {staff_id: days}。（舊額度線，/leave/quota 與工作台用）"""
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


# ── 請假單 ──────────────────────────────────────────────────────────────────

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
        holidays = await leave_service.holidays_map(session)
        return {"items": [leave_service.request_dict(r, holidays) for r in rows], "total": len(rows),
                "vocab": leave_vocab()}


@router.post("/leave")
async def create_leave(body: LeaveCreate, request: Request):
    """管理端建立/代登（員工自助走 POST /api/v1/me/leave）。同員工端規則：重疊／不足／範圍錯 → 422。"""
    payload = check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        staff = await session.get(CrmStaff, body.staff_id)
        if staff is None:
            raise HTTPException(status_code=404, detail="人員不存在")
        holidays = await leave_service.holidays_map(session)
        ev = await leave_service.evaluate(session, staff.id, staff.name, body, holidays=holidays)
        if ev["errors"]:
            raise HTTPException(status_code=422, detail="；".join(e["msg"] for e in ev["errors"]))
        hours, part = leave_service.hours_from_body(body, holidays)
        obj = leave_service.build_request(staff.id, staff.name, body, hours, part, _actor(payload))
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
        out = leave_service.request_dict(obj, holidays)
    out["warnings"] = ev["warnings"]
    return out


@router.get("/leave/quota")
async def leave_quota(request: Request, year: int = 0):
    """在職人員的特休額度總覽（舊額度線：crm_staff.annual_leave_days − 當年已核准特休）；新分頁看 /balances。"""
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


@router.get("/leave/{leave_id}/context")
async def leave_context(leave_id: str, request: Request):
    """待核卡片要的：{request, balance:{available,reserved,expiring,enough,short}, same_period:[…],
    shoot_conflicts:[…], notice_days, allocations:[…]}。"""
    check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await _get_leave(session, leave_id)
        holidays = await leave_service.holidays_map(session)
        today = date.today()
        start, end = tw_day(obj.start_date), tw_day(obj.end_date)
        hours = float(obj.hours if obj.hours is not None else (obj.days or 0) * HOURS_PER_DAY)
        bal = None
        if obj.leave_type in LEDGER_TYPES:
            b = (await leave_service.balances_for(session, [obj.staff_id], today, exclude_id=obj.id))[obj.staff_id][obj.leave_type]
            free = round(b["available"] - b["reserved"], 2)
            bal = {**b, "kind": obj.leave_type, "needed": hours, "enough": free >= hours,
                   "short": round(max(0.0, hours - free), 2)}
        same = [{"id": r.id, "staff_id": r.staff_id, "staff_name": r.staff_name or "", "leave_type": r.leave_type,
                 "start": day_iso(r.start_date) or "", "end": day_iso(r.end_date) or "", "status": r.status}
                for r in await leave_service.active_between(session, start, end, exclude_id=obj.id)
                if r.staff_id != obj.staff_id]
        shoots = await leave_service.shoot_conflicts_for(session, obj.staff_id, obj.staff_name or "", start, end)
        allocs = (await session.execute(
            select(HrLeaveAllocation, HrLeaveCredit)
            .outerjoin(HrLeaveCredit, HrLeaveCredit.id == HrLeaveAllocation.credit_id)
            .where(HrLeaveAllocation.request_id == obj.id))).all()
        created = tw_day(obj.created_at) or today
        return {
            "request": leave_service.request_dict(obj, holidays, today),
            "balance": bal, "same_period": same, "shoot_conflicts": shoots,
            "notice_days": (start - created).days if start else None, "notice_min_days": NOTICE_DAYS,
            "allocations": [{"credit_id": a.credit_id, "hours": a.hours,
                             "reason": (c.reason if c else "") or "", "expires_on": (c.expires_on.isoformat() if c and c.expires_on else None)}
                            for a, c in allocs],
        }


@router.post("/leave/{leave_id}/approve")
async def approve_leave(leave_id: str, request: Request):
    """待審→已核准：走時數帳的先 FIFO 分配寫 hr_leave_allocations（不足 422）；通知申請人（leave_result）。"""
    payload = check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await _get_leave(session, leave_id)
        parts = await leave_service.approve_request(session, obj, _actor(payload))
        await session.commit()
        await session.refresh(obj)
        out = leave_service.request_dict(obj, await leave_service.holidays_map(session))
    out["allocations"] = [{"credit_id": cid, "hours": h} for cid, h in parts]
    await _notify_result("核准", out)
    return out


@router.post("/leave/{leave_id}/reject")
async def reject_leave(leave_id: str, body: LeaveReject, request: Request):
    """待審→已退回（理由必填）；通知申請人。"""
    payload = check_admin_or_module(request, "hr_leave")
    note = (body.note or "").strip()
    if not note:
        raise HTTPException(status_code=422, detail="退回要填理由")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await _get_leave(session, leave_id)
        if obj.status != "待審":
            raise HTTPException(status_code=409, detail=f"此單狀態是「{obj.status}」，只有待審可退回（消假走 cancel_decide）")
        obj.status = "已退回"
        obj.reject_note = note
        obj.approved_by = _actor(payload)
        obj.approved_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(obj)
        out = leave_service.request_dict(obj)
    await _notify_result("退回", out, note)
    return out


@router.post("/leave/{leave_id}/cancel_decide")
async def decide_cancel(leave_id: str, body: LeaveCancelDecide, request: Request):
    """消假待審 → approve=True：已撤回（釋放 allocations）／False：回已核准；通知申請人。"""
    payload = check_admin_or_module(request, "hr_leave")
    note = (body.note or "").strip()
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await _get_leave(session, leave_id)
        if obj.status != "消假待審":
            raise HTTPException(status_code=409, detail=f"此單狀態是「{obj.status}」，不是消假待審")
        if body.approve:
            await leave_service.release_allocations(session, obj.id)
            obj.status = "已撤回"
            obj.approved_by = _actor(payload)
            obj.approved_at = datetime.now(timezone.utc)
        else:
            obj.status = "已核准"
        if note:
            obj.reject_note = note
        await session.commit()
        await session.refresh(obj)
        out = leave_service.request_dict(obj, await leave_service.holidays_map(session))
    await _notify_result("消假核准" if body.approve else "消假退回", out, note)
    return out


@router.put("/leave/{leave_id}")
async def update_leave(leave_id: str, body: LeaveUpdate, request: Request):
    """只改欄位（假別／起迄／半天時段／時數／事由）。**不接受 status**（422）—— 改狀態走 approve／reject／cancel_decide。
    已核准／消假待審的單只准改事由（時數已扣帳，改期要先撤回再送）。"""
    check_admin_or_module(request, "hr_leave")
    data = body.model_dump(exclude_unset=True)
    if data.get("status") is not None:
        raise HTTPException(status_code=422, detail="PUT 不改狀態：核准／退回／消假請走 approve、reject、cancel_decide")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await _get_leave(session, leave_id)
        holidays = await leave_service.holidays_map(session)
        field_keys = ("leave_type", "start_date", "end_date", "days", "part", "start_time", "end_time", "hours")
        touching = [k for k in field_keys if k in data and data[k] is not None]
        if touching and obj.status in ("已核准", "消假待審"):
            raise HTTPException(status_code=409, detail="已核准的單時數已扣帳，只能改事由；改期請先撤回再重送")
        if "leave_type" in data and data["leave_type"]:
            if data["leave_type"] not in ALL_LEAVE_TYPES:
                raise HTTPException(status_code=422, detail=f"假別需為：{'/'.join(ALL_LEAVE_TYPES)}")
            obj.leave_type = data["leave_type"]
        if "start_date" in data:
            obj.start_date = parse_ymd(data["start_date"]) or obj.start_date
        if "end_date" in data:
            obj.end_date = parse_ymd(data["end_date"]) or obj.end_date
        if data.get("part"):
            if data["part"] not in PARTS:
                raise HTTPException(status_code=422, detail=f"part 需為：{'/'.join(PARTS)}")
            obj.part = data["part"]
        if "start_time" in data:
            obj.start_time = (data["start_time"] or "").strip() or None
        if "end_time" in data:
            obj.end_time = (data["end_time"] or "").strip() or None
        if "reason" in data:
            obj.reason = (data["reason"] or "").strip() or None
        if obj.end_date < obj.start_date:
            raise HTTPException(status_code=422, detail="迄日不可早於起日")
        if touching:
            # 時數：明給 hours 用它；舊分頁給 days 用 days×8；否則照起迄／半天時段重算
            if data.get("hours") is not None:
                hours = float(data["hours"])
            elif data.get("days") is not None and not data.get("part"):
                hours = float(data["days"]) * HOURS_PER_DAY
            else:
                try:
                    hours = working_hours(tw_day(obj.start_date), tw_day(obj.end_date), obj.part or "all",
                                          obj.start_time, obj.end_time, holidays)
                except ValueError as e:
                    raise HTTPException(status_code=422, detail=str(e))
            if hours <= 0 or round(hours * 2) != hours * 2:
                raise HTTPException(status_code=422, detail="時數需大於 0，且以 0.5 小時為最小單位")
            obj.hours = round(hours, 2)
            obj.days = hours_to_days(hours)
        await session.commit()
        await session.refresh(obj)
        return leave_service.request_dict(obj, holidays)


@router.delete("/leave/{leave_id}")
async def delete_leave(leave_id: str, request: Request):
    """硬刪（管理員清錯單用）；已核准的會先釋放 allocations。員工撤回不走這裡（狀態改已撤回）。"""
    check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await _get_leave(session, leave_id)
        await leave_service.release_allocations(session, obj.id)
        await session.delete(obj)
        await session.commit()
    return {"deleted": leave_id}


# ── 時數帳 ──────────────────────────────────────────────────────────────────

@router.get("/balances")
async def all_balances(request: Request, year: int = 0):
    """全員（在職）：特休／補休 available／reserved／expiring、病假已用、法定特休天數。"""
    check_admin_or_module(request, "hr_leave")
    today = date.today()
    year = year or today.year
    factory = db_factory_or_503()
    async with factory() as session:
        staff_rows = (await session.execute(
            select(CrmStaff).where(CrmStaff.status == "在職").order_by(CrmStaff.name))).scalars().all()
        ids = [s.id for s in staff_rows]
        bal = await leave_service.balances_for(session, ids, today)
        sick = await leave_service.sick_used_for(session, ids, year)
        pending = (await session.execute(
            select(HrLeaveRequest.staff_id, func.count(HrLeaveRequest.id))
            .where(HrLeaveRequest.staff_id.in_(ids))
            .where(HrLeaveRequest.status.in_(("待審", "消假待審")))
            .group_by(HrLeaveRequest.staff_id))).all() if ids else []
        pend = {sid: int(n) for sid, n in pending}
        return {"year": year, "today": today.isoformat(), "vocab": leave_vocab(), "staff": [{
            "staff_id": s.id, "name": s.name, "role": s.role or "",
            "hire_date": day_iso(s.hire_date) or "",
            "annual_days_by_law": annual_days_for(tw_day(s.hire_date), today),
            "balances": bal.get(s.id, {}),
            "sick_used_days": sick.get(s.id, 0.0),
            "pending_count": pend.get(s.id, 0),
        } for s in staff_rows]}


@router.get("/credits")
async def list_credits(request: Request, staff_id: str = "", year: int = 0, status: str = ""):
    """credit 明細（含每筆已被扣多少 used／remaining）。不給 staff_id＝全員。"""
    check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        if staff_id:
            ids = [staff_id]
        else:
            ids = [i for (i,) in (await session.execute(select(HrLeaveCredit.staff_id).distinct())).all()]
        by_staff = await leave_service.credits_for(session, ids, status=status, year=year)
        items = [c for sid in ids for c in by_staff.get(sid, [])]
        return {"items": items, "total": len(items)}


@router.post("/credits")
async def create_credit(body: CreditCreate, request: Request):
    """手開時數（管理員）：直接「可用」，approved_by＝操作者。"""
    payload = check_admin_or_module(request, "hr_leave")
    if body.kind not in CREDIT_KINDS:
        raise HTTPException(status_code=422, detail=f"kind 需為：{'/'.join(CREDIT_KINDS)}")
    if body.hours is None or body.hours <= 0 or round(body.hours * 2) != body.hours * 2:
        raise HTTPException(status_code=422, detail="時數需大於 0，且以 0.5 小時為最小單位")
    granted, expires = as_date(body.granted_on), as_date(body.expires_on)
    if granted is None:
        raise HTTPException(status_code=422, detail="granted_on 需為 YYYY-MM-DD")
    if body.expires_on and expires is None:
        raise HTTPException(status_code=422, detail="expires_on 需為 YYYY-MM-DD")
    if expires is not None and expires < granted:
        raise HTTPException(status_code=422, detail="到期日不可早於生效日")
    source = (body.source or "manual").strip()
    if source not in CREDIT_SOURCES:
        raise HTTPException(status_code=422, detail=f"source 需為：{'/'.join(CREDIT_SOURCES)}")
    factory = db_factory_or_503()
    async with factory() as session:
        staff = await session.get(CrmStaff, body.staff_id)
        if staff is None:
            raise HTTPException(status_code=404, detail="人員不存在")
        obj = HrLeaveCredit(
            id=uuid.uuid4().hex[:12], staff_id=staff.id, staff_name=staff.name,
            kind=body.kind, hours=round(float(body.hours), 2), granted_on=granted, expires_on=expires,
            source=source, reason=(body.reason or "").strip() or None, shoot_id=(body.shoot_id or "").strip() or None,
            status="可用", approved_by=_actor(payload), approved_at=datetime.now(timezone.utc),
            note=(body.note or "").strip() or None, created_by=_actor(payload),
        )
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
        return leave_service.credit_dict(obj, 0.0)


async def _decide_credit(credit_id: str, request: Request, new_status: str, note: str) -> dict:
    payload = check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await session.get(HrLeaveCredit, credit_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到 credit")
        if obj.status != "待審":
            raise HTTPException(status_code=409, detail=f"此筆狀態是「{obj.status}」，只有待審可決定")
        obj.status = new_status
        obj.approved_by = _actor(payload)
        obj.approved_at = datetime.now(timezone.utc)
        if note:
            obj.note = note
        await session.commit()
        await session.refresh(obj)
        return leave_service.credit_dict(obj, 0.0)


@router.post("/credits/{credit_id}/approve")
async def approve_credit(credit_id: str, request: Request):
    """待審 credit（三期加班申請）→ 可用。"""
    return await _decide_credit(credit_id, request, "可用", "")


@router.post("/credits/{credit_id}/reject")
async def reject_credit(credit_id: str, body: LeaveCancel, request: Request):
    return await _decide_credit(credit_id, request, "拒絕", (body.note or "").strip())


@router.delete("/credits/{credit_id}")
async def delete_credit(credit_id: str, request: Request):
    """沒有 allocations 才准刪（被扣過的 credit 刪了餘額會憑空變動）。"""
    check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await session.get(HrLeaveCredit, credit_id)
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到 credit")
        n = (await session.execute(
            select(func.count(HrLeaveAllocation.id)).where(HrLeaveAllocation.credit_id == credit_id))).scalar() or 0
        if n:
            raise HTTPException(status_code=409, detail=f"這筆已被 {n} 張請假單扣過，不能刪（可改狀態為結算）")
        await session.delete(obj)
        await session.commit()
    return {"deleted": credit_id}


# ── 假日表 ──────────────────────────────────────────────────────────────────

def _holiday_dict(h) -> dict:
    return {"date": h.date.isoformat(), "name": h.name or "", "kind": h.kind or "國定假日",
            "weekday": h.date.weekday()}


@router.get("/holidays")
async def list_holidays(request: Request, year: int = 0):
    check_admin_or_module(request, "hr_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        stmt = select(HrHoliday)
        if year:
            stmt = stmt.where(HrHoliday.date >= date(year, 1, 1)).where(HrHoliday.date < date(year + 1, 1, 1))
        rows = (await session.execute(stmt.order_by(HrHoliday.date))).scalars().all()
        return {"items": [_holiday_dict(h) for h in rows], "total": len(rows), "kinds": list(HOLIDAY_KINDS)}


@router.post("/holidays")
async def upsert_holiday(body: HolidayCreate, request: Request):
    """新增或改一天（同日期覆蓋）：颱風假當天公告就是這裡加。"""
    check_admin_or_module(request, "hr_leave")
    d = as_date(body.date)
    if d is None:
        raise HTTPException(status_code=422, detail="date 需為 YYYY-MM-DD")
    kind = (body.kind or "國定假日").strip()
    if kind not in HOLIDAY_KINDS:
        raise HTTPException(status_code=422, detail=f"kind 需為：{'/'.join(HOLIDAY_KINDS)}")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await session.get(HrHoliday, d)
        if obj is None:
            obj = HrHoliday(date=d, name=(body.name or "").strip() or kind, kind=kind)
            session.add(obj)
        else:
            obj.kind = kind
            if body.name is not None:
                obj.name = body.name.strip() or kind
        await session.commit()
        await session.refresh(obj)
        return _holiday_dict(obj)


@router.delete("/holidays/{day}")
async def delete_holiday(day: str, request: Request):
    check_admin_or_module(request, "hr_leave")
    d = as_date(day)
    if d is None:
        raise HTTPException(status_code=422, detail="日期需為 YYYY-MM-DD")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await session.get(HrHoliday, d)
        if obj is None:
            raise HTTPException(status_code=404, detail="假日表沒有這天")
        await session.delete(obj)
        await session.commit()
    return {"deleted": d.isoformat()}


@router.post("/holidays/import")
async def import_holidays(body: HolidayImport, request: Request):
    """貼行政院人事總處年度行事曆 CSV（欄：西元日期／星期／是否放假／備註）→ 國定假日＋補班日 upsert；
    颱風假（手加）不動。回 {imported, skipped, years}。"""
    check_admin_or_module(request, "hr_leave")
    try:
        rows = parse_gov_calendar_csv(body.csv or "")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not rows:
        raise HTTPException(status_code=422, detail="CSV 裡沒有可匯入的假日／補班日（確認欄名：西元日期、是否放假、備註）")
    factory = db_factory_or_503()
    async with factory() as session:
        if body.replace_year:
            y = int(body.replace_year)
            old = (await session.execute(
                select(HrHoliday).where(HrHoliday.date >= date(y, 1, 1)).where(HrHoliday.date < date(y + 1, 1, 1))
                .where(HrHoliday.kind != "颱風假"))).scalars().all()
            for h in old:
                await session.delete(h)
            await session.flush()
        imported = skipped = 0
        for r in rows:
            obj = await session.get(HrHoliday, r["date"])
            if obj is not None and obj.kind == "颱風假":
                skipped += 1
                continue
            if obj is None:
                session.add(HrHoliday(date=r["date"], name=r["name"], kind=r["kind"]))
            else:
                obj.name, obj.kind = r["name"], r["kind"]
            imported += 1
        await session.commit()
    return {"imported": imported, "skipped": skipped, "years": sorted({r["date"].year for r in rows})}


# ── 舊額度線（crm_staff.annual_leave_days；新分頁改看 /balances）───────────────

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
