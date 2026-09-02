"""services/timesheet_manual.py — 手填列（管理端代填／員工自填／總表改列）的落庫規則與專案下拉。

一列輸入 → 欄位只有 normalize_row 一支：分類在清單內（norm_work_type）、給了 project_id 就用它
（下拉明確選的）否則名稱走 resolve_project（跟 Sheet 同一支，手填與 Sheet 才不會落到不同
project_id 讓 burn 表分兩列）—— 插入（insert_manual_rows）與更新（timesheet_self.apply_update）
同吃，422 帶規則原句。手填列（manual=True）另套「實際或計畫至少一個 > 0」並算 status；
Sheet 列不套。更新時案名沒動可給 keep=(案名, project_id) 沿用原對映（caller 判，這裡不重判）。
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import func as safunc, select

from core.hr_logic import Misses, norm_work_type, resolve_project, row_state
from services.timesheet_ingest import parse_date
from services.timesheet_lookup import load_project_lookup, project_names


async def names_for(session, rows) -> dict:
    """下拉只給 id 沒給名的那幾列才回查案名（有界 IN；目前兩個前端都只送名字，這是 API 路）。"""
    return await project_names(session, [r.project_id for r in rows if r.project_id and not (r.project_name or "").strip()])


def normalize_row(r, lk, id_to_name: dict, *, manual: bool = True, keep: tuple | None = None) -> tuple:
    """一列輸入（TimesheetManualRow 形狀）→ (要落庫的欄位, 對映原因 why)；規則錯 → 422。
    manual=False（改 Sheet 列）不套 row_state：status 保留、0 小時也放行（Sheet 本來就收 0）。
    keep=(案名, project_id)：沿用這個對映、不查表（caller 已判定案名沒動，lk 可為 None）。"""
    try:
        status = row_state(r.hours, r.planned_hours) if manual else None
        wt = norm_work_type(r.work_type)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    wd = parse_date(r.work_date)
    if wd is None:
        raise HTTPException(status_code=422, detail=f"日期格式錯誤：{r.work_date}")
    pname = (r.project_name or "").strip()
    if r.project_id:
        pid, why = r.project_id, "map"
        pname = pname or (id_to_name.get(pid) or "").strip()
    elif keep is not None:
        pid, why = keep[1], "map"
    else:
        pid, why = resolve_project(pname, lk)
    return {
        "work_date": wd, "project_id": pid, "project_name": pname,
        "task_note": (r.task_note or "").strip() or None,
        "hours": float(r.hours or 0),
        "planned_hours": float(r.planned_hours) if r.planned_hours is not None else None,
        "work_type": wt, "status": status,
    }, why


async def insert_manual_rows(session, staff_id: str, staff_name: str, rows) -> dict:
    """手填列落庫（source='manual'、status＝plan／draft、帶 staff_id）。caller 負責 commit。

    row_hash 用 manual_ 前綴 uuid（不與 Sheet 冪等 hash 相干 — 手填允許同日同案多筆；
    Sheet 端撞手填由 ingest 的 manual_dup_key 檢查擋）。
    """
    from db.models import Timesheet
    if not rows:
        raise HTTPException(status_code=422, detail="至少一列")
    lk = await load_project_lookup(session)
    id_to_name = await names_for(session, rows)
    misses = Misses()
    for r in rows:
        fields, why = normalize_row(r, lk, id_to_name)
        misses.note(why, fields["project_name"])
        session.add(Timesheet(id=uuid.uuid4().hex, staff_name=staff_name, staff_id=staff_id,
                              source="manual", row_hash="manual_" + uuid.uuid4().hex, **fields))
    return {"inserted": len(rows), **misses.report()}


async def project_options(session, staff_name: str | None = None) -> list:
    """補登用專案下拉：進行中（製作/結案）+ 該員最近填過的專案名。"""
    from db.models import CrmProject, Timesheet
    rows = (await session.execute(
        select(CrmProject.id, CrmProject.name)
        .where(CrmProject.status.in_(("製作", "結案")))
        .order_by(CrmProject.name)
    )).all()
    opts = [{"id": pid, "name": n or ""} for pid, n in rows]
    if staff_name:
        have = {o["name"] for o in opts}
        recent = (await session.execute(
            select(Timesheet.project_name, safunc.max(Timesheet.work_date))
            .where(Timesheet.staff_name == staff_name)
            .group_by(Timesheet.project_name)
            .order_by(safunc.max(Timesheet.work_date).desc())
            .limit(10)
        )).all()
        opts.extend({"id": None, "name": p} for p, _ in recent if p and p not in have)
    return opts


