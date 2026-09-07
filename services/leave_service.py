"""services/leave_service.py — 假勤的 DB 面：員工端（routers/api_me）與管理端（routers/api_hr）共用。

規則全部在 core.leave_logic（純函式、有測試）；這裡只做查詢、組 dict、寫 allocations。
規劃 docs/LEAVE_PLAN.md §7。三個不變式：
  · 餘額不存快照：每次 credits − allocations 重算（balances_for）
  · 「還占著期間」只認 core.leave_logic.ACTIVE_STATUSES（重疊、同期誰休、保留時數三處同一份）
  · 舊列 hours 可能還是 NULL（開機回填前）：SQL 一律 COALESCE(hours, days*8)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException  # type: ignore
from sqlalchemy import func, select  # type: ignore

from core.hr_logic import day_iso, leave_to_dict, midnight_of, parse_ymd, tw_day
from core.leave_logic import (ACTIVE_STATUSES, ALL_LEAVE_TYPES, HOURS_PER_DAY, LEDGER_TYPES,
                              PARTS, SICK_CAP_DAYS, InsufficientHours, allocate, annual_days_for,
                              as_date, balance, cancel_mode, hours_to_days, in_crew, notice_warning,
                              overlaps, working_hours)
from core.shoot_logic import CANCELLED as SHOOT_CANCELLED
from db.models import CrmProject, CrmShoot, HrHoliday, HrLeaveAllocation, HrLeaveCredit, HrLeaveRequest

_HOURS = func.coalesce(HrLeaveRequest.hours, HrLeaveRequest.days * HOURS_PER_DAY)


def _err(code: str, msg: str) -> dict:
    return {"code": code, "msg": msg}


# ── 讀 ─────────────────────────────────────────────────────────────────────

async def holidays_map(session) -> dict:
    """{date: kind} —— core.leave_logic 的 holidays 參數。"""
    rows = (await session.execute(select(HrHoliday.date, HrHoliday.kind))).all()
    return {d: k for d, k in rows}


def credit_dict(c, used: float = 0.0) -> dict:
    used = round(float(used or 0), 2)
    return {
        "id": c.id, "staff_id": c.staff_id, "staff_name": c.staff_name or "",
        "kind": c.kind, "hours": round(float(c.hours or 0), 2), "used": used,
        "remaining": round(float(c.hours or 0) - used, 2),
        "granted_on": c.granted_on.isoformat() if c.granted_on else None,
        "expires_on": c.expires_on.isoformat() if c.expires_on else None,
        "source": c.source or "manual", "reason": c.reason or "", "shoot_id": c.shoot_id or "",
        "status": c.status, "approved_by": c.approved_by or "",
        "approved_at": c.approved_at.isoformat() if c.approved_at else None,
        "note": c.note or "", "created_by": c.created_by or "",
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


async def credits_for(session, staff_ids: list, status: str = "", year: int = 0) -> dict:
    """{staff_id: [credit dict（含 used／remaining）]}；used 從 allocations 聚合，一次查完不 N+1。"""
    if not staff_ids:
        return {}
    used_sq = (select(HrLeaveAllocation.credit_id,
                      func.coalesce(func.sum(HrLeaveAllocation.hours), 0.0).label("used"))
               .group_by(HrLeaveAllocation.credit_id).subquery())
    stmt = (select(HrLeaveCredit, func.coalesce(used_sq.c.used, 0.0))
            .outerjoin(used_sq, used_sq.c.credit_id == HrLeaveCredit.id)
            .where(HrLeaveCredit.staff_id.in_(list(staff_ids))))
    if status:
        stmt = stmt.where(HrLeaveCredit.status == status)
    if year:
        stmt = stmt.where(HrLeaveCredit.granted_on >= date(year, 1, 1)).where(HrLeaveCredit.granted_on < date(year + 1, 1, 1))
    rows = (await session.execute(
        stmt.order_by(HrLeaveCredit.expires_on.nulls_last(), HrLeaveCredit.granted_on, HrLeaveCredit.created_at))).all()
    out: dict = {sid: [] for sid in staff_ids}
    for c, used in rows:
        out.setdefault(c.staff_id, []).append(credit_dict(c, used))
    return out


async def pending_hours_for(session, staff_ids: list, exclude_id: str = "") -> dict:
    """{staff_id: {假別: 待審小時}} —— 只算走時數帳的假別（保留時數）。"""
    if not staff_ids:
        return {}
    stmt = (select(HrLeaveRequest.staff_id, HrLeaveRequest.leave_type, func.coalesce(func.sum(_HOURS), 0.0))
            .where(HrLeaveRequest.staff_id.in_(list(staff_ids)))
            .where(HrLeaveRequest.status == "待審")
            .where(HrLeaveRequest.leave_type.in_(LEDGER_TYPES)))
    if exclude_id:
        stmt = stmt.where(HrLeaveRequest.id != exclude_id)
    out: dict = {}
    for sid, lt, h in (await session.execute(stmt.group_by(HrLeaveRequest.staff_id, HrLeaveRequest.leave_type))).all():
        out.setdefault(sid, {})[lt] = round(float(h or 0), 2)
    return out


async def sick_used_for(session, staff_ids: list, year: int, exclude_id: str = "") -> dict:
    """{staff_id: 當年病假天數}（待審／已核准／消假待審都算 —— 上限提醒要含已送出的）。"""
    if not staff_ids:
        return {}
    stmt = (select(HrLeaveRequest.staff_id, func.coalesce(func.sum(_HOURS), 0.0))
            .where(HrLeaveRequest.staff_id.in_(list(staff_ids)))
            .where(HrLeaveRequest.leave_type == "病假")
            .where(HrLeaveRequest.status.in_(ACTIVE_STATUSES))
            .where(HrLeaveRequest.start_date >= datetime(year, 1, 1))
            .where(HrLeaveRequest.start_date < datetime(year + 1, 1, 1)))
    if exclude_id:
        stmt = stmt.where(HrLeaveRequest.id != exclude_id)
    return {sid: hours_to_days(h) for sid, h in (await session.execute(stmt.group_by(HrLeaveRequest.staff_id))).all()}


async def balances_for(session, staff_ids: list, on: date, exclude_id: str = "") -> dict:
    """{staff_id: {特休: balance, 補休: balance}}（core.leave_logic.balance 的形狀：available／reserved／expiring）。"""
    credits = await credits_for(session, staff_ids)
    pending = await pending_hours_for(session, staff_ids, exclude_id)
    out = {}
    for sid in staff_ids:
        out[sid] = {kind: balance(credits.get(sid, []), pending_hours=pending.get(sid, {}).get(kind, 0.0), kind=kind, on=on)
                    for kind in LEDGER_TYPES}
    return out


async def active_between(session, start: date, end: date, staff_id: str = "", exclude_id: str = "") -> list:
    """[start, end] 期間還占著位子的申請單（待審／已核准／消假待審），可限一人、可排除自己。"""
    stmt = (select(HrLeaveRequest)
            .where(HrLeaveRequest.status.in_(ACTIVE_STATUSES))
            .where(HrLeaveRequest.start_date < midnight_of(end) + timedelta(days=1))
            .where(HrLeaveRequest.end_date >= midnight_of(start)))
    if staff_id:
        stmt = stmt.where(HrLeaveRequest.staff_id == staff_id)
    if exclude_id:
        stmt = stmt.where(HrLeaveRequest.id != exclude_id)
    rows = (await session.execute(stmt.order_by(HrLeaveRequest.start_date))).scalars().all()
    # SQL 那層是 timestamptz 粗篩；台北日期精算再過一次（同一列寫 naive 讀 aware 的老坑）
    return [r for r in rows if overlaps(r.start_date, r.end_date, start, end)]


async def shoot_conflicts_for(session, staff_id: str, staff_name: str, start: date, end: date) -> list:
    """期間內我在 crew 的場次：[{shoot_id, date, end_date, project_name, title}]。"""
    from routers.api_shoots import _crew_list   # 場次 crew JSON 的唯一解析器（同 services.media_log_catchup 的懶 import 慣例）
    rows = (await session.execute(
        select(CrmShoot, CrmProject.name)
        .outerjoin(CrmProject, CrmProject.id == CrmShoot.project_id)
        .where(CrmShoot.date <= end)
        .where(func.coalesce(CrmShoot.end_date, CrmShoot.date) >= start)
        .where(CrmShoot.status != SHOOT_CANCELLED)
        .order_by(CrmShoot.date))).all()
    out = []
    for s, pname in rows:
        if in_crew(_crew_list(s.crew), staff_id, staff_name):
            out.append({"shoot_id": s.id, "date": s.date.isoformat(),
                        "end_date": (s.end_date or s.date).isoformat(),
                        "project_name": pname or "", "title": (s.title or "").strip() or (pname or "")})
    return out


def request_dict(o, holidays=None, today: date | None = None) -> dict:
    """leave_to_dict ＋ cancel_mode（已核准才有：free／apply／locked，員工端據此畫「撤回」或「申請消假」）。"""
    d = leave_to_dict(o)
    d["days"] = hours_to_days(d["hours"])
    d["cancel_mode"] = cancel_mode(o.start_date, today or date.today(), holidays) if o.status == "已核准" else None
    return d


# ── 送單前的計算（preview 與 create 同一份）─────────────────────────────────

def hours_from_body(body, holidays) -> tuple:
    """(hours, part) —— hours 明給就用它；part／時段有給就算；都沒有（舊分頁 {days}）才 days×8。
    規則錯 raise ValueError（呼叫端轉 bad_range）。"""
    part = (getattr(body, "part", None) or "").strip() or "all"
    if part not in PARTS:
        raise ValueError(f"part 需為：{'/'.join(PARTS)}")
    hours = getattr(body, "hours", None)
    if hours is None and part == "all" and not getattr(body, "start_time", None) and getattr(body, "days", None) is not None:
        hours = float(body.days) * HOURS_PER_DAY
    if hours is None:
        hours = working_hours(body.start_date, body.end_date, part,
                              getattr(body, "start_time", None), getattr(body, "end_time", None), holidays)
    hours = float(hours)
    if hours <= 0:
        raise ValueError("期間內沒有工作日（週末／假日不用請假）")
    if round(hours * 2) != hours * 2:
        raise ValueError("時數以 0.5 小時為最小單位")
    return round(hours, 2), part


async def evaluate(session, staff_id: str, staff_name: str, body, today: date | None = None,
                   exclude_id: str = "", holidays=None) -> dict:
    """{hours, days, errors:[{code,msg}], warnings:[{code,msg}], balance}。
    errors：bad_type／bad_range／overlap／insufficient；warnings：notice_short／shoot_conflict／sick_cap。"""
    today = today or date.today()
    holidays = holidays if holidays is not None else await holidays_map(session)
    errors, warnings = [], []
    leave_type = (body.leave_type or "").strip()
    if leave_type not in ALL_LEAVE_TYPES:
        errors.append(_err("bad_type", f"假別需為：{'/'.join(ALL_LEAVE_TYPES)}"))
    start, end = as_date(body.start_date), as_date(body.end_date)
    if not start or not end:
        errors.append(_err("bad_range", "起訖日期必填（YYYY-MM-DD）"))
        return {"hours": 0, "days": 0, "errors": errors, "warnings": warnings, "balance": None}
    if end < start:
        errors.append(_err("bad_range", "迄日不可早於起日"))
        return {"hours": 0, "days": 0, "errors": errors, "warnings": warnings, "balance": None}
    hours = 0.0
    try:
        hours, _part = hours_from_body(body, holidays)
    except ValueError as e:
        errors.append(_err("bad_range", str(e)))
    if errors:
        return {"hours": hours, "days": hours_to_days(hours), "errors": errors, "warnings": warnings, "balance": None}

    clash = await active_between(session, start, end, staff_id=staff_id, exclude_id=exclude_id)
    if clash:
        c = clash[0]
        errors.append(_err("overlap", f"與你 {day_iso(c.start_date)}～{day_iso(c.end_date)} 的{c.leave_type}（{c.status}）重疊"))

    bal = None
    if leave_type in LEDGER_TYPES:
        bal = (await balances_for(session, [staff_id], today, exclude_id))[staff_id][leave_type]
        free = round(bal["available"] - bal["reserved"], 2)
        if free < hours:
            errors.append(_err("insufficient", f"{leave_type}不足：可用 {free:g} 小時（含待審保留 {bal['reserved']:g}），差 {hours - free:g} 小時"))

    nw = notice_warning(start, today)
    if nw:
        warnings.append(_err("notice_short", nw))
    for s in await shoot_conflicts_for(session, staff_id, staff_name, start, end):
        warnings.append(_err("shoot_conflict", f"{s['date']} 你在「{s['title']}」拍攝名單"))
    if leave_type == "病假":
        used = (await sick_used_for(session, [staff_id], start.year, exclude_id)).get(staff_id, 0.0)
        if used + hours_to_days(hours) > SICK_CAP_DAYS:
            warnings.append(_err("sick_cap", f"今年病假已用 {used:g} 天，這次後共 {used + hours_to_days(hours):g} 天，超過 {SICK_CAP_DAYS} 天上限（需證明／半薪規則）"))
    return {"hours": hours, "days": hours_to_days(hours), "errors": errors, "warnings": warnings, "balance": bal}


def build_request(staff_id: str, staff_name: str, body, hours: float, part: str, created_by: str) -> HrLeaveRequest:
    """evaluate 沒有 errors 之後才呼叫：組待審單（days 是 hours/8 鏡射）。"""
    st = (getattr(body, "start_time", None) or "").strip() or None
    et = (getattr(body, "end_time", None) or "").strip() or None
    return HrLeaveRequest(
        id=uuid.uuid4().hex[:12], staff_id=staff_id, staff_name=staff_name,
        leave_type=body.leave_type.strip(), start_date=parse_ymd(body.start_date), end_date=parse_ymd(body.end_date),
        hours=hours, days=hours_to_days(hours), part=part,
        start_time=st if part == "range" else None, end_time=et if part == "range" else None,
        reason=(body.reason or "").strip() or None, status="待審", created_by=created_by,
    )


# ── 核准／釋放 ──────────────────────────────────────────────────────────────

async def approve_request(session, obj: HrLeaveRequest, actor: str, today: date | None = None) -> list:
    """待審 → 已核准：走時數帳的先 FIFO 分配寫 hr_leave_allocations（不足 422），再寫三欄。回 [(credit_id, hours)]。"""
    today = today or date.today()
    if obj.status != "待審":
        raise HTTPException(status_code=409, detail=f"此單狀態是「{obj.status}」，只有待審可核准")
    hours = float(obj.hours if obj.hours is not None else (obj.days or 0) * HOURS_PER_DAY)
    parts = []
    if obj.leave_type in LEDGER_TYPES:
        credits = [c for c in (await credits_for(session, [obj.staff_id])).get(obj.staff_id, []) if c["kind"] == obj.leave_type]
        try:
            parts = allocate(credits, hours, on=today)
        except InsufficientHours as e:
            raise HTTPException(status_code=422, detail=f"{obj.leave_type}時數不足，差 {e.short:g} 小時（先到「時數帳」補 credit）")
        for cid, h in parts:
            session.add(HrLeaveAllocation(id=uuid.uuid4().hex[:12], request_id=obj.id, credit_id=cid, hours=h))
    obj.status = "已核准"
    obj.approved_by = actor
    obj.approved_at = datetime.now(timezone.utc)
    obj.reject_note = None
    return parts


async def release_allocations(session, request_id: str) -> int:
    """撤回／退回／刪單：把這張單吃掉的 credit 全部放回去。"""
    rows = (await session.execute(select(HrLeaveAllocation).where(HrLeaveAllocation.request_id == request_id))).scalars().all()
    for a in rows:
        await session.delete(a)
    return len(rows)


async def staff_leave_summary(session, staff, today: date | None = None, limit: int = 20) -> dict:
    """員工端 summary 與管理端 balances 共用的一人份：餘額、病假、最近的單、待審數、法定特休。"""
    today = today or date.today()
    holidays = await holidays_map(session)
    bal = (await balances_for(session, [staff.id], today))[staff.id]
    sick = (await sick_used_for(session, [staff.id], today.year)).get(staff.id, 0.0)
    rows = (await session.execute(
        select(HrLeaveRequest).where(HrLeaveRequest.staff_id == staff.id)
        .order_by(HrLeaveRequest.start_date.desc().nulls_last(), HrLeaveRequest.created_at.desc()).limit(limit))).scalars().all()
    pending = (await session.execute(
        select(func.count(HrLeaveRequest.id)).where(HrLeaveRequest.staff_id == staff.id)
        .where(HrLeaveRequest.status.in_(("待審", "消假待審"))))).scalar() or 0
    return {
        "staff_id": staff.id, "staff_name": staff.name, "today": today.isoformat(),
        "balances": bal,
        "sick": {"used_days": sick, "cap_days": SICK_CAP_DAYS},
        "requests": [request_dict(r, holidays, today) for r in rows],
        "pending_count": int(pending),
        "hire_date": day_iso(getattr(staff, "hire_date", None)) or "",
        "annual_days_by_law": annual_days_for(tw_day(getattr(staff, "hire_date", None)), today),
    }
