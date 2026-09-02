"""services/timesheet_self.py — 「自己的工作項」的守衛、序列化、讀改刪（own-scope 共用件）。

routers/api_me.py（員工端 /my.html）與 routers/api_timesheets.py（CRM tab 的「我的一天」）
都走這裡：兩邊只差守衛用的模組鑰匙。own-scope 一律經 core.identity.resolve_current_staff
（token → users.staff_id），絕不接受 client 傳的 staff_id。
"""
from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy import or_, select

from core.auth import check_admin_or_module
from core.hr_logic import EDIT_BLOCK_TEXT, can_edit_timesheet, month_span, tw_day
from core.identity import resolve_current_staff
from services.timesheet_lookup import load_project_lookup
from services.timesheet_manual import insert_manual_rows, names_for, normalize_row

# can_edit_timesheet 的代碼 → HTTP 狀態：不是你的＝403，其餘（Sheet 列／已鎖）＝409
_BLOCK_STATUS = {"not_owner": 403, "not_manual": 409, "locked": 409}


def ts_dict(r, staff_id: str | None = None) -> dict:
    """一列 Timesheet 的唯一序列化（看板／時間軸／人員逐日／我的一天都吃這個）。
    給 `staff_id` 才算 `editable`（own-scope 視角）。"""
    d = tw_day(r.work_date)
    out = {
        "id": r.id,
        "date": d.isoformat() if d else "",
        "staff_name": r.staff_name or "",
        "project_name": r.project_name or "",
        "project_id": r.project_id or "",
        "task_note": r.task_note or "",
        "hours": round(float(r.hours or 0), 2),
        "planned_hours": round(float(r.planned_hours), 2) if r.planned_hours is not None else None,
        "work_type": r.work_type or "",
        "source": r.source or "",
        "status": r.status or "",
        "note": r.note or "",
    }
    if staff_id is not None:
        out["editable"] = can_edit_timesheet(r, staff_id) == ""
    return out


def month_or_422(request_month: str):
    """'YYYY-MM'（空＝本月）→ (月初, 下月初)；格式錯 → 422。兩個 router 共用。"""
    try:
        return month_span(request_month)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


async def bound_ident(request: Request, module: str) -> dict:
    """守衛（模組鑰匙由呼叫端給：員工頁 me_finance、CRM tab timesheets）＋ 必須綁定人員檔案。"""
    check_admin_or_module(request, module)
    ident = await resolve_current_staff(request)
    if ident["staff"] is None:
        raise HTTPException(status_code=409, detail="帳號尚未綁定人員檔案，請聯絡管理員")
    return ident


def own_rows(ident: dict):
    """本人的列：認 staff_id；舊 Sheet 列若 staff_id 空則退回姓名比對。"""
    from db.models import Timesheet
    return select(Timesheet).where(or_(Timesheet.staff_id == ident["staff_id"],
                                       Timesheet.staff_name == ident["staff"].name))


async def get_row(session, row_id: str):
    from db.models import Timesheet
    r = await session.get(Timesheet, row_id)
    if r is None:
        raise HTTPException(status_code=404, detail="找不到這一列")
    return r


async def own_row(session, row_id: str, ident: dict):
    """撈一列並驗「本人＋手填＋未鎖」；不是就 403/409（代碼→狀態，文字給 detail）。"""
    r = await get_row(session, row_id)
    code = can_edit_timesheet(r, ident["staff_id"])
    if code:
        raise HTTPException(status_code=_BLOCK_STATUS[code], detail=EDIT_BLOCK_TEXT[code])
    return r


async def list_rows(session, ident: dict, d0, d1) -> list:
    """本人 [d0, d1) 的列，新的在前，含 editable。"""
    from db.models import Timesheet
    rows = (await session.execute(
        own_rows(ident).where(Timesheet.work_date >= d0).where(Timesheet.work_date < d1)
        .order_by(Timesheet.work_date.desc(), Timesheet.created_at.desc()))).scalars().all()
    return [ts_dict(r, ident["staff_id"]) for r in rows]


async def add_rows(session, ident: dict, rows) -> dict:
    """本人一次填多列（實際或計畫；規則在 insert_manual_rows）。caller 不必 commit。"""
    if not rows:
        raise HTTPException(status_code=422, detail="至少一列")
    result = await insert_manual_rows(session, staff_id=ident["staff_id"], staff_name=ident["staff"].name, rows=rows)
    await session.commit()
    return result


async def apply_update(session, r, body) -> None:
    """把 body（TimesheetManualRow 形狀）套到一列：日期／專案（重新對映）／分類／內容／實際／計畫。
    員工改自己的（update_row）與管理員總表（admin_update_row）同一份；hours 與 planned_hours
    至少一個 > 0。Sheet 列保留 status=import（計畫／實際的判定只對手填列有意義）。不 commit。"""
    fields, _why = normalize_row(body, await load_project_lookup(session), await names_for(session, [body]))
    if r.source != "manual":
        fields.pop("status")          # Sheet 列保留 import：計畫／實際只對手填列有意義
    for k, v in fields.items():
        setattr(r, k, v)
    # 對不到案的名字不擋（project_id 空 → 前端標「未對映」，管理員再指定），跟插入同一規則


async def update_row(session, ident: dict, row_id: str, body) -> dict:
    r = await own_row(session, row_id, ident)
    await apply_update(session, r, body)
    await session.commit()
    return ts_dict(r, ident["staff_id"])


async def delete_row(session, ident: dict, row_id: str) -> dict:
    r = await own_row(session, row_id, ident)
    await session.delete(r)
    await session.commit()
    return {"deleted": row_id}


# ── 總表（管理員）：任一列都能調，含 Sheet 列；守衛在端點（check_admin）──

async def admin_update_row(session, row_id: str, body) -> dict:
    """管理員改任一列（欄位同 apply_update）＋ 管理員備註。改過的 Sheet 列 row_hash 不變，
    下次拉取仍認得它、不會再插一次；但 Sheet 那一格之後若也改了，會以新 hash 另插一列。"""
    r = await get_row(session, row_id)
    await apply_update(session, r, body)
    if body.note is not None:
        r.note = body.note.strip() or None
    await session.commit()
    return {**ts_dict(r), "editable": True}


async def admin_delete_row(session, row_id: str) -> dict:
    await session.delete(await get_row(session, row_id))
    await session.commit()
    return {"deleted": row_id}
