"""services/timesheet_lookup.py — 工時對映用的兩張查表（拿 session 回查表）。

router（ingest／remap／budgets／summary／手填）與 `scripts/import_timesheets.py` 的
dry-run 都吃這裡 —— 腳本不該為了一個查表函式 import 整個 FastAPI router 模組。
規則本身在 `core.hr_logic`（純函式），這裡只有 I/O。
"""
from __future__ import annotations

from sqlalchemy import select

from core.hr_logic import ProjectLookup, group_by_name


async def load_project_lookup(session) -> ProjectLookup:
    """對映表 ＋ **私帳**專案（owner 2026-09-02：這張 Sheet 對應的是私帳的專案）。"""
    from db.models import Client, CrmProject, TimesheetProjectMap
    pmap = dict((await session.execute(
        select(TimesheetProjectMap.sheet_name, TimesheetProjectMap.project_id))).all())
    rows = (await session.execute(
        select(CrmProject.id, CrmProject.name, Client.short_name)
        .outerjoin(Client, Client.id == CrmProject.client_id)
        .where(CrmProject.entity == "mine"))).all()
    return ProjectLookup.build(pmap, rows)


async def load_staff_index(session) -> dict:
    """`{姓名: [(id, name), …]}` —— 同名不合併，「不猜」由 `core.hr_logic.unique_hit` 判。"""
    from db.models import CrmStaff
    rows = (await session.execute(select(CrmStaff.id, CrmStaff.name))).all()
    return group_by_name(rows)
