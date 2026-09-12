# -*- coding: utf-8 -*-
"""routers/timesheets/mapping.py —— 工作追蹤 API 的一段：私帳案名對映表、回填（remap）、Sheet 專案狀態的預算

2026-09-12 從 routers/api_timesheets.py（1,100 行）拆成套件；URL 全部不變（同一顆 router 在 _shared）。
共用守衛／小工具在 ._shared；掃原始碼的測試用 _srcscan.timesheets_src()。
"""
from fastapi import HTTPException, Request
from sqlalchemy import func as safunc, select, update
from core.auth import check_admin_or_module, current_username
from core.db_guard import db_factory_or_503
from core.hr_logic import Misses, remap_target, resolve_project
from core.schemas import TimesheetBudgetRequest, TimesheetProjectMapRequest
from db.models import Timesheet, TimesheetProjectMap
from routers.crm._shared import project_names_map
from services.timesheet_lookup import load_project_lookup
from ._shared import _require_mine_admin, router
from .projects import _write_budget_hours


# ── 對映表／回填／預算（docs/TIMESHEET_IMPORT_PLAN.md Phase A-3）────────────────

@router.get("/projects")
async def timesheet_projects(request: Request):
    """挑選視窗用的私帳案清單：只有 id／名稱／客戶。

    不用 `/crm/projects?entity=mine`：那支回整包金額欄位給一個只要名字的用途、
    走 MoneyRedactRoute 白繞一圈。這裡同一份 load_project_lookup。

    權限稽核第二批（2026-09-08）：讀清單放給 timesheets 分頁鑰匙（只有 id／名稱／客戶，沒有錢；員工填工時
    的下拉本來就選得到私帳案名）。寫入那半邊（project_map／remap／budgets）仍是 `_require_mine_admin`，
    前端「指定專案」只在 Lv3 畫。
    """
    check_admin_or_module(request, "timesheets")
    factory = db_factory_or_503()
    async with factory() as session:
        lk = await load_project_lookup(session)
    return {"projects": sorted((r for hits in lk.by_name.values() for r in hits),
                               key=lambda r: r["name"])}


@router.put("/project_map")
async def upsert_project_map(req: TimesheetProjectMapRequest, request: Request):
    """整批 upsert。專案必須存在（任一帳本 —— owner 決定的可以指到母公司案）。"""
    _require_mine_admin(request, level="full")
    factory = db_factory_or_503()
    who = current_username(request)
    async with factory() as session:
        pids = {it.project_id for it in req.items}
        bad = sorted(pids - set(await project_names_map(session, pids)))   # 存在的才回得來
        if bad:
            raise HTTPException(status_code=422, detail=f"找不到專案：{bad[:5]}")
        n = 0
        for it in req.items:
            key = (it.sheet_name or "").strip()
            if not key:
                continue
            m = await session.get(TimesheetProjectMap, key)
            if m is None:
                m = TimesheetProjectMap(sheet_name=key)
                session.add(m)
            m.project_id = it.project_id
            m.decided_by = who
            m.note = (it.note or "")[:255] or None
            n += 1
        await session.commit()
    return {"status": "ok", "upserted": n}


@router.post("/remap")
async def remap_timesheets(request: Request):
    """依對映表＋規則回填既有列的 `project_id`。規則在 core.hr_logic.remap_target
    （對映表覆蓋／自動只補空的／絕不清空）。

    🔴 依 (project_name, project_id) **聚合**再判定：全表 9,800 列只有 ~350 個不同名字，
    逐列 resolve ＋ 9,800 個 ORM 物件 ＋ 9,800 句 UPDATE 是白費 —— 每按一次「指定專案」
    都會跑這支。
    """
    _require_mine_admin(request, level="full")
    factory = db_factory_or_503()
    changed = 0
    by_reason: dict = {}
    async with factory() as session:
        lk = await load_project_lookup(session)
        groups = (await session.execute(
            select(Timesheet.project_name, Timesheet.project_id, safunc.count(Timesheet.id))
            .where(Timesheet.project_name != "")
            .group_by(Timesheet.project_name, Timesheet.project_id))).all()
        for pname, cur_pid, n in groups:
            pid, why = resolve_project(pname, lk)
            by_reason[why] = by_reason.get(why, 0) + n
            target = remap_target(why, cur_pid, pid)
            if target is None:
                continue
            res = await session.execute(
                update(Timesheet)
                .where(Timesheet.project_name == pname,
                       Timesheet.project_id == cur_pid)   # None → SQLAlchemy 自己出 IS NULL
                .values(project_id=target))
            changed += res.rowcount or 0
        await session.commit()
    return {"status": "ok", "changed": changed, "by_reason": by_reason}


@router.put("/budgets")
async def set_budgets(req: TimesheetBudgetRequest, request: Request):
    """Sheet「專案狀態」的預算（剩餘＋實際）→ 對到的案的 `budget_hours`。
    走同一支 resolver；撞案與找不到的原樣回報、不寫。≤0 視為沒設、跳過。"""
    _require_mine_admin(request, level="full")
    factory = db_factory_or_503()
    applied = 0
    misses = Misses()
    async with factory() as session:
        lk = await load_project_lookup(session)
        wanted: dict = {}
        for it in req.items:
            if it.budget_hours <= 0:
                continue
            pid, why = resolve_project(it.sheet_name, lk)
            if not misses.note(why, it.sheet_name) and pid:
                wanted[pid] = float(it.budget_hours)
        applied = await _write_budget_hours(session, wanted)
    return {"status": "ok", "applied": applied, **misses.report()}   # 鍵名同 /ingest
