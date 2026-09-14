"""routers/api_calendar.py — 公布欄「行事曆」（docs/CALENDAR_PLAN.md §4.2）。

統一事件流：一趟撈五種（拍攝場次／工作登記／已核准的假／假日／里程碑，scope=me 多帶「我的一週」計畫卡）。
可登記的只有「工作登記」`crm_schedule`（其餘在原本的地方改，這裡唯讀）。寫入後同步 Google（services.calendar_sync）。
守衛：讀＝公布欄／專案管理／工作追蹤／「今天與這週」任一把；登記自己＝綁定人員檔案；排別人或外部人員＝crm_projects；
顏色設定＝admin。私帳案對看不到私帳的人只給案名不給 id（同團隊的一週）。
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from core.auth import ME_ZONE_MASTER, check_admin, check_admin_or_module, payload_grants
from core.db_guard import db_factory_or_503
from core.hr_logic import day_iso, midnight_of, tw_day
from core.identity import require_bound_staff, resolve_current_staff
from core.schedule_logic import (CANCELLED, COLOR_KINDS, COLOR_KIND_LABELS, DONE, GOOGLE_COLORS, KIND_LABELS, KINDS,
                                 PLANNED, SLOT_LABELS, SLOTS, STATUSES, attendee_norm, color_hex, color_map, conflicts,
                                 is_attendee, slot_times, timesheet_row_for_done)
from core.schemas import CalendarColorsPayload, SchedulePayload, ScheduleStatusPayload, ScheduleDonePayload
from services import calendar_sync
from services.calendar_sync import attendees_of, schedule_dict, timesheet_ids_of

try:
    from sqlalchemy import or_, select
    from db.models import (Client, CrmProject, CrmProjectMilestone, CrmSchedule, CrmShoot, CrmStaff, HrHoliday,
                           HrLeaveRequest, Timesheet)
except ImportError:  # 機隊 agent 沒裝 DB 套件
    pass

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])

READ_KEYS = ("bulletin", "crm_projects", "timesheets", ME_ZONE_MASTER, "me_team_week")
WRITE_OTHERS_KEY = "crm_projects"


def _reader(request: Request) -> dict:
    return check_admin_or_module(request, *READ_KEYS) or {}


def _parse_day(raw, field: str, required: bool = False):
    raw = (raw or "").strip()
    if not raw:
        if required:
            raise HTTPException(status_code=422, detail=f"{field} 必填")
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} 格式須為 YYYY-MM-DD")


def _hide_mine(request: Request) -> bool:
    try:
        from core.ledger import hide_mine_projects
        return bool(hide_mine_projects(request))
    except Exception:       # noqa: BLE001 — 沒有 ledger 模組的環境：一律隱藏
        return True


async def _projects(session, ids) -> dict:
    ids = [i for i in set(ids or ()) if i]
    if not ids:
        return {}
    rows = (await session.execute(
        select(CrmProject.id, CrmProject.name, CrmProject.entity, Client.short_name)
        .outerjoin(Client, Client.id == CrmProject.client_id).where(CrmProject.id.in_(ids)))).all()
    return {i: {"name": n or "", "entity": e or "parent", "client": c or ""} for i, n, e, c in rows}


def _pid_for(info: dict, pid: str, hide: bool) -> str:
    return "" if (hide and info.get("entity") == "mine") else (pid or "")


# ── 事件流 ──────────────────────────────────────────────

async def _shoot_events(session, d0, d1, me, hide) -> list:
    from routers.api_shoots import _rows_for, _visible_shoots
    rows = (await session.execute(
        _visible_shoots().where(CrmShoot.date <= d1, or_(CrmShoot.end_date >= d0, CrmShoot.date >= d0))
        .order_by(CrmShoot.date, CrmShoot.start_time))).scalars().all()
    out = []
    for r in await _rows_for(session, list(rows)):
        people = [{"name": c["name"], "staff_id": c.get("staff_id", ""), "role": "", "external": not c.get("staff_id")} for c in r["crew"]]
        out.append({"kind": "shoot", "id": r["id"], "date": r["date"], "end_date": r["end_date"],
                    "start_time": r["start_time"], "end_time": r["end_time"],
                    "title": r["title"] or r["project_name"], "project_id": r["project_id"], "project_name": r["project_name"],
                    "client": r["client_short_name"], "people": people, "location": r["location_name"] or r["location_text"],
                    "equipment": [e["name"] for e in r["equipment"]], "notes": r["notes"], "status": r["status"],
                    "mine": is_attendee(people, me["staff_id"], me["name"]),
                    "sync": {"ok": bool(r["synced_at"]) and not r["sync_error"], "error": r["sync_error"]}})
    return out


async def _schedule_rows(session, d0, d1, project_id: str = "") -> list:
    q = select(CrmSchedule).where(CrmSchedule.date <= d1, or_(CrmSchedule.end_date >= d0, CrmSchedule.date >= d0))
    if project_id:
        q = q.where(CrmSchedule.project_id == project_id)
    return (await session.execute(q.order_by(CrmSchedule.date, CrmSchedule.start_time))).scalars().all()


def _schedule_event(d: dict, info: dict, me, hide) -> dict:
    return {"kind": "schedule", "id": d["id"], "sub_kind": d["kind"], "date": d["date"], "end_date": d["end_date"],
            "start_time": d["start_time"], "end_time": d["end_time"], "title": d["title"],
            "project_id": _pid_for(info, d["project_id"], hide), "project_name": info.get("name", ""), "client": info.get("client", ""),
            "people": d["attendees"], "location": d["location_text"], "notes": d["notes"], "status": d["status"],
            "timesheet_ids": d["timesheet_ids"], "created_by": d["created_by"],
            "mine": is_attendee(d["attendees"], me["staff_id"], me["name"]),
            "sync": {"ok": bool(d["synced_at"]) and not d["sync_error"], "error": d["sync_error"]}}


async def _leave_events(session, d0, d1, me) -> list:
    rows = (await session.execute(
        select(HrLeaveRequest).where(HrLeaveRequest.status == "已核准",
                                     HrLeaveRequest.start_date < midnight_of(d1 + timedelta(days=1)),
                                     HrLeaveRequest.end_date >= midnight_of(d0)))).scalars().all()
    out = []
    for l in rows:
        part = l.part or "all"
        st, et = ("", "")
        if part in ("am", "pm"):
            st, et = SLOTS[part]
        elif part == "range":
            st, et = l.start_time or "", l.end_time or ""
        out.append({"kind": "leave", "id": l.id, "date": day_iso(l.start_date), "end_date": day_iso(l.end_date),
                    "start_time": st, "end_time": et, "title": f"{l.staff_name or ''} {l.leave_type or '請假'}" + ("（半天）" if part in ("am", "pm") else ""),
                    "project_id": "", "project_name": "", "client": "", "people": [{"name": l.staff_name or "", "staff_id": l.staff_id or "", "role": "", "external": False}],
                    "location": "", "notes": "", "status": l.status, "mine": l.staff_id == me["staff_id"],
                    "sync": {"ok": bool(l.synced_at) and not l.sync_error, "error": l.sync_error or ""}})
    return out


async def _holiday_events(session, d0, d1) -> list:
    rows = (await session.execute(select(HrHoliday).where(HrHoliday.date >= d0, HrHoliday.date <= d1))).scalars().all()
    return [{"kind": "holiday", "id": h.date.isoformat(), "date": h.date.isoformat(), "end_date": "", "start_time": "", "end_time": "",
             "title": h.name or h.kind or "假日", "holiday_kind": h.kind or "", "project_id": "", "project_name": "", "client": "",
             "people": [], "location": "", "notes": "", "status": "", "mine": False, "sync": {"ok": True, "error": ""}} for h in rows]


async def _milestone_events(session, d0, d1, me, hide, project_id: str = "") -> list:
    q = select(CrmProjectMilestone).where(CrmProjectMilestone.due_date >= d0, CrmProjectMilestone.due_date <= d1)
    if project_id:
        q = q.where(CrmProjectMilestone.project_id == project_id)
    rows = (await session.execute(q.order_by(CrmProjectMilestone.due_date))).scalars().all()
    info = await _projects(session, [m.project_id for m in rows])
    out = []
    for m in rows:
        pi = info.get(m.project_id or "", {})
        out.append({"kind": "milestone", "id": m.id, "date": m.due_date.isoformat(), "end_date": "", "start_time": "", "end_time": "",
                    "title": m.title or "", "project_id": _pid_for(pi, m.project_id, hide), "project_name": pi.get("name", ""),
                    "client": pi.get("client", ""),
                    "people": [{"name": m.assignee_name, "staff_id": m.assignee_staff_id or "", "role": "", "external": False}] if m.assignee_name else [],
                    "location": "", "notes": m.note or "", "status": m.status or "open",
                    "mine": bool(me["staff_id"]) and m.assignee_staff_id == me["staff_id"] or (bool(me["name"]) and m.assignee_name == me["name"]),
                    "sync": {"ok": bool(m.synced_at) and not m.sync_error, "error": m.sync_error or ""}})
    return out


async def _plan_events(session, d0, d1, me, hide, exclude_ids: set) -> list:
    """我的一週計畫卡（timesheets status=plan）；已經被工作登記代表的（plan_row_id）不重複畫。"""
    if not me["staff_id"] and not me["name"]:
        return []
    from services.timesheet_self import own_filter
    ident = {"staff_id": me["staff_id"], "staff": type("S", (), {"name": me["name"]})()}
    rows = (await session.execute(
        select(Timesheet).where(own_filter(ident), Timesheet.status == "plan",
                                Timesheet.work_date >= midnight_of(d0), Timesheet.work_date < midnight_of(d1 + timedelta(days=1))))).scalars().all()
    info = await _projects(session, [r.project_id for r in rows if r.project_id])
    out = []
    for r in rows:
        if r.id in exclude_ids:
            continue
        pi = info.get(r.project_id or "", {})
        out.append({"kind": "plan", "id": r.id, "date": day_iso(r.work_date) or "", "end_date": "", "start_time": r.start_time or "",
                    "end_time": r.end_time or "", "title": r.task_note or r.project_name or "（計畫）",
                    "project_id": _pid_for(pi, r.project_id or "", hide), "project_name": pi.get("name") or r.project_name or "",
                    "client": pi.get("client", ""), "people": [{"name": me["name"], "staff_id": me["staff_id"], "role": "", "external": False}],
                    "location": "", "notes": "", "status": "plan", "mine": True, "sync": {"ok": True, "error": ""}})
    return out


async def _me(request: Request) -> dict:
    ident = await resolve_current_staff(request)
    st = ident.get("staff")
    return {"username": ident.get("username") or "", "staff_id": ident.get("staff_id") or "", "name": (st.name if st is not None else "") or ""}


@router.get("/events")
async def calendar_events(request: Request, from_: str = Query("", alias="from"), to: str = Query(""), scope: str = Query("all")):
    """統一事件流。scope＝all（全公司）／me（只跟我有關）／project:<id>（一案）。from／to 含首尾，預設本月。"""
    payload = _reader(request)
    today = date.today()
    d0 = _parse_day(from_, "from") or today.replace(day=1)
    d1 = _parse_day(to, "to") or (d0 + timedelta(days=42))
    if d1 < d0:
        raise HTTPException(status_code=422, detail="to 不能早於 from")
    if (d1 - d0).days > 200:
        raise HTTPException(status_code=422, detail="一次最多 200 天")
    scope = (scope or "all").strip()
    project_id = scope[8:] if scope.startswith("project:") else ""
    me = await _me(request)
    hide = _hide_mine(request)
    factory = db_factory_or_503()
    async with factory() as session:
        sch = await _schedule_rows(session, d0, d1, project_id)
        info = await _projects(session, [s.project_id for s in sch])
        events = [_schedule_event(schedule_dict(s, info.get(s.project_id or "", {}).get("name", ""),
                                                info.get(s.project_id or "", {}).get("client", "")), info.get(s.project_id or "", {}), me, hide)
                  for s in sch]
        shoots = await _shoot_events(session, d0, d1, me, hide)
        if project_id:
            shoots = [x for x in shoots if x["project_id"] == project_id]
        events += shoots
        events += await _milestone_events(session, d0, d1, me, hide, project_id)
        if not project_id:
            events += await _leave_events(session, d0, d1, me)
            events += await _holiday_events(session, d0, d1)
        if scope == "me":
            events += await _plan_events(session, d0, d1, me, hide, {s.plan_row_id for s in sch if s.plan_row_id})
    if scope == "me":
        events = [e for e in events if e["mine"] or e["kind"] == "holiday"]
    events.sort(key=lambda e: (e["date"], e["start_time"] or "", e["kind"]))
    return {"from": d0.isoformat(), "to": d1.isoformat(), "scope": scope, "events": events,
            "me": {"staff_id": me["staff_id"], "name": me["name"], "can_assign": payload_grants(payload, WRITE_OTHERS_KEY),
                   "bound": bool(me["staff_id"]), "is_admin": payload_grants(payload)},
            "colors": _colors_payload()}


def _colors_payload() -> dict:
    colors = calendar_sync.colors_from_settings()
    return {"map": colors, "hex": {k: color_hex(colors, k) for k in COLOR_KINDS},
            "palette": [{"id": k, "name": v[0], "hex": v[1]} for k, v in GOOGLE_COLORS.items()],
            "labels": COLOR_KIND_LABELS}


@router.get("/options")
async def calendar_options(request: Request):
    """表單字彙：種類／狀態／時段、人員（在職／合夥／兼職）、進行中專案、最近 90 天用過的外部人員、職務建議、顏色、日曆有沒有接。"""
    payload = _reader(request)
    hide = _hide_mine(request)
    factory = db_factory_or_503()
    async with factory() as session:
        staff = (await session.execute(
            select(CrmStaff.id, CrmStaff.name, CrmStaff.role, CrmStaff.status).order_by(CrmStaff.name))).all()
        q = select(CrmProject.id, CrmProject.name, CrmProject.entity, Client.short_name).outerjoin(Client, Client.id == CrmProject.client_id) \
            .where(CrmProject.status.notin_(["歸檔", "未成案"])).order_by(CrmProject.updated_at.desc()).limit(300)
        projects = (await session.execute(q)).all()
        recent = (await session.execute(
            select(CrmSchedule.attendees).where(CrmSchedule.date >= date.today() - timedelta(days=90)))).scalars().all()
    externals, roles = {}, {}
    for raw in recent:
        try:
            lst = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except ValueError:
            lst = []
        for a in attendee_norm(lst):
            if a["external"]:
                externals[a["name"]] = a["contact"] or externals.get(a["name"], "")
            if a["role"]:
                roles[a["role"]] = roles.get(a["role"], 0) + 1
    sa, cal_id, _err = await calendar_sync.gc.load_config()
    return {
        "kinds": [{"id": k, "label": KIND_LABELS[k]} for k in KINDS], "statuses": list(STATUSES),
        "slots": [{"id": k, "label": SLOT_LABELS[k], "times": list(SLOTS.get(k, ("", "")))} for k in ("all", "am", "pm", "custom")],
        "staff": [{"id": i, "name": n or "", "role": r or "", "status": st or "在職"} for i, n, r, st in staff if (st or "在職") != "離職"],
        "projects": [{"id": ("" if (hide and e == "mine") else i), "name": n or "", "client": c or ""} for i, n, e, c in projects
                     if not (hide and e == "mine")],
        "externals": [{"name": n, "contact": c} for n, c in sorted(externals.items())],
        "roles": [r for r, _n in sorted(roles.items(), key=lambda kv: -kv[1])] or ["攝影", "燈光", "收音", "剪接", "導演", "製片", "助理"],
        "colors": _colors_payload(),
        "calendar_configured": bool(sa and cal_id),
        "me": {"can_assign": payload_grants(payload, WRITE_OTHERS_KEY), "is_admin": payload_grants(payload)},
    }


@router.get("/conflicts")
async def calendar_conflicts(request: Request, date_: str = Query("", alias="date"), end_date: str = Query(""),
                             attendees: str = Query("[]"), exclude: str = Query("")):
    """存之前問一次：這幾個人那幾天有沒有已核准的假／別場拍攝／已排工作。只提醒不擋。"""
    _reader(request)
    d0 = _parse_day(date_, "date", required=True)
    d1 = _parse_day(end_date, "end_date") or d0
    try:
        att = attendee_norm(json.loads(attendees or "[]"))
    except ValueError:
        raise HTTPException(status_code=422, detail="attendees 需為 JSON 陣列")
    if not att:
        return {"conflicts": []}
    return {"conflicts": await _conflicts_for(att, d0, d1, exclude)}


async def _conflicts_for(att: list, d0, d1, exclude: str = "") -> list:
    factory = db_factory_or_503()
    async with factory() as session:
        leaves = [{"staff_id": l.staff_id, "staff_name": l.staff_name, "start_date": tw_day(l.start_date), "end_date": tw_day(l.end_date),
                   "leave_type": l.leave_type} for l in (await session.execute(
                       select(HrLeaveRequest).where(HrLeaveRequest.status == "已核准",
                                                    HrLeaveRequest.start_date < midnight_of(d1 + timedelta(days=1)),
                                                    HrLeaveRequest.end_date >= midnight_of(d0)))).scalars().all()]
        shoots = []
        for s, pname in (await session.execute(
                select(CrmShoot, CrmProject.name).join(CrmProject, CrmProject.id == CrmShoot.project_id)
                .where(CrmShoot.date <= d1, or_(CrmShoot.end_date >= d0, CrmShoot.date >= d0)))).all():
            try:
                crew = json.loads(s.crew) if isinstance(s.crew, str) else (s.crew or [])
            except ValueError:
                crew = []
            shoots.append({"id": s.id, "title": s.title or pname or "", "date": s.date, "end_date": s.end_date, "crew": crew, "status": s.status})
        schedules = [{"id": s.id, "title": s.title, "date": s.date, "end_date": s.end_date, "attendees": attendees_of(s), "status": s.status}
                     for s in await _schedule_rows(session, d0, d1)]
    return conflicts(att, d0, d1, leaves, shoots, schedules, exclude_id=exclude)


# ── 工作登記：寫 ─────────────────────────────────────

async def _writer(request: Request, attendees: list) -> dict:
    """登記自己＝綁定人員檔案；含別人或外部人員＝crm_projects。回本人 ident（username／staff_id／name）。"""
    payload = _reader(request)
    me = await _me(request)
    others = [a for a in attendees if not (a["staff_id"] and a["staff_id"] == me["staff_id"])]
    if others or not attendees:
        if not payload_grants(payload, WRITE_OTHERS_KEY):
            raise HTTPException(status_code=403, detail="排別人或外部人員需要「專案管理」權限；登記自己請把人員留自己")
    elif not me["staff_id"]:
        raise HTTPException(status_code=409, detail="帳號尚未綁定人員檔案，請聯絡管理員")
    return me


def _apply(s, req: SchedulePayload, partial: bool) -> None:
    def given(k):
        return (k in req.model_fields_set) if partial else True
    if given("kind"):
        s.kind = req.kind if req.kind in KINDS else "work"
    if given("title") or not partial:
        title = (req.title or "").strip()[:255]
        if not title and not partial:
            raise HTTPException(status_code=422, detail="請填「做什麼」")
        if title:
            s.title = title
    if given("project_id"):
        s.project_id = (req.project_id or "").strip() or None
    if given("date") and (req.date or not partial):
        s.date = _parse_day(req.date, "date", required=True)
    if given("end_date"):
        ed = _parse_day(req.end_date, "end_date")
        s.end_date = ed if ed and ed > s.date else None
    if given("slot") or given("start_time") or given("end_time") or not partial:
        slot = (req.slot or "").strip() or ("custom" if (req.start_time or req.end_time) else "all")
        st, et = slot_times(slot, req.start_time or (s.start_time or ""), req.end_time or (s.end_time or ""))
        s.start_time, s.end_time = (st or None), (et or None)
    if given("attendees"):
        s.attendees = json.dumps(attendee_norm(req.attendees or []), ensure_ascii=False)
    if given("location_text"):
        s.location_text = (req.location_text or "").strip()[:255] or None
    if given("notes"):
        s.notes = (req.notes or "").strip() or None


async def _plan_card_sync(session, s, me: dict, delete: bool = False) -> None:
    """一期：登記自己的單日工作 → 同時建／更新一張「我的一週」計畫卡（plan_row_id）；多人或多日或取消 → 刪卡。"""
    att = attendees_of(s)
    solo = len(att) == 1 and att[0]["staff_id"] and att[0]["staff_id"] == me["staff_id"] and not s.end_date and s.status == PLANNED and not delete
    if s.plan_row_id:
        row = await session.get(Timesheet, s.plan_row_id)
        if row is not None and row.status == "plan":
            if solo:
                row.work_date, row.task_note = midnight_of(s.date), (s.title or "")[:255]
                row.project_id = s.project_id
                if s.project_id:
                    p = await session.get(CrmProject, s.project_id)
                    row.project_name = (p.name if p is not None else row.project_name) or row.project_name
                row.start_time, row.end_time = s.start_time, s.end_time
                return
            await session.delete(row)
        s.plan_row_id = None
    if solo:
        # 建卡走 add_rows（同 /timesheets/mine/rows）：row_hash／專案對映／階段鏡射都在那裡，不自己 new Timesheet
        from types import SimpleNamespace
        from core.schemas import TimesheetManualRow
        from services.timesheet_self import add_rows
        pname = ""
        if s.project_id:
            p = await session.get(CrmProject, s.project_id)
            pname = (p.name if p is not None else "") or ""
        ident = {"staff_id": me["staff_id"], "staff": SimpleNamespace(name=me["name"]), "username": me["username"]}
        res = await add_rows(session, ident, [TimesheetManualRow(work_date=s.date.isoformat(), project_id=s.project_id or None,
                                                                  project_name=pname, task_note=(s.title or "")[:255],
                                                                  start_time=s.start_time, end_time=s.end_time, plan=True)])
        ids = list(res.get("ids") or [])
        s.plan_row_id = ids[0] if ids else None


async def _row_or_404(session, sid: str):
    s = await session.get(CrmSchedule, sid)
    if s is None:
        raise HTTPException(status_code=404, detail="找不到這筆工作登記")
    return s


async def _out(session, s) -> dict:
    info = (await _projects(session, [s.project_id])).get(s.project_id or "", {})
    return schedule_dict(s, info.get("name", ""), info.get("client", ""))


async def _finish(sid: str) -> dict:
    """寫入端點的收尾：同步 Google → 回最新列。"""
    await calendar_sync.sync("schedule", sid)
    factory = db_factory_or_503()
    async with factory() as session:
        return {"schedule": await _out(session, await _row_or_404(session, sid))}


@router.post("/schedule")
async def create_schedule(req: SchedulePayload, request: Request):
    att = attendee_norm(req.attendees or [])
    me = await _writer(request, att)
    if not att:
        att = [{"staff_id": me["staff_id"], "name": me["name"], "role": "", "external": False, "contact": ""}]
        req = req.model_copy(update={"attendees": att})
    factory = db_factory_or_503()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        s = CrmSchedule(id=uuid.uuid4().hex, status=PLANNED, created_by=me["username"], created_at=now, updated_at=now)
        _apply(s, req, partial=False)
        session.add(s)
        await session.flush()
        await _plan_card_sync(session, s, me)
        await session.commit()
        sid = s.id
    return await _finish(sid)


@router.put("/schedule/{sid}")
async def update_schedule(sid: str, req: SchedulePayload, request: Request):
    factory = db_factory_or_503()
    async with factory() as session:
        s = await _row_or_404(session, sid)
        att = attendee_norm(req.attendees) if "attendees" in req.model_fields_set and req.attendees is not None else attendees_of(s)
        me = await _writer(request, att)
        _apply(s, req, partial=True)
        s.updated_at = datetime.now(timezone.utc)
        await _plan_card_sync(session, s, me)
        await session.commit()
    return await _finish(sid)


@router.post("/schedule/{sid}/status")
async def set_schedule_status(sid: str, req: ScheduleStatusPayload, request: Request):
    status = (req.status or "").strip()
    if status not in STATUSES:
        raise HTTPException(status_code=422, detail="status 必須是 " + "／".join(STATUSES))
    factory = db_factory_or_503()
    async with factory() as session:
        s = await _row_or_404(session, sid)
        me = await _writer(request, attendees_of(s))
        s.status = status
        s.updated_at = datetime.now(timezone.utc)
        await _plan_card_sync(session, s, me, delete=(status == CANCELLED))
        await session.commit()
    return await _finish(sid)


@router.delete("/schedule/{sid}")
async def delete_schedule(sid: str, request: Request):
    factory = db_factory_or_503()
    async with factory() as session:
        s = await _row_or_404(session, sid)
        me = await _writer(request, attendees_of(s))
        eid = s.google_event_id
        await _plan_card_sync(session, s, me, delete=True)
        await session.delete(s)
        await session.commit()
    await calendar_sync.delete_events([eid])
    return {"status": "ok", "id": sid}


@router.post("/schedule/{sid}/done")
async def schedule_done(sid: str, req: ScheduleDonePayload, request: Request):
    """「做了 ✓」：對本人產一列工時（同 /timesheets/mine/rows），記進 timesheet_ids；已產過回同一列。"""
    from core.schemas import TimesheetManualRow
    from services.timesheet_self import add_rows
    ident = await require_bound_staff(request, *READ_KEYS)
    factory = db_factory_or_503()
    async with factory() as session:
        s = await _row_or_404(session, sid)
        att = attendees_of(s)
        if not is_attendee(att, ident["staff_id"], ident["staff"].name):
            raise HTTPException(status_code=409, detail="你不在這件工作的人員裡")
        done_ids = timesheet_ids_of(s)
        existing = None
        if done_ids:
            existing = (await session.execute(
                select(Timesheet).where(Timesheet.id.in_(done_ids), Timesheet.staff_id == ident["staff_id"]))).scalars().first()
        if existing is not None:
            return {"schedule": await _out(session, s), "timesheet_id": existing.id, "created": False}
        row = await _out(session, s)
        fields = timesheet_row_for_done(row, req.hours)
        # 計畫卡本來就是這一件 → 直接把它變成實際（同一列），不再多插一列
        if s.plan_row_id:
            card = await session.get(Timesheet, s.plan_row_id)
            if card is not None and card.status == "plan" and card.staff_id == ident["staff_id"]:
                card.hours, card.status, card.start_time, card.end_time = fields["hours"], "draft", fields["start_time"], fields["end_time"]
                card.task_note = fields["task_note"]
                s.timesheet_ids = json.dumps(done_ids + [card.id])
                s.plan_row_id = None
                if all(a["staff_id"] == ident["staff_id"] or a["external"] for a in att):
                    s.status = DONE
                await session.commit()
                tid = card.id
                await calendar_sync.sync("schedule", sid)
                return {"schedule": row, "timesheet_id": tid, "created": True}
        res = await add_rows(session, ident, [TimesheetManualRow(**fields)])
        new_ids = list(res.get("ids") or [])
        s.timesheet_ids = json.dumps(done_ids + new_ids)
        if all(a["staff_id"] == ident["staff_id"] or a["external"] for a in att):
            s.status = DONE
        await session.commit()
    await calendar_sync.sync("schedule", sid)
    return {"schedule": row, "timesheet_id": new_ids[0] if new_ids else "", "created": True}


@router.post("/schedule/{sid}/resync")
async def schedule_resync(sid: str, request: Request):
    _reader(request)
    return await _finish(sid)


@router.post("/resync-all")
async def resync_all(request: Request, days: int = Query(180, ge=1, le=400)):
    check_admin(request)
    return await calendar_sync.resync_all(days)


# ── 顏色 ──

@router.get("/colors")
async def get_colors(request: Request):
    _reader(request)
    return _colors_payload()


@router.put("/colors")
async def put_colors(req: CalendarColorsPayload, request: Request, apply: int = Query(0)):
    """六類別各一個 Google colorId；apply=1 順便把未來 180 天的事件全部重新同步（換色）。"""
    check_admin(request)
    from config import load_settings, save_settings
    colors = color_map(req.colors or {})
    s = load_settings()
    g = dict(s.get("google_calendar") or {})
    g["colors"] = colors
    save_settings({"google_calendar": g})
    out = {"colors": _colors_payload()}
    if apply:
        out["applied"] = await calendar_sync.resync_all(180)
    return out


# ── 手機「今天」（通告）──

@router.get("/day")
async def calendar_day(request: Request, date_: str = Query("", alias="date")):
    """本人那天：拍攝（含器材／地點／crew）＋被排的工作＋本週到期、我負責的里程碑。"""
    ident = await require_bound_staff(request, *READ_KEYS)
    d = _parse_day(date_, "date") or date.today()
    me = {"staff_id": ident["staff_id"], "name": ident["staff"].name or ""}
    hide = _hide_mine(request)
    week_end = d + timedelta(days=6 - d.weekday())
    factory = db_factory_or_503()
    async with factory() as session:
        sch = await _schedule_rows(session, d, d)
        info = await _projects(session, [s.project_id for s in sch])
        work = [_schedule_event(schedule_dict(s, info.get(s.project_id or "", {}).get("name", ""), info.get(s.project_id or "", {}).get("client", "")),
                                info.get(s.project_id or "", {}), me, hide) for s in sch if s.status != CANCELLED]
        shoots = await _shoot_events(session, d, d, me, hide)
        ms = await _milestone_events(session, d, week_end, me, hide)
        leaves = await _leave_events(session, d, d, me)
    return {"date": d.isoformat(), "me": me,
            "shoots": [x for x in shoots if x["mine"]], "work": [x for x in work if x["mine"]],
            "milestones": [x for x in ms if x["mine"] and x["status"] != "done"],
            "leave": [x for x in leaves if x["mine"]]}
