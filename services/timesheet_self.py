"""services/timesheet_self.py — 「自己的工作項」的讀改刪（own-scope 共用件）。

routers/api_me.py（員工端 /my.html）與 routers/api_timesheets.py（CRM tab 的「我的一天」）
都走這裡：列的序列化、「能不能改」的驗證、改列的欄位規則只有一份。
own-scope 一律經 core.identity.resolve_current_staff（token → users.staff_id），
絕不接受 client 傳的 staff_id。
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from core.auth import check_admin_or_module
from core.hr_logic import can_edit_timesheet, norm_work_type, resolve_project, row_state
from core.identity import resolve_current_staff


def ts_dict(r, staff_id: str) -> dict:
    d = r.work_date.astimezone().date() if r.work_date else None   # 寫 naive／讀 aware 差一天：歸一到本地日
    return {
        "id": r.id,
        "date": d.isoformat() if d else "",
        "project_name": r.project_name or "",
        "project_id": r.project_id or "",
        "task_note": r.task_note or "",
        "hours": round(float(r.hours or 0), 2),
        "planned_hours": round(float(r.planned_hours), 2) if r.planned_hours is not None else None,
        "work_type": r.work_type or "",
        "source": r.source or "",
        "status": r.status or "",
        "editable": can_edit_timesheet(r, staff_id) == "",
    }


async def bound_ident(request: Request, module: str) -> dict:
    """守衛（模組鑰匙由呼叫端給：員工頁 me_finance、CRM tab timesheets）＋ 必須綁定人員檔案。"""
    check_admin_or_module(request, module)
    ident = await resolve_current_staff(request)
    if ident["staff"] is None:
        raise HTTPException(status_code=409, detail="帳號尚未綁定人員檔案，請聯絡管理員")
    return ident


async def own_row(session, row_id: str, ident: dict):
    """撈一列並驗「本人＋手填＋未鎖」；不是就 403/409（原因來自 can_edit_timesheet）。"""
    from db.models import Timesheet
    r = await session.get(Timesheet, row_id)
    if r is None:
        raise HTTPException(status_code=404, detail="找不到這一列")
    why = can_edit_timesheet(r, ident["staff_id"])
    if why:
        raise HTTPException(status_code=403 if "不是你的" in why else 409, detail=why)
    return r


async def apply_update(session, r, body) -> None:
    """改自己的一列：日期／專案（重新對映）／分類／內容／實際／計畫。
    `body` 是 TimesheetManualRow 形狀；hours 與 planned_hours 至少一個 > 0。"""
    from routers.crm._shared import project_names_map
    from services.timesheet_ingest import parse_date
    from services.timesheet_lookup import load_project_lookup
    wd = parse_date(body.work_date)
    if wd is None:
        raise HTTPException(status_code=422, detail=f"日期格式錯誤：{body.work_date}")
    try:
        status = row_state(body.hours, body.planned_hours)
        wt = norm_work_type(body.work_type)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    pname = (body.project_name or "").strip()
    if body.project_id:
        pid = body.project_id
        pname = pname or (await project_names_map(session, [pid])).get(pid, "")
    else:
        pid, _why = resolve_project(pname, await load_project_lookup(session))
    r.work_date, r.project_id, r.project_name = wd, pid, pname
    r.task_note = (body.task_note or "").strip() or None
    r.hours = float(body.hours or 0)
    r.planned_hours = float(body.planned_hours) if body.planned_hours is not None else None
    r.work_type = wt
    r.status = status
