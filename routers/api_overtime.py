"""api_overtime.py — 加班申請（docs/PAYROLL_OVERTIME_PLAN.md 第二批）：員工端 /api/v1/me/overtime*、管理端 /api/v1/hr/overtime*。

員工端守衛同請假：require_bound_staff(request, 'me_leave')（本人＋綁定人員檔案＋我的請假那把）；
    時數由起訖算、種類由假日表判，客戶端算好的數字一律不收（core.overtime_logic.evaluate）。
管理端：清單＝check_admin_or_module('hr_leave','finance_partner')（同假勤 LEAVE_VIEWERS）；核准／退回＝check_admin。
核准：補休 → hr_leave_credits 一列（source overtime、當年 12/31 到期）；加班費 → 算定金額掛到加班日那個月的薪資單
    （那個月已確認就往後找），草稿存在就當場重算那一列（routers.api_payroll.refresh_staff_line）。
通知：Google Chat 群（notifier overtime_request／overtime_result，無 emoji），best-effort。
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from sqlalchemy import select  # type: ignore

from core.auth import check_admin, check_admin_or_module
from core.db_guard import db_factory_or_503
from core.money import can_see_money
from core.identity import require_bound_staff
from core.leave_logic import as_date
from core.overtime_logic import (ACTIVE_STATUSES, OT_STATUSES, PAYOUTS, evaluate, expires_on_for, month_of, pay_month_for, summarize_month,
                                 vocab)
from core.payroll_logic import hourly_wage, normalize_rates, profile_as_of
from core.schemas import MeOvertimeCreate, MeOvertimePreview, OvertimeReject
from db.models import CrmStaff, HrLeaveCredit, HrOvertimeRequest, PayrollRateTable, PayrollRun, StaffPayProfile
from services import leave_service

me_router = APIRouter(prefix="/api/v1/me/overtime", tags=["me"])
hr_router = APIRouter(prefix="/api/v1/hr/overtime", tags=["hr"])

VIEWERS = ("hr_leave", "finance_partner")
LIST_LIMIT = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _actor(payload) -> str:
    return (payload or {}).get("sub") or ""


def ot_dict(o: HrOvertimeRequest) -> dict:
    return {"id": o.id, "staff_id": o.staff_id, "staff_name": o.staff_name or "",
            "date": o.date.isoformat() if o.date else None, "start_time": o.start_time or "", "end_time": o.end_time or "",
            "hours": float(o.hours or 0), "day_kind": o.day_kind or "工作日", "payout": o.payout or "補休",
            "project_id": o.project_id or "", "project_name": o.project_name or "", "shoot_id": o.shoot_id or "",
            "reason": o.reason or "", "status": o.status or "待審",
            "approved_by": o.approved_by or "", "approved_at": o.approved_at.isoformat() if o.approved_at else None,
            "reject_note": o.reject_note or "", "credit_hours": float(o.credit_hours or 0), "credit_id": o.credit_id or "",
            "pay_month": o.pay_month or "", "pay_amount": int(o.pay_amount) if o.pay_amount is not None else None,
            "payroll_line_id": o.payroll_line_id or "",
            "created_at": o.created_at.isoformat() if o.created_at else None}


async def _hourly_for(session, staff_id: str, month: str):
    """(每小時工資或 None, 費率表)：主檔在該月適用的那一段；沒主檔 → None。"""
    profs = (await session.execute(select(StaffPayProfile).where(StaffPayProfile.staff_id == staff_id))).scalars().all()
    rows = [{"effective_from": p.effective_from, "pay_type": p.pay_type, "base_amount": p.base_amount} for p in profs]
    prof = profile_as_of(rows, month)
    row = (await session.execute(select(PayrollRateTable).where(PayrollRateTable.year == int(month[:4])))).scalar_one_or_none()
    rates = normalize_rates(row.data if row else None, int(month[:4]))
    if not prof:
        return None, rates
    return hourly_wage(prof["pay_type"], prof["base_amount"], rates), rates


async def _others(session, staff_id: str, d, exclude_id: str = ""):
    """同月其他單的時數合計、同一天其他單的 (start, end)、含這個月往前三個月的合計（勞基法 138 小時那條）。"""
    m = month_of(d)
    y, mo = int(m[:4]), int(m[5:])
    d0 = as_date(f"{y}-{mo:02d}-01")
    d1 = as_date(f"{y + 1}-01-01") if mo == 12 else as_date(f"{y}-{mo + 1:02d}-01")
    q0 = as_date(f"{y - 1}-{mo + 10:02d}-01") if mo <= 2 else as_date(f"{y}-{mo - 2:02d}-01")
    rows = (await session.execute(
        select(HrOvertimeRequest).where(HrOvertimeRequest.staff_id == staff_id, HrOvertimeRequest.status.in_(ACTIVE_STATUSES),
                                        HrOvertimeRequest.date >= q0, HrOvertimeRequest.date < d1))).scalars().all()
    rows = [r for r in rows if r.id != exclude_id]
    month_rows = [r for r in rows if r.date >= d0]
    month_hours = sum(float(r.hours or 0) for r in month_rows)
    quarter_hours = sum(float(r.hours or 0) for r in rows)
    same_day = [(r.start_time, r.end_time) for r in month_rows if r.date == as_date(d)]
    return month_hours, same_day, quarter_hours


async def _evaluate(session, staff_id: str, body, exclude_id: str = "") -> dict:
    d = as_date(body.date)
    if d is None:
        raise HTTPException(status_code=422, detail="加班日要像 2026-10-03")
    holidays = await leave_service.holidays_map(session)
    month_hours, same_day, quarter_hours = await _others(session, staff_id, d, exclude_id)
    hourly, rates = await _hourly_for(session, staff_id, month_of(d))
    ev = evaluate(d, body.start_time, body.end_time, body.payout, month_hours=month_hours, quarter_hours=quarter_hours, same_day=same_day,
                  holidays=holidays, hourly=hourly, rates=rates)
    ev["date"] = d.isoformat()
    return ev


async def _notify(template: str, **kw) -> None:
    try:
        from notifier import notify_tab_async
        await notify_tab_async(template, **kw)
    except Exception:
        pass


# ── 員工端 ────────────────────────────────────────────────────────────────

@me_router.get("")
async def my_overtime(request: Request, year: int = 0):
    """{vocab, month:{hours,credit_hours,pay_amount,count}（本月）, items:[今年的單，新的在前]}。"""
    ident = await require_bound_staff(request, "me_leave")
    factory = db_factory_or_503()
    today = datetime.now().date()
    year = year or today.year
    async with factory() as session:
        rows = (await session.execute(
            select(HrOvertimeRequest).where(HrOvertimeRequest.staff_id == ident["staff_id"],
                                            HrOvertimeRequest.date >= as_date(f"{year}-01-01"), HrOvertimeRequest.date < as_date(f"{year + 1}-01-01"))
            .order_by(HrOvertimeRequest.date.desc(), HrOvertimeRequest.start_time.desc()).limit(LIST_LIMIT))).scalars().all()
        items = [ot_dict(r) for r in rows]
        _h, rates = await _hourly_for(session, ident["staff_id"], today.strftime("%Y-%m"))
        has_profile = _h is not None
    return {"vocab": vocab(rates), "year": year, "has_pay_profile": has_profile,
            "month": summarize_month(items, today.strftime("%Y-%m")), "items": items}


@me_router.get("/shoots")
async def my_shoots_on(request: Request, date: str = ""):
    """從場次帶入：那天我在 crew 的場次 [{shoot_id, project_id?, project_name, title}]。"""
    ident = await require_bound_staff(request, "me_leave")
    d = as_date(date)
    if d is None:
        raise HTTPException(status_code=422, detail="日期要像 2026-10-03")
    factory = db_factory_or_503()
    async with factory() as session:
        items = await leave_service.shoot_conflicts_for(session, ident["staff_id"], ident["staff"].name, d, d)
    return {"items": items}


@me_router.post("/preview")
async def preview_my_overtime(body: MeOvertimePreview, request: Request):
    ident = await require_bound_staff(request, "me_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        return await _evaluate(session, ident["staff_id"], body, body.exclude_id or "")


@me_router.post("")
async def apply_my_overtime(body: MeOvertimeCreate, request: Request):
    ident = await require_bound_staff(request, "me_leave")
    if not (body.reason or "").strip():
        raise HTTPException(status_code=422, detail="事由必填（做了什麼、哪個案子）")
    if body.payout not in PAYOUTS:
        raise HTTPException(status_code=422, detail="要選換補休還是加班費")
    factory = db_factory_or_503()
    async with factory() as session:
        ev = await _evaluate(session, ident["staff_id"], body)
        if ev["errors"]:
            raise HTTPException(status_code=422, detail="；".join(e["msg"] for e in ev["errors"]))
        project_name = (body.project_name or "").strip()
        if body.shoot_id and not project_name:
            hits = await leave_service.shoot_conflicts_for(session, ident["staff_id"], ident["staff"].name, as_date(body.date), as_date(body.date))
            hit = next((h for h in hits if h["shoot_id"] == body.shoot_id), None)
            project_name = (hit or {}).get("project_name") or (hit or {}).get("title") or ""
        o = HrOvertimeRequest(id=uuid.uuid4().hex, staff_id=ident["staff_id"], staff_name=ident["staff"].name, date=as_date(body.date),
                              start_time=body.start_time, end_time=body.end_time, hours=ev["hours"], day_kind=ev["day_kind"],
                              payout=body.payout, project_id=body.project_id or None, project_name=project_name or None,
                              shoot_id=body.shoot_id or None, reason=body.reason.strip(), status="待審",
                              credit_hours=ev["credit_hours"] if body.payout == "補休" else 0.0,
                              pay_amount=ev["pay_amount"] if body.payout == "加班費" else None,
                              created_by=ident["username"], created_at=_now(), updated_at=_now())
        session.add(o)
        await session.commit()
        d = ot_dict(o)
    d["warnings"] = ev["warnings"]
    await _notify("overtime_request", staff_name=d["staff_name"], date=d["date"], start=d["start_time"], end=d["end_time"],
                  hours=f"{d['hours']:g}", day_kind=d["day_kind"], payout=d["payout"], reason=d["reason"] or "-")
    return d


@me_router.post("/{ot_id}/cancel")
async def cancel_my_overtime(ot_id: str, request: Request):
    """待審 → 已撤回（已核准的不能自己撤，找管理員）。"""
    ident = await require_bound_staff(request, "me_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        o = (await session.execute(select(HrOvertimeRequest).where(HrOvertimeRequest.id == ot_id, HrOvertimeRequest.staff_id == ident["staff_id"]))).scalar_one_or_none()
        if o is None:
            raise HTTPException(status_code=404, detail="找不到這張加班單（或不是你的）")
        if o.status != "待審":
            raise HTTPException(status_code=409, detail=f"這張單已經是「{o.status}」，不能撤回")
        o.status, o.updated_at = "已撤回", _now()
        await session.commit()
        return ot_dict(o)


# ── 管理端 ────────────────────────────────────────────────────────────────

def redact_pay(items: list, can_see: bool) -> list:
    """沒有金額鑰匙就把 `pay_amount` 整個鍵刪掉（不是歸零 —— 同 core/money.py 抹除層的慣例）。

    🔴 加班費金額回推得出時薪：`pay_amount ÷ 時數 ÷ 倍率` ＝ 每小時工資 × 240 ＝ 月薪。清單看得到的人
    （人事那把 hr_leave、合夥人那把 finance_partner）本來就**不該**看到別人的薪水，合夥人更是絕不給 money_view
    （core/ledger.py 的鐵則）。核准的人是管理員（Lv3），can_see_money 對他一律 True，看得到金額。
    前端兩處（hr_leave.js `_otWhat`）判的是 `pay_amount == null`，鍵不在時是 undefined，正好走同一條「核准時算金額」。
    """
    if can_see:
        return items
    for it in items:
        it.pop("pay_amount", None)
    return items


@hr_router.get("")
async def list_overtime(request: Request, status: str = "", staff_id: str = "", month: str = "", limit: int = LIST_LIMIT):
    """加班單清單（新的在前）＋每張單所屬月的累計小時（畫上限的黃紅 pill）。沒有金額鑰匙的人看不到加班費金額。"""
    check_admin_or_module(request, *VIEWERS)
    factory = db_factory_or_503()
    async with factory() as session:
        q = select(HrOvertimeRequest)
        if status:
            q = q.where(HrOvertimeRequest.status == status)
        if staff_id:
            q = q.where(HrOvertimeRequest.staff_id == staff_id)
        if month:
            y, mo = int(month[:4]), int(month[5:7])
            q = q.where(HrOvertimeRequest.date >= as_date(f"{y}-{mo:02d}-01"),
                        HrOvertimeRequest.date < (as_date(f"{y + 1}-01-01") if mo == 12 else as_date(f"{y}-{mo + 1:02d}-01")))
        rows = (await session.execute(q.order_by(HrOvertimeRequest.date.desc(), HrOvertimeRequest.created_at.desc()).limit(max(1, min(int(limit), 1000))))).scalars().all()
        items = [ot_dict(r) for r in rows]
        totals = {}
        for it in items:
            key = (it["staff_id"], it["date"][:7])
            if key not in totals:
                mh, _sd, _q = await _others(session, it["staff_id"], as_date(it["date"]))
                totals[key] = round(mh, 2)
        for it in items:
            it["month_total"] = totals[(it["staff_id"], it["date"][:7])]
        redact_pay(items, can_see_money(request))
        _h, rates = await _hourly_for(session, "", datetime.now().strftime("%Y-%m"))
    return {"items": items, "vocab": vocab(rates), "statuses": list(OT_STATUSES), "month_cap": vocab(rates)["month_cap"]}


async def _get_pending(session, ot_id: str) -> HrOvertimeRequest:
    o = await session.get(HrOvertimeRequest, ot_id)
    if o is None:
        raise HTTPException(status_code=404, detail="找不到這張加班單")
    if o.status != "待審":
        raise HTTPException(status_code=409, detail=f"這張單已經是「{o.status}」")
    return o


@hr_router.post("/{ot_id}/approve")
async def approve_overtime(ot_id: str, request: Request):
    payload = check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        o = await _get_pending(session, ot_id)
        now = _now()
        detail = ""
        if o.payout == "補休":
            from core.payroll_logic import credit_hours_for
            _h, rates = await _hourly_for(session, o.staff_id, month_of(o.date))
            hours = credit_hours_for(o.hours, o.day_kind, rates)
            c = HrLeaveCredit(id=uuid.uuid4().hex, staff_id=o.staff_id, staff_name=o.staff_name, kind="補休", hours=hours,
                              granted_on=o.date, expires_on=expires_on_for(o.date), source="overtime",
                              reason=f"加班補休 {o.date.isoformat()} {o.start_time}–{o.end_time}" + (f"（{o.project_name}）" if o.project_name else ""),
                              shoot_id=o.shoot_id, status="可用", approved_by=_actor(payload), approved_at=now,
                              created_by=_actor(payload), created_at=now, updated_at=now)
            session.add(c)
            o.credit_hours, o.credit_id = hours, c.id
            detail = f"，補休 +{hours:g} 小時"
        else:
            hourly, rates = await _hourly_for(session, o.staff_id, month_of(o.date))
            if hourly is None:
                raise HTTPException(status_code=409, detail=f"{o.staff_name} 還沒有薪資主檔，算不出加班費；先到人事管理 › 薪資填，或請他改換補休")
            from core.payroll_logic import overtime_pay
            confirmed = [m for (m,) in (await session.execute(select(PayrollRun.month).where(PayrollRun.status == "已確認"))).all()]
            o.pay_month = pay_month_for(o.date, confirmed)
            o.pay_amount = overtime_pay(hourly, o.hours, o.day_kind, rates)
            detail = f"，加班費 {o.pay_amount:,} 元（掛 {o.pay_month} 薪資單）"
        o.status, o.approved_by, o.approved_at, o.updated_at = "已核准", _actor(payload), now, now
        await session.commit()
        if o.payout == "加班費":
            from routers.api_payroll import refresh_staff_line
            await refresh_staff_line(session, o.staff_id, o.pay_month)   # 草稿在就當場把那一列的加班費加上
            await session.commit()
        d = ot_dict(o)
    await _notify("overtime_result", result="核准", staff_name=d["staff_name"], date=d["date"], start=d["start_time"], end=d["end_time"],
                  hours=f"{d['hours']:g}", payout=d["payout"], detail=detail, note="")
    return {"status": "ok", "item": d}


@hr_router.post("/{ot_id}/reject")
async def reject_overtime(ot_id: str, body: OvertimeReject, request: Request):
    payload = check_admin(request)
    if not (body.note or "").strip():
        raise HTTPException(status_code=422, detail="退回要寫理由")
    factory = db_factory_or_503()
    async with factory() as session:
        o = await _get_pending(session, ot_id)
        o.status, o.reject_note, o.approved_by, o.approved_at, o.updated_at = "已退回", body.note.strip(), _actor(payload), _now(), _now()
        await session.commit()
        d = ot_dict(o)
    await _notify("overtime_result", result="退回", staff_name=d["staff_name"], date=d["date"], start=d["start_time"], end=d["end_time"],
                  hours=f"{d['hours']:g}", payout=d["payout"], detail="", note=f"\n說明：{d['reject_note']}")
    return {"status": "ok", "item": d}


async def approved_pay_by_staff(session, month: str) -> dict:
    """{staff_id: 加班費合計} —— 已核准、換加班費、掛在 `month` 的單；給薪資單產生／重算用。"""
    rows = (await session.execute(select(HrOvertimeRequest).where(
        HrOvertimeRequest.status == "已核准", HrOvertimeRequest.payout == "加班費", HrOvertimeRequest.pay_month == month))).scalars().all()
    out: dict = {}
    for r in rows:
        out[r.staff_id] = out.get(r.staff_id, 0) + int(r.pay_amount or 0)
    return out


async def mark_lines(session, month: str, line_ids_by_staff: dict) -> None:
    """薪資單重算後回寫 payroll_line_id（哪一列吃了這筆加班費）。

    🔴 只動 `line_ids_by_staff` 裡有的人：`refresh_staff_line` 核准單筆時只傳**一個人**，
    照掃整個月會把其他人已經接好的連結用 `.get()` 的 None 清掉。
    """
    if not line_ids_by_staff:
        return
    rows = (await session.execute(select(HrOvertimeRequest).where(
        HrOvertimeRequest.status == "已核准", HrOvertimeRequest.payout == "加班費", HrOvertimeRequest.pay_month == month,
        HrOvertimeRequest.staff_id.in_(list(line_ids_by_staff))))).scalars().all()
    for r in rows:
        if r.staff_id in line_ids_by_staff:
            r.payroll_line_id = line_ids_by_staff[r.staff_id]


# main.py 的載入器只認 `router`：兩支合成一支。include_router 是複製當下的路由，所以一定放在所有端點之後（放檔頭會得到一支空的）。
router = APIRouter()
router.include_router(me_router)
router.include_router(hr_router)

__all__ = ["router", "ot_dict", "approved_pay_by_staff", "mark_lines", "CrmStaff"]
