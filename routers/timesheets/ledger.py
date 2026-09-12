# -*- coding: utf-8 -*-
"""routers/timesheets/ledger.py —— 工作追蹤 API 的一段：總表：一個月每一列（date／from＋to_day／staff_id 切法）、人員清單、批次調整、衝突待決

2026-09-12 從 routers/api_timesheets.py（1,100 行）拆成套件；URL 全部不變（同一顆 router 在 _shared）。
共用守衛／小工具在 ._shared；掃原始碼的測試用 _srcscan.timesheets_src()。
"""
from datetime import timedelta
from fastapi import HTTPException, Query, Request
from sqlalchemy import func as safunc, select
from core.auth import check_admin, check_admin_or_module, current_username, payload_grants
from core.db_guard import db_factory_or_503
from core.hr_logic import WORK_TYPES, month_key, tw_day
from core.schemas import TimesheetConflictResolve, TimesheetRowAdminUpdate, TimesheetRowsBatch
from db.models import CrmStaff, Timesheet
from services.timesheet_conflicts import list_conflicts, resolve_conflict
from services.timesheet_self import admin_batch_update, admin_delete_row, admin_update_row, month_or_422, ts_dict
from ._shared import _CANDS_CACHE, _day_or_422, router


# ── 總表（像發票總表那樣一列一列看；管理員逐列調細節與備註）──────────────────

@router.get("/rows")
async def ledger_rows(request: Request, month: str = "", to: str = "", date: str = "", from_: str = Query("", alias="from"),
                      to_day: str = Query("", alias="to_day"), staff_id: str = ""):
    """month（起）～ to（迄，含；最多 12 個月）的所有列（每人每案每項）。篩選（人／案／關鍵字／來源）
    在前端做。editable 只給管理員（一般成員看得到、改不了）。

    管理視角的「看誰的」（docs/WORK_TRACKING_V2_PLAN.md §4-1／4-4，2026-09-12）多三種切法：`date`（一天）、
    `from`＋`to_day`（一段日期，最多 62 天；`to` 已被月份佔走）、`staff_id`（只看一個人）。給了 date 或 from 就不看 month。
    列的 `editable` 跟整包一樣只給管理員（PUT／DELETE /rows 是 check_admin）—— 格子照這個欄位決定畫成可改還是唯讀。"""
    is_admin = payload_grants(check_admin_or_module(request, "timesheets"))
    m0, m1, label = _rows_window(month, to, date, from_, to_day)
    factory = db_factory_or_503()
    async with factory() as session:
        q = select(Timesheet).where(Timesheet.work_date >= m0).where(Timesheet.work_date < m1)
        if (staff_id or "").strip():
            q = q.where(Timesheet.staff_id == staff_id.strip())
        rows = (await session.execute(q.order_by(Timesheet.work_date.desc(), Timesheet.staff_name, Timesheet.created_at))).scalars().all()
    return {**label, "editable": is_admin,
            "items": [{**ts_dict(r, with_note=is_admin), "editable": is_admin} for r in rows], "work_types": list(WORK_TYPES)}


#: 日期切法的區間上限（管理視角一次看一個人幾週的列；月份那條另有 12 個月的上限）
ROWS_DAY_SPAN_MAX = 62


def _rows_window(month: str, to: str, date: str, from_: str, to_day: str):
    """/rows 的時間窗：給了 date 或 from 走日期（一天／一段），否則走月份（起～迄）。回 (起, 迄+1, 回應裡的標籤欄)。"""
    if (date or "").strip() or (from_ or "").strip():
        d0 = _day_or_422(date or from_)
        d1 = d0 + timedelta(days=1) if (date or "").strip() else _day_or_422(to_day or from_) + timedelta(days=1)
        if d1 <= d0:
            raise HTTPException(status_code=422, detail="迄日不能早於起日")
        if (d1 - d0).days > ROWS_DAY_SPAN_MAX:
            raise HTTPException(status_code=422, detail=f"區間最多 {ROWS_DAY_SPAN_MAX} 天")
        return d0, d1, {"date": d0.date().isoformat(), "to_day": (d1 - timedelta(days=1)).date().isoformat()}
    m0, m1 = month_or_422(month)
    t0 = None
    if to:
        t0, t1 = month_or_422(to)
        if t0 < m0:
            raise HTTPException(status_code=422, detail="迄月不能早於起月")
        if (t0.year - m0.year) * 12 + (t0.month - m0.month) >= 12:
            raise HTTPException(status_code=422, detail="區間最多 12 個月")
        m1 = t1
    return m0, m1, {"month": month_key(m0), "to": month_key(t0) if to else ""}


@router.get("/people")
async def timesheet_people(request: Request):
    """管理視角「看誰的」切換器：在職／合夥／兼職人員（id、name、status），照團隊的一週的順序。
    刻意不重用 /crm/staff（那支要 crm_staff 鑰匙、而且帶薪資欄位）。"""
    check_admin_or_module(request, "timesheets")
    from core.hr_logic import staff_rank, is_active_staff
    factory = db_factory_or_503()
    async with factory() as session:
        staff = (await session.execute(select(CrmStaff.id, CrmStaff.name, CrmStaff.status, CrmStaff.hire_date))).all()
        # 「未填」點名的起點：到職日，沒填就用他第一筆工時的日期（生產 11 個在職沒有一個填 hire_date，
        # 2026-09-12 查過）—— 新人到職前、或還沒開始填的人，不該被標紅。
        first = {sid: d for sid, d in (await session.execute(
            select(Timesheet.staff_id, safunc.min(Timesheet.work_date)).where(Timesheet.staff_id.isnot(None))
            .group_by(Timesheet.staff_id))).all()}
    people = []
    for sid, name, st, hire in staff:
        if not name or not (is_active_staff(st) or (st or "").strip() == "兼職"):
            continue
        since = max(d for d in (tw_day(hire), tw_day(first.get(sid))) if d) if (hire or first.get(sid)) else None
        people.append({"id": sid, "name": name, "status": (st or "").strip(), "since": since.isoformat() if since else None})
    people.sort(key=lambda p: (staff_rank(p["status"]), p["name"]))
    return {"people": people}


@router.post("/rows/batch")
async def ledger_batch(body: TimesheetRowsBatch, request: Request):
    """總表批次調整（管理員）：勾選的列一次改專案／分類／備註／管理員備註。"""
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await admin_batch_update(session, body.ids, body.model_dump(exclude_unset=True, exclude={"ids"}),
                                        current_username(request))


@router.put("/rows/{row_id}")
async def ledger_update_row(row_id: str, body: TimesheetRowAdminUpdate, request: Request):
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await admin_update_row(session, row_id, body, current_username(request))


@router.get("/conflicts")
async def ledger_conflicts(request: Request):
    """待決的衝突（Sheet 與總表改過的同一列內容不同）；管理員。"""
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return {"items": await list_conflicts(session)}


@router.post("/conflicts/{cid}/resolve")
async def ledger_resolve_conflict(cid: str, body: TimesheetConflictResolve, request: Request):
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await resolve_conflict(session, cid, body.choice, current_username(request))


@router.delete("/rows/{row_id}")
async def ledger_delete_row(row_id: str, request: Request):
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await admin_delete_row(session, row_id, current_username(request))
