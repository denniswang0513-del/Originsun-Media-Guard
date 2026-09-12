# -*- coding: utf-8 -*-
"""routers/timesheets/mine.py —— 工作追蹤 API 的一段：每日看板／格子字彙／兼職排班（plan-for）／員工自填 own-scope（/mine*）

2026-09-12 從 routers/api_timesheets.py（1,100 行）拆成套件；URL 全部不變（同一顆 router 在 _shared）。
共用守衛／小工具在 ._shared；掃原始碼的測試用 _srcscan.timesheets_src()。
"""
from datetime import datetime, timedelta
from fastapi import HTTPException, Query, Request
from sqlalchemy import func as safunc, select
from core.auth import check_admin_or_module, check_logged_in, payload_grants
from core.db_guard import db_factory_or_503
from core.hr_logic import WORK_TYPES, _TW, midnight_of, parse_ymd, stages_by_category
from core.schemas import MeTimesheetBatch, MeTimesheetUpdate, TimesheetMergeRequest, TimesheetMergeUndo
from db.models import CrmStaff, WorkStageNode
from services.timesheet_self import add_rows, board_days, delete_row, list_rows, search_rows, update_row
from ._shared import _CANDS_CACHE, _day_or_422, _mine_ident, _ts_or_bound, router


# ── 每日看板 ＋ 我的一天（P1）──────────────────────────────────────────────────
#
# 閘門＝timesheets 模組（進得了 tab 就看得到大家每天做了什麼，owner 拍板）；
# 「我的一天」的讀改刪再加「綁定人員檔案」（core.identity.require_bound_staff）。

# 看板計算搬到 services.timesheet_self.board_days（/board 與 /me/team_week 同一份）


@router.get("/board")
async def day_board(request: Request, date: str = "", days: int = 1):
    """每日看板：從 date 起 days 天（1＝當天、7＝週模式），每天每個人做了什麼（實際＋計畫）。
    只顯示有列的人；不排名、不標紅（主管層另做）。"""
    check_admin_or_module(request, "timesheets")
    days = max(1, min(int(days or 1), 14))
    d0 = _day_or_422(date)
    factory = db_factory_or_503()
    async with factory() as session:
        out_days = await board_days(session, d0, days)
        # 管理視角「今天還沒填」（docs/WORK_TRACKING_V2_PLAN.md §4-3）：在職／合夥而且那天沒有任何實際或草稿列的人
        # （只有計畫卡不算填了）。週末不點名。兼職不進來（他們不是每天要填）。
        from core.hr_logic import active_staff_where
        active = [n for (n,) in (await session.execute(select(CrmStaff.name).where(active_staff_where()))).all() if n]
    for day in out_days:
        weekend = datetime.fromisoformat(day["date"]).weekday() >= 5
        filled = {p["name"] for p in day["people"] if any(it.get("status") != "plan" for it in p["items"])}
        day["absent"] = [] if weekend else sorted(n for n in active if n not in filled)
    return {"from": d0.date().isoformat(), "days": days, "items": out_days}


@router.get("/options")
async def timesheet_options(request: Request):
    """格子用的字彙：工作分類 ＋ 每個分類自己的階段（只回 active；編輯器要含停用的走 /crm/work-stages/nodes）。
    守衛放寬到綁定人員（員工頁的專案紀錄格子也要它）。"""
    await _ts_or_bound(request)
    factory = db_factory_or_503()
    async with factory() as session:
        nodes = (await session.execute(select(WorkStageNode))).scalars().all()
    return {"work_types": list(WORK_TYPES), "stages": stages_by_category(nodes)}


# ── 兼職排班（owner 2026-09-08）：有「兼職排班」鑰匙的正職（在職／合夥）幫狀態是「兼職」的人排「我的一週」的卡 ──
# 卡＝他的一列工時（status=plan、時數 0、planned_by＝排的人）。只碰計畫列、時數不收；兼職自己那邊完全不變
# （卡出現在他的格子、他填時數／挪／刪都照舊）。刻意不走 own-scope 的 /mine/*（那邊絕不收 client 給的 staff_id），
# 另開這一組、守衛明講「誰能幫誰」。

_PARTTIME = "兼職"


async def _plan_for_ident(request: Request, session, staff_id: str) -> dict:
    """守衛＋解析：回 {"username": 排的人, "target": CrmStaff, "ident": 給 timesheet_self 用的 own-scope dict}。
    管理員／工作追蹤模組 → 任何人；否則「綁定＋me_plan_parttime＋本人是在職／合夥」且「對方是兼職」。"""
    from core.hr_logic import is_active_staff
    from core.identity import require_bound_staff
    payload = check_logged_in(request)
    who = payload.get("username") or payload.get("sub") or ""
    full = payload_grants(payload, "timesheets")
    target = await session.get(CrmStaff, staff_id)
    if target is None:
        raise HTTPException(status_code=404, detail="找不到這個人員")
    if not full:
        me = await require_bound_staff(request, "me_plan_parttime")
        if not is_active_staff(getattr(me["staff"], "status", None)):
            raise HTTPException(status_code=403, detail="只有在職／合夥可以幫兼職排班")
        if (target.status or "").strip() != _PARTTIME:
            raise HTTPException(status_code=403, detail="只能幫狀態是「兼職」的人排")
    return {"username": who, "target": target, "ident": {"staff_id": target.id, "staff": target, "username": who}}


async def _plan_row_of(session, target, row_id: str):
    """對方的**計畫列**（status=plan）才給改／刪；實際紀錄與別人的列一律 403。"""
    from services.timesheet_self import get_row
    r = await get_row(session, row_id)
    if r.staff_id != target.id or (r.status or "") != "plan":
        raise HTTPException(status_code=403, detail="只能改這個人的計畫列（有時數的紀錄不能動）")
    return r


@router.get("/plan-for/targets")
async def plan_for_targets(request: Request):
    """可以幫誰排：狀態是「兼職」的人員（管理員／工作追蹤模組看全部在職的人也行，但視窗只列兼職）。"""
    from core.identity import require_bound_staff
    from core.hr_logic import is_active_staff
    if not payload_grants(check_logged_in(request), "timesheets"):
        me = await require_bound_staff(request, "me_plan_parttime")
        if not is_active_staff(getattr(me["staff"], "status", None)):   # 跟 _plan_for_ident 同一道：兼職／離職不能列名單
            raise HTTPException(status_code=403, detail="只有在職／合夥可以幫兼職排班")
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmStaff.id, CrmStaff.name).where(CrmStaff.status == _PARTTIME).order_by(CrmStaff.name))).all()
    return {"targets": [{"id": i, "name": n} for i, n in rows]}


@router.get("/plan-for/{staff_id}/rows")
async def plan_for_rows(staff_id: str, request: Request, from_: str = Query("", alias="from"), to: str = ""):
    """對方那一週的列（實際＋計畫都回，視窗只讓動計畫列）。"""
    from services.timesheet_self import list_rows
    d0 = _day_or_422(from_)
    d1 = _day_or_422(to) + timedelta(days=1)
    factory = db_factory_or_503()
    async with factory() as session:
        ctx = await _plan_for_ident(request, session, staff_id)
        items = await list_rows(session, ctx["ident"], d0, d1)
    return {"staff_id": staff_id, "staff_name": ctx["target"].name, "items": items}


@router.post("/plan-for/{staff_id}/rows")
async def plan_for_add(staff_id: str, body: MeTimesheetBatch, request: Request):
    """幫對方加卡：一律計畫列（plan=True、時數不收），planned_by＝排的人；貼一則 Chat（best-effort）。"""
    from db.models import Timesheet
    from services.timesheet_self import add_rows
    factory = db_factory_or_503()
    async with factory() as session:
        ctx = await _plan_for_ident(request, session, staff_id)
        for r in body.rows:
            r.plan = True
            r.hours = None
            r.planned_hours = None
        _CANDS_CACHE["val"] = None
        result = await add_rows(session, ctx["ident"], body.rows)
        for rid in result.get("ids") or []:
            obj = await session.get(Timesheet, rid)
            if obj is not None:
                obj.planned_by = ctx["username"] or None
        await session.commit()
    days = sorted({r.work_date for r in body.rows})
    try:
        from notifier import notify_tab_async
        await notify_tab_async("plan_parttime", planner=ctx["username"] or "?", staff_name=ctx["target"].name,
                               count=len(body.rows), days="、".join(days))
    except Exception:
        pass
    return result


@router.put("/plan-for/{staff_id}/{row_id}")
async def plan_for_update(staff_id: str, row_id: str, body: MeTimesheetUpdate, request: Request):
    """改對方的計畫卡（內容／分類／階段／挪日期）。時數不收 —— 那是他自己在格子裡填的。"""
    from services.timesheet_self import update_row
    factory = db_factory_or_503()
    async with factory() as session:
        ctx = await _plan_for_ident(request, session, staff_id)
        await _plan_row_of(session, ctx["target"], row_id)
        body.hours = None
        body.planned_hours = None
        body.plan = True
        _CANDS_CACHE["val"] = None
        return await update_row(session, ctx["ident"], row_id, body)


@router.delete("/plan-for/{staff_id}/{row_id}")
async def plan_for_delete(staff_id: str, row_id: str, request: Request):
    from services.timesheet_self import delete_row
    factory = db_factory_or_503()
    async with factory() as session:
        ctx = await _plan_for_ident(request, session, staff_id)
        await _plan_row_of(session, ctx["target"], row_id)
        _CANDS_CACHE["val"] = None
        return await delete_row(session, ctx["ident"], row_id)


@router.get("/mine")
async def my_day(request: Request, date: str = ""):
    """我的一天：本人該日的工作項（實際＋計畫）＋ 計畫／實際合計 ＋ 昨天的列（供「複製昨天」）。
    合計兩欄留給工作追蹤分頁；員工頁（/my.html）不畫它們（owner 鐵則：不做個人合計卡）。"""
    ident = await _mine_ident(request)
    d0 = _day_or_422(date)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = await list_rows(session, ident, d0 - timedelta(days=1), d0 + timedelta(days=1))
    day = d0.date().isoformat()
    today_items = [i for i in rows if i["date"] == day]
    return {
        "date": day, "staff_name": ident["staff"].name,
        "items": today_items,
        "planned_total": round(sum(i["planned_hours"] or 0 for i in today_items), 1),
        "actual_total": round(sum(i["hours"] for i in today_items), 1),
        "yesterday": [i for i in rows if i["date"] < day],
        "work_types": list(WORK_TYPES),
    }


@router.get("/mine/incomplete")
async def my_incomplete_days(request: Request, days: int = Query(30, ge=1, le=120)):
    """近 N 天「存了草稿但還沒填時數」的日期（owner 2026-09-07：今天那條提醒「日期 專案紀錄未完成」）。
    路徑要排在 /mine/{row_id} 前面（不然 incomplete 會被當成 row_id）。"""
    from core.hr_logic import PENDING_STATUS
    from db.models import Timesheet
    from services.timesheet_self import own_filter
    from core.hr_logic import tw_day
    ident = await _mine_ident(request)
    d0 = datetime.now(_TW).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet.work_date, safunc.count()).where(own_filter(ident))
            .where(Timesheet.status == PENDING_STATUS).where(Timesheet.work_date >= d0)
            .group_by(Timesheet.work_date).order_by(Timesheet.work_date))).all()
    return {"days": [{"date": tw_day(d).isoformat(), "count": int(n or 0)} for d, n in rows]}   # 台北日期（UTC 午夜存的列 .date() 會少一天）


@router.post("/mine/merge")
async def my_merge_day(body: TimesheetMergeRequest, request: Request):
    """合併同案（owner 2026-09-07）：同案同分類同階段的列併成一列。dry_run 回預覽（哪幾組、各幾列幾小時）；
    正式跑併列＋記合併紀錄（可復原）。規則在 core.hr_logic.merge_plan，I/O 在 services.timesheet_self.merge_day。"""
    from services.timesheet_self import merge_day
    ident = await _mine_ident(request)
    d0 = _day_or_422(body.date)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await merge_day(session, ident, d0, dry_run=body.dry_run)


@router.post("/mine/merge/undo")
async def my_merge_undo(body: TimesheetMergeUndo, request: Request):
    """復原一次合併：被併掉的列照原 id 放回去、本體還原（合併後對它的修改會被蓋掉）。"""
    from services.timesheet_self import undo_merge
    ident = await _mine_ident(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await undo_merge(session, ident, body.log_id)


@router.get("/mine/merge/last")
async def my_last_merge(request: Request, date: str = ""):
    """那一天最近一筆還沒復原的合併（前端用來決定「復原合併」鈕出不出現）。"""
    from services.timesheet_self import last_merge
    ident = await _mine_ident(request)
    d0 = _day_or_422(date)
    factory = db_factory_or_503()
    async with factory() as session:
        return {"last": await last_merge(session, ident, d0)}


@router.get("/mine/rows")
async def my_rows(request: Request, from_: str = Query("", alias="from"), to: str = "", q: str = "",
                  project_id: str = "", stage_id: str = ""):
    """查自己的紀錄（§2-A11）：from／to（YYYY-MM-DD，含；預設近 30 天）、q（內容／備註／案名）、
    project_id、stage_id；**只列不算**，最多 500 列。"""
    ident = await _mine_ident(request)
    for label, raw in (("to", to), ("from", from_)):
        if (raw or "").strip() and parse_ymd(raw) is None:
            raise HTTPException(status_code=422, detail=f"{label} 日期格式錯誤：{raw}")
    to_dt = midnight_of(parse_ymd(to) or datetime.now())
    from_dt = parse_ymd(from_) or (to_dt - timedelta(days=29))
    if from_dt > to_dt:
        raise HTTPException(status_code=422, detail="from 不能晚於 to")
    factory = db_factory_or_503()
    async with factory() as session:
        items = await search_rows(session, ident, from_dt, to_dt + timedelta(days=1),
                                  q=q, project_id=project_id, stage_id=stage_id, limit=500)
    return {"from": from_dt.date().isoformat(), "to": to_dt.date().isoformat(), "items": items}


@router.post("/mine/rows")
async def my_add_rows(body: MeTimesheetBatch, request: Request):
    ident = await _mine_ident(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await add_rows(session, ident, body.rows)


@router.put("/mine/{row_id}")
async def my_update_row(row_id: str, body: MeTimesheetUpdate, request: Request):
    """改自己的一列（含「完成」：把 hours 填上，計畫列就變實際）。"""
    ident = await _mine_ident(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await update_row(session, ident, row_id, body)


@router.delete("/mine/{row_id}")
async def my_delete_row(row_id: str, request: Request):
    ident = await _mine_ident(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await delete_row(session, ident, row_id)
