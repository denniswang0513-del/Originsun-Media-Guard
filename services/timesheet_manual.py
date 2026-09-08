"""services/timesheet_manual.py — 手填列（管理端代填／員工自填／總表改列）的落庫規則與專案下拉。

一列輸入 → 欄位只有 normalize_row 一支：分類在清單內（norm_work_type）、給了 project_id 就用它
（下拉明確選的）否則名稱走 resolve_project（跟 Sheet 同一支，手填與 Sheet 才不會落到不同
project_id 讓 burn 表分兩列）—— 插入（insert_manual_rows）與更新（timesheet_self.apply_update）
同吃，422 帶規則原句。手填列（manual=True）另套「實際或計畫至少一個 > 0」並算 status；
Sheet 列不套。更新時案名沒動可給 keep=(案名, project_id) 沿用原對映（caller 判，這裡不重判）。
"""
from __future__ import annotations

from core.project_flow import is_closed

import re
import uuid

from fastapi import HTTPException
from sqlalchemy import func as safunc, select

from core.hr_logic import Misses, norm_work_type, resolve_project, row_state
from services.timesheet_ingest import parse_date
from services.timesheet_lookup import load_project_lookup, project_names


async def names_for(session, rows) -> dict:
    """下拉只給 id 沒給名的那幾列才回查案名（有界 IN；目前兩個前端都只送名字，這是 API 路）。"""
    return await project_names(session, [r.project_id for r in rows if r.project_id and not (r.project_name or "").strip()])


_HHMM = re.compile(r"^(\d{1,2})(?::?(\d{2}))?$")   # 「9」「930」「09:30」都收（同格子 normTime）


def hhmm_or_none(v) -> str | None:
    """「9:30」「0930」「17:30」→ "09:30"／"17:30"；空或看不懂 → None（跟格子的 normTime 同一套寬鬆）。"""
    m = _HHMM.match(str(v or "").strip())
    if not m:
        return None
    h, mm = int(m.group(1)), int(m.group(2) or 0)
    if h > 23 or mm > 59:
        return None
    return f"{h:02d}:{mm:02d}"


def _hours_from_range(start, end) -> float:
    """起訖都有、時數沒給 → 算出來（跨午夜 +24h；同 ts-sheet.applyTimeRange）。"""
    a, b = hhmm_or_none(start), hhmm_or_none(end)
    if not a or not b:
        return 0.0
    m = lambda s: int(s[:2]) * 60 + int(s[3:])
    mins = m(b) - m(a)
    if mins < 0:
        mins += 24 * 60
    return round(mins / 60, 2)


def normalize_row(r, lk, id_to_name: dict, *, manual: bool = True, keep: tuple | None = None) -> tuple:
    """一列輸入（TimesheetManualRow 形狀）→ (要落庫的欄位, 對映原因 why)；規則錯 → 422。
    manual=False（改 Sheet 列）不套 row_state：status 保留、0 小時也放行（Sheet 本來就收 0）。
    keep=(案名, project_id)：沿用這個對映、不查表（caller 已判定案名沒動，lk 可為 None）。"""
    start, end = hhmm_or_none(getattr(r, "start_time", None)), hhmm_or_none(getattr(r, "end_time", None))
    hours = float(r.hours or 0) or _hours_from_range(start, end)     # 起訖都給、時數沒給 → 算出來（只算這一次）
    try:
        status = row_state(hours, r.planned_hours, plan=bool(getattr(r, "plan", None))) if manual else None
        wt = norm_work_type(r.work_type)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    wd = parse_date(r.work_date)
    if wd is None:
        raise HTTPException(status_code=422, detail=f"日期格式錯誤：{r.work_date}")
    pname = (r.project_name or "").strip()
    # 空白列不存。沒時數的列（草稿 pending、或「我的一週」排的計畫 plan）都要有內容才收 ——
    # plan 旗標會讓 row_state 直接回 "plan"，只看 pending 的話，帶 plan:true 的空 POST 就繞過這道守衛存進一列垃圾
    if status in ("pending", "plan") and hours <= 0 and not (
            pname or (r.task_note or "").strip() or getattr(r, "stage_id", None)
            or (getattr(r, "remark", None) or "").strip()):   # 前端「填了任何一格就存」：備註也算一格
        raise HTTPException(status_code=422, detail="空白列不存：至少要有專案、做了什麼、備註或工作階段")
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
        "remark": (getattr(r, "remark", None) or "").strip() or None,
        "start_time": start, "end_time": end,          # 沒帶＝apply_update 先從列上補回來（同 remark／planned_hours）
        "hours": hours,
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
    ids = []
    for r in rows:
        fields, why = normalize_row(r, lk, id_to_name)
        misses.note(why, fields["project_name"])
        rid = uuid.uuid4().hex
        session.add(Timesheet(id=rid, staff_name=staff_name, staff_id=staff_id,
                              source="manual", row_hash="manual_" + uuid.uuid4().hex, **fields))
        ids.append(rid)
    # ids 照送進來的順序回：我的一天的格子存完要把 id 掛回那一列，之後改就是 PUT 同一列
    return {"inserted": len(rows), "ids": ids, **misses.report()}


async def project_options(session, staff_name: str | None = None) -> list:
    """補登用專案下拉：進行中（製作/結案）+ 該員最近填過的專案名。"""
    from db.models import Client, CrmProject, Timesheet
    rows = (await session.execute(
        select(CrmProject.id, CrmProject.name, Client.short_name, CrmProject.start_date, CrmProject.shoot_date, CrmProject.created_at,
               CrmProject.status, CrmProject.entity, CrmProject.mine_link_id, CrmProject.source_project_id, CrmProject.updated_at)
        .outerjoin(Client, Client.id == CrmProject.client_id)
        .where(CrmProject.status.in_(("製作", "結案")))
        .order_by(CrmProject.name)
    )).all()
    # 最近有更新的排前面（owner 2026-09-06）：最近一筆工時的日期 vs 專案本身 updated_at，取較新者
    from core.hr_logic import tw_day
    ids = [r[0] for r in rows]
    last_ts = {pid: d for pid, d in (await session.execute(
        select(Timesheet.project_id, safunc.max(Timesheet.work_date)).where(Timesheet.project_id.in_(ids))
        .group_by(Timesheet.project_id))).all()} if ids else {}
    touched = {r[0]: max((d for d in (tw_day(last_ts.get(r[0])), tw_day(r[10])) if d), default=None) for r in rows}
    rows = sorted(rows, key=lambda r: (touched[r[0]] is None, -(touched[r[0]].toordinal() if touched[r[0]] else 0), r[1] or ""))
    # 已連結的母私帳只列一個（owner 2026-09-06：同名出現兩次）。工時對映只認私帳，所以留私帳那筆、
    # 母帳那筆若它的私帳分身也在清單裡就不列；連結兩種形狀都認（母帳 mine_link_id／私帳 source_project_id）
    ids = {r[0] for r in rows}
    shadowed = {r[0] for r in rows if r[7] != "mine" and r[8] in ids}
    shadowed |= {r[9] for r in rows if r[7] == "mine" and r[9] in ids}
    opts = []
    for pid, n, client, sd, shd, cd, st, _ent, _ml, _src, _upd in rows:
        if pid in shadowed:
            continue
        d = sd or shd or cd
        year = str(d.year) if d else ""
        # label＝「年份 客戶 案名」（owner 2026-09-03：跟零用金一樣的呈現）；前端用它當下拉的字，存的時候對回 id
        # closed：前端把下拉分「進行中（預設）／已結案（收著）」（owner 2026-09-03）——分組規則住這裡，前端只看旗標
        opts.append({"id": pid, "name": n or "", "client": client or "", "year": year, "closed": is_closed(st),
                     "label": " ".join(x for x in (year, client or "", n or "") if x)})
    if staff_name:
        have = {o["name"] for o in opts}
        recent = (await session.execute(
            select(Timesheet.project_name, safunc.max(Timesheet.work_date))
            .where(Timesheet.staff_name == staff_name)
            .group_by(Timesheet.project_name)
            .order_by(safunc.max(Timesheet.work_date).desc())
            .limit(10)
        )).all()
        opts.extend({"id": None, "name": p, "client": "", "year": "", "closed": False, "label": p} for p, _ in recent if p and p not in have)
    return opts


