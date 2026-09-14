"""services/calendar_sync.py — 四種事件同一條 Google 日曆同步線（docs/CALENDAR_PLAN.md §3）。

工作登記（crm_schedule）／里程碑（crm_project_milestones）／請假（hr_leave_requests）在這裡；拍攝場次沿用
routers/api_shoots._sync_calendar（它是場次寫入端點的收尾，測試釘住「同步只從 _finish 進」），resync_all 才會叫它。
規矩同場次：憑證／日曆沒設＝整條線中性不動；失敗只寫 sync_error、不擋操作；阻塞 I/O 一律 asyncio.to_thread。
顏色：settings `google_calendar.colors`（core.schedule_logic.color_map），每次組 body 都帶 colorId。
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

from core.db_guard import db_factory_or_503
from core.hr_logic import day_iso, leave_to_dict
from core.schedule_logic import (CANCELLED as SCH_CANCELLED, attendee_norm, color_map, event_body_leave,
                                 event_body_milestone, event_body_schedule)
from services import google_calendar as gc

KINDS = ("schedule", "milestone", "leave", "shoot")


def colors_from_settings() -> dict:
    from config import load_settings
    return color_map((load_settings().get("google_calendar") or {}).get("colors"))


def _ts(dt) -> str | None:
    return dt.isoformat() if dt else None


def attendees_of(obj) -> list:
    try:
        raw = json.loads(obj.attendees) if isinstance(obj.attendees, str) else (obj.attendees or [])
    except ValueError:
        raw = []
    return attendee_norm(raw)


def timesheet_ids_of(obj) -> list:
    try:
        v = json.loads(obj.timesheet_ids) if isinstance(obj.timesheet_ids, str) else (obj.timesheet_ids or [])
    except ValueError:
        v = []
    return [str(x) for x in v if x] if isinstance(v, list) else []


def schedule_dict(s, project_name: str = "", client: str = "") -> dict:
    """CrmSchedule → API dict（行事曆事件流、寫入端點回應、Google body 都吃這一份）。"""
    return {
        "id": s.id, "kind": s.kind or "work", "project_id": s.project_id or "", "project_name": project_name, "client": client,
        "title": s.title or "", "date": day_iso(s.date) or "", "end_date": day_iso(s.end_date) or "",
        "start_time": s.start_time or "", "end_time": s.end_time or "",
        "attendees": attendees_of(s), "location_text": s.location_text or "", "notes": s.notes or "",
        "status": s.status or "planned", "timesheet_ids": timesheet_ids_of(s), "plan_row_id": s.plan_row_id or "",
        "google_event_id": s.google_event_id or "", "synced_at": _ts(s.synced_at), "sync_error": s.sync_error or "",
        "created_by": s.created_by or "",
    }


async def _project_names(session, ids) -> dict:
    from sqlalchemy import select
    from db.models import Client, CrmProject
    ids = [i for i in set(ids or ()) if i]
    if not ids:
        return {}
    rows = (await session.execute(
        select(CrmProject.id, CrmProject.name, CrmProject.entity, Client.short_name)
        .outerjoin(Client, Client.id == CrmProject.client_id).where(CrmProject.id.in_(ids)))).all()
    return {i: {"name": n or "", "entity": e or "parent", "client": c or ""} for i, n, e, c in rows}


async def _body_for(session, kind: str, obj, colors: dict) -> dict:
    if kind == "schedule":
        info = (await _project_names(session, [obj.project_id])).get(obj.project_id or "", {})
        return event_body_schedule(schedule_dict(obj, info.get("name", ""), info.get("client", "")), "", colors)
    if kind == "milestone":
        info = (await _project_names(session, [obj.project_id])).get(obj.project_id or "", {})
        m = {"id": obj.id, "title": obj.title or "", "due_date": obj.due_date, "week_start": obj.week_start,
             "assignee_name": obj.assignee_name or "", "note": obj.note or "", "status": obj.status or "open"}
        return event_body_milestone(m, info.get("name", ""), "", colors)
    if kind == "leave":
        d = leave_to_dict(obj)
        d.update({"hours": obj.hours if obj.hours is not None else float(obj.days or 0) * 8, "part": obj.part or "all",
                  "start_time": obj.start_time or "", "end_time": obj.end_time or ""})
        return event_body_leave(d, obj.staff_name or "", "", colors)
    raise ValueError(kind)


def _model(kind: str):
    from db.models import CrmProjectMilestone, CrmSchedule, HrLeaveRequest
    return {"schedule": CrmSchedule, "milestone": CrmProjectMilestone, "leave": HrLeaveRequest}[kind]


def _wants_delete(kind: str, obj) -> bool:
    """哪些狀態＝日曆上不該有這個事件：工作登記取消、假不是已核准（退回／撤回）。里程碑刪除走 delete_events。"""
    if kind == "schedule":
        return (obj.status or "") == SCH_CANCELLED
    if kind == "leave":
        return (obj.status or "") != "已核准"
    return False


async def sync(kind: str, ident: str) -> dict:
    """同步一筆（建／改／該刪就刪）。回 {google_event_id, synced_at, sync_error, skipped}。"""
    if kind == "shoot":
        from routers.api_shoots import _sync_calendar
        row = await _sync_calendar(ident)
        return {"google_event_id": row.get("google_event_id", ""), "synced_at": row.get("synced_at"),
                "sync_error": row.get("sync_error", ""), "skipped": False}
    sa, shared_id, _err = await gc.load_config()
    cal_id = gc.calendar_for(kind, shared_id)          # 每種事件可以各自一本（休假一本、拍攝一本…）
    factory = db_factory_or_503()
    async with factory() as session:
        obj = await session.get(_model(kind), ident)
        if obj is None:
            return {"sync_error": "找不到這筆", "skipped": True}
        if sa is None or not cal_id:
            return {"google_event_id": obj.google_event_id or "", "synced_at": _ts(obj.synced_at),
                    "sync_error": obj.sync_error or "", "skipped": True}
        now = datetime.now(timezone.utc)
        if _wants_delete(kind, obj):
            ok, e = (True, "") if not obj.google_event_id else await asyncio.to_thread(gc.delete_event, sa, cal_id, obj.google_event_id)
            if ok:
                obj.google_event_id, obj.synced_at, obj.sync_error = None, now, None
            else:
                obj.sync_error = e[:500]
        else:
            body = await _body_for(session, kind, obj, colors_from_settings())
            eid, e = await asyncio.to_thread(gc.upsert_event, sa, cal_id, obj.google_event_id, body)
            if eid:
                obj.google_event_id, obj.synced_at, obj.sync_error = eid, now, None
            else:
                obj.sync_error = (e or "同步失敗")[:500]
        await session.commit()
        return {"google_event_id": obj.google_event_id or "", "synced_at": _ts(obj.synced_at),
                "sync_error": obj.sync_error or "", "skipped": False}


async def sync_many(kind: str, ids) -> list:
    out = []
    for i in list(dict.fromkeys(x for x in (ids or []) if x)):
        try:
            out.append(await sync(kind, i))
        except Exception as e:      # noqa: BLE001 — 同步是 best-effort，一筆壞不擋其餘
            out.append({"sync_error": str(e)[:200], "skipped": False})
    return out


async def delete_events(event_ids, kind: str = "schedule") -> None:
    """已經從資料庫刪掉的東西（里程碑、工作登記硬刪）：只剩事件 id 可刪。best-effort。`kind` 決定去哪本日曆刪。"""
    ids = [e for e in (event_ids or []) if e]
    if not ids:
        return
    sa, shared_id, _err = await gc.load_config()
    cal_id = gc.calendar_for(kind, shared_id)
    if sa is None or not cal_id:
        return
    for eid in ids:
        try:
            await asyncio.to_thread(gc.delete_event, sa, cal_id, eid)
        except Exception:           # noqa: BLE001
            pass


async def resync_all(days: int = 180, past: int = 7) -> dict:
    """管理員的「全部重新同步」：從 `past` 天前到未來 `days` 天的工作登記／里程碑／已核准的假／場次逐筆 sync
    （沒同步過、同步失敗、或顏色剛改都會補上）；退回／撤回但還掛著事件的假也一併刪。歷史匯入要補上日曆時把 past 拉大。"""
    from sqlalchemy import or_, select
    from db.models import CrmProjectMilestone, CrmSchedule, CrmShoot, HrLeaveRequest
    from core.hr_logic import midnight_of
    today = date.today()
    d0, d1 = today - timedelta(days=int(past)), today + timedelta(days=int(days))
    factory = db_factory_or_503()
    async with factory() as session:
        sch = (await session.execute(select(CrmSchedule.id).where(CrmSchedule.date >= d0, CrmSchedule.date <= d1))).scalars().all()
        ms = (await session.execute(select(CrmProjectMilestone.id).where(
            CrmProjectMilestone.due_date >= d0, CrmProjectMilestone.due_date <= d1))).scalars().all()
        lv = (await session.execute(select(HrLeaveRequest.id).where(
            HrLeaveRequest.end_date >= midnight_of(d0), HrLeaveRequest.start_date <= midnight_of(d1),
            or_(HrLeaveRequest.status == "已核准", HrLeaveRequest.google_event_id.isnot(None))))).scalars().all()
        sh = (await session.execute(select(CrmShoot.id).where(CrmShoot.date >= d0, CrmShoot.date <= d1))).scalars().all()
    out = {}
    for kind, ids in (("schedule", sch), ("milestone", ms), ("leave", lv), ("shoot", sh)):
        res = await sync_many(kind, ids)
        out[kind] = {"total": len(res), "failed": sum(1 for r in res if r.get("sync_error") and not r.get("skipped")),
                     "skipped": sum(1 for r in res if r.get("skipped"))}
    return out
