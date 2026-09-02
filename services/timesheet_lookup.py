"""services/timesheet_lookup.py — 工時對映用的兩張查表（拿 session 回查表）。

router（ingest／remap／budgets／summary／手填）與 `scripts/import_timesheets.py` 的
dry-run 都吃這裡 —— 腳本不該為了一個查表函式 import 整個 FastAPI router 模組。
規則本身在 `core.hr_logic`（純函式），這裡只有 I/O。
"""
from __future__ import annotations

from sqlalchemy import select

from core.hr_logic import ProjectLookup, group_by_name, lookup_row
from core.ledger import is_mine


async def load_project_lookup(session) -> ProjectLookup:
    """對映表 ＋ **私帳**專案（owner 2026-09-02：這張 Sheet 對應的是私帳的專案）。"""
    from db.models import Client, CrmProject, TimesheetProjectMap
    pmap = dict((await session.execute(
        select(TimesheetProjectMap.sheet_name, TimesheetProjectMap.project_id))).all())
    rows = (await session.execute(
        select(CrmProject.id, CrmProject.name, Client.short_name)
        .outerjoin(Client, Client.id == CrmProject.client_id)
        .where(is_mine(CrmProject.entity)))).all()
    return ProjectLookup.build(pmap, rows)


async def budgets_for(session, project_ids) -> dict:
    """`{project_id: budget_hours}`（只回有值的）—— burn 表／專案檔案／團隊匯總同一支反查，
    這裡是 is_mine 表態過的查表模組，可見性掃描不必再豁免 router。"""
    from db.models import CrmProject
    ids = [p for p in set(project_ids or []) if p]
    if not ids:
        return {}
    rows = (await session.execute(
        select(CrmProject.id, CrmProject.budget_hours).where(CrmProject.id.in_(ids)))).all()
    return {pid: b for pid, b in rows if b}


async def load_staff_index(session) -> dict:
    """`{姓名: [lookup_row, …]}` —— 同名不合併，「不猜」由 `core.hr_logic.resolve_staff` 判。
    列的形狀跟專案那份一樣（id／name），resolve_* 兩支才能同一個契約。"""
    from db.models import CrmStaff
    rows = (await session.execute(select(CrmStaff.id, CrmStaff.name))).all()
    return group_by_name([lookup_row(sid, nm) for sid, nm in rows])
