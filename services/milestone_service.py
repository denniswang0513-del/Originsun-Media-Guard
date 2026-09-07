"""services/milestone_service.py — 每週專案里程碑的 I/O（規則在 core.milestone_logic；示範 frontend/demo/milestones.html）。

彈窗預設帶出的案（owner 2026-09-07「先帶出最近有紀錄的案子與上週有紀錄的案子」）：
這週有里程碑／從前幾週延過來還沒完成的案 ＋ 近 RECENT_DAYS 天有工時紀錄的案。其餘的案前端打字找（project_options）。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select

from core.hr_logic import STAFF_ACTIVE, midnight_of, tw_day
from core.journal_logic import week_start_of
from core.milestone_logic import (RECENT_DAYS, STATUS_DONE, STATUS_OPEN, as_date, default_due, milestone_dict,
                                  shifted_week, sort_projects)


def _week_of(start: str | None, today: date) -> date:
    d = as_date(start) if start else None
    return week_start_of(d or today)


async def _milestones_for_week(session, week_start: date):
    """這週的 ＋ 更早的週還沒完成的（延過來）。"""
    from db.models import CrmProjectMilestone as M
    return (await session.execute(
        select(M).where(or_(M.week_start == week_start, and_(M.status == STATUS_OPEN, M.week_start < week_start)))
        .order_by(M.week_start, M.sort, M.created_at))).scalars().all()


async def _projects_info(session, ids: list) -> dict:
    from db.models import Client, CrmProject
    if not ids:
        return {}
    rows = (await session.execute(
        select(CrmProject.id, CrmProject.name, Client.short_name, CrmProject.status, CrmProject.entity,
               CrmProject.mine_link_id, CrmProject.source_project_id)
        .outerjoin(Client, Client.id == CrmProject.client_id).where(CrmProject.id.in_(ids)))).all()
    return {pid: {"project_id": pid, "name": n or "", "client": c or "", "status": st or "",
                  "entity": ent or "", "mine_link_id": ml or "", "source_project_id": src or ""}
            for pid, n, c, st, ent, ml, src in rows}


def _shadowed(ids: set, info: dict) -> set:
    """已連結的母私帳只列一個（同 timesheet_manual.project_options）：留私帳那筆，母帳那筆若它的分身也在清單裡就不列。"""
    out = set()
    for pid in ids:
        p = info.get(pid) or {}
        if p.get("entity") != "mine" and p.get("mine_link_id") in ids:
            out.add(pid)
        if p.get("entity") == "mine" and p.get("source_project_id") in ids:
            out.add(p["source_project_id"])
    return out


async def _hours_by_project(session, d0: datetime, d1: datetime) -> dict:
    """{project_id: (小時, 最後一筆日期)}，只算實際時數、只認對到案的列。"""
    from db.models import Timesheet
    rows = (await session.execute(
        select(Timesheet.project_id, func.coalesce(func.sum(Timesheet.hours), 0.0), func.max(Timesheet.work_date))
        .where(Timesheet.work_date >= d0).where(Timesheet.work_date < d1)
        .where(Timesheet.project_id.isnot(None)).where(Timesheet.project_id != "").where(Timesheet.hours > 0)
        .group_by(Timesheet.project_id))).all()
    return {pid: (round(float(h or 0), 1), tw_day(last)) for pid, h, last in rows}


async def staff_options(session) -> list:
    from db.models import CrmStaff
    rows = (await session.execute(
        select(CrmStaff.id, CrmStaff.name).where(or_(CrmStaff.status == STAFF_ACTIVE, CrmStaff.status.is_(None), CrmStaff.status == ""))
        .order_by(CrmStaff.name))).all()
    return [{"id": i, "name": n} for i, n in rows]


async def week_payload(session, start: str | None, today: date | None = None) -> dict:
    """GET /milestones/week：這週的案（有里程碑的＋最近有紀錄的）、每案的里程碑與工時、今天到期、人員清單。"""
    today = today or date.today()
    week_start = _week_of(start, today)
    w0 = midnight_of(week_start)
    w1 = w0 + timedelta(days=7)
    ms = await _milestones_for_week(session, week_start)
    hours_week = await _hours_by_project(session, w0, w1)
    hours_recent = await _hours_by_project(session, w0 - timedelta(days=RECENT_DAYS), w1)
    ids = list({m.project_id for m in ms if m.project_id} | set(hours_recent))
    info = await _projects_info(session, ids)
    with_ms = {m.project_id for m in ms}
    shadow = _shadowed(set(ids), info) - with_ms          # 有里程碑的那筆一定要列
    projects = []
    for pid in ids:
        if pid in shadow:
            continue
        p = {k: v for k, v in (info.get(pid) or {"project_id": pid, "name": "（找不到的案）", "client": "", "status": ""}).items()
             if k in ("project_id", "name", "client", "status")}
        mine = [milestone_dict(m, week_start, today) for m in ms if m.project_id == pid]
        hw = hours_week.get(pid, (0.0, None))
        hr = hours_recent.get(pid, (0.0, None))
        p.update({"milestones": mine, "hours_week": hw[0], "hours_recent": hr[0],
                  "last_activity": hr[1].isoformat() if hr[1] else "",
                  "reason": "milestone" if mine else "recent"})
        projects.append(p)
    flat = [m for p in projects for m in p["milestones"]]
    return {
        "week_start": week_start.isoformat(), "today": today.isoformat(),
        "days": [(week_start + timedelta(days=i)).isoformat() for i in range(7)],
        "default_due": default_due(week_start).isoformat(),
        "projects": sort_projects(projects),
        "open_count": sum(1 for m in flat if not m["done"]), "done_count": sum(1 for m in flat if m["done"]),
        "late_count": sum(1 for m in flat if m["late"]),
        "due_today": [{"id": m["id"], "title": m["title"], "project_name": next((p["name"] for p in projects if p["project_id"] == m["project_id"]), ""),
                       "assignee_name": m["assignee_name"]} for m in flat if not m["done"] and m["due_date"] == today.isoformat()],
        "staff": await staff_options(session),
    }


async def save_week(session, start: str, items: list, username: str) -> dict:
    """POST /milestones/week/save：一次套用整個彈窗（新增／改／勾完成／刪），同一交易。"""
    from db.models import CrmProjectMilestone as M
    today = date.today()
    week_start = _week_of(start, today)
    now = datetime.now(timezone.utc)
    for it in items:
        title = (it.title or "").strip()
        if it.id:
            m = await session.get(M, it.id)
            if m is None:
                continue
            if it.delete or not title:
                await session.delete(m)
                continue
            m.title = title
            m.due_date = as_date(it.due_date) or m.due_date
            m.assignee_staff_id = (it.assignee_staff_id or "").strip() or None
            m.assignee_name = (it.assignee_name or "").strip()
            m.note = (it.note or "").strip() or None
            if it.done is not None and (m.status == STATUS_DONE) != bool(it.done):
                m.status = STATUS_DONE if it.done else STATUS_OPEN
                m.done_by = username if it.done else None
                m.done_at = now if it.done else None
            m.updated_at = now
        else:
            if it.delete or not title or not (it.project_id or "").strip():
                continue
            session.add(M(id=uuid.uuid4().hex, project_id=it.project_id.strip(), week_start=week_start, title=title,
                          due_date=as_date(it.due_date) or default_due(week_start),
                          assignee_staff_id=(it.assignee_staff_id or "").strip() or None,
                          assignee_name=(it.assignee_name or "").strip(), note=(it.note or "").strip() or None,
                          status=STATUS_DONE if it.done else STATUS_OPEN, done_by=username if it.done else None,
                          done_at=now if it.done else None, created_by=username))
    await session.commit()
    return await week_payload(session, week_start.isoformat(), today)


async def set_done(session, mid: str, done: bool, username: str) -> dict:
    from db.models import CrmProjectMilestone as M
    m = await session.get(M, mid)
    if m is None:
        raise HTTPException(status_code=404, detail="找不到這個里程碑")
    m.status = STATUS_DONE if done else STATUS_OPEN
    m.done_by = username if done else None
    m.done_at = datetime.now(timezone.utc) if done else None
    m.updated_at = datetime.now(timezone.utc)
    await session.commit()
    today = date.today()
    return milestone_dict(m, week_start_of(today), today)


async def defer(session, mid: str, start: str, username: str) -> dict:
    """延到下週：從「目前看的這週」往後搬一週（到期跟著搬）。"""
    from db.models import CrmProjectMilestone as M
    m = await session.get(M, mid)
    if m is None:
        raise HTTPException(status_code=404, detail="找不到這個里程碑")
    today = date.today()
    week_start = _week_of(start, today)
    m.week_start, m.due_date = shifted_week(m.week_start, m.due_date, week_start)
    m.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return milestone_dict(m, week_start, today)


async def today_summary(session, today: date) -> dict:
    """今天那條：本週幾個、今天到期哪幾個（給 /me/today）。"""
    p = await week_payload(session, None, today)
    return {"total": p["open_count"] + p["done_count"], "open": p["open_count"], "late": p["late_count"], "due_today": p["due_today"]}


async def project_milestones(session, project_id: str) -> list:
    """專案頁：這個案的里程碑按週。"""
    from db.models import CrmProjectMilestone as M
    today = date.today()
    rows = (await session.execute(select(M).where(M.project_id == project_id).order_by(M.week_start, M.sort, M.created_at))).scalars().all()
    weeks: dict = {}
    for m in rows:
        d = milestone_dict(m, as_date(m.week_start) or week_start_of(today), today)
        weeks.setdefault(d["week_start"], []).append(d)
    return [{"week_start": k, "items": v} for k, v in sorted(weeks.items())]
