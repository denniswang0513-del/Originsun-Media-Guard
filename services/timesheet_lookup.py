"""services/timesheet_lookup.py — 工時對映用的兩張查表（拿 session 回查表）。

router（ingest／remap／budgets／summary／手填）與 `scripts/import_timesheets.py` 的
dry-run 都吃這裡 —— 腳本不該為了一個查表函式 import 整個 FastAPI router 模組。
規則本身在 `core.hr_logic`（純函式），這裡只有 I/O。
"""
from __future__ import annotations

from sqlalchemy import select

from core.finance_logic import load_margin_model, margin_for_type, suggested_budget_hours
from core.hr_logic import ProjectLookup, budget_burn, group_by_name, is_stale, lookup_row, tw_day
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


async def project_names(session, project_ids) -> dict:
    """`{project_id: name}`（手填只給 id 沒給名的那幾列回查案名；services 自己查，不 import router）。"""
    from db.models import CrmProject
    ids = [p for p in set(project_ids or []) if p]
    if not ids:
        return {}
    rows = (await session.execute(select(CrmProject.id, CrmProject.name).where(CrmProject.id.in_(ids)))).all()
    return {pid: n or "" for pid, n in rows}


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


def suggested_hours(model: dict, contract, tax_rate, ptype):
    """建議工時預算＝合約未稅 ×（1−該案型預期毛利）÷ 日成本 × 每日工時（core.finance_logic）。
    burn 表與專案檔案頁同一份。"""
    return suggested_budget_hours(contract, tax_rate, margin_for_type(model, ptype),
                                  model["daily_cost"], model["hours_per_day"])


async def burn_rows(session) -> list:
    """burn 表的專案那一半（/summary 與 /dashboard 共用）：每個已對映案的已投入／預算／消耗率／停滯。
    只選要用的四欄 —— CrmProject 六十多欄含幾個 JSONB／Text，三百案整列撈是白費。"""
    from datetime import date
    from sqlalchemy import func as safunc
    from db.models import Client, CrmProject, Timesheet
    matched = (await session.execute(
        select(Timesheet.project_id, safunc.sum(Timesheet.hours), safunc.count(Timesheet.id),
               safunc.max(Timesheet.work_date))
        .where(Timesheet.project_id.isnot(None)).where(Timesheet.hours > 0)   # 只算實際，同 /me/team/projects
        .group_by(Timesheet.project_id))).all()
    pids = [m[0] for m in matched]
    # 客戶欄（owner 2026-09-06）：代稱優先、沒有才全稱 —— 跟專案同一句 outerjoin 撈
    projs = {row[0]: row[1:] for row in (await session.execute(
        select(CrmProject.id, CrmProject.name, CrmProject.status, CrmProject.budget_hours,
               CrmProject.contract_amount, CrmProject.tax_rate, CrmProject.project_type,
               safunc.coalesce(safunc.nullif(Client.short_name, ""), Client.full_name, ""))
        .outerjoin(Client, Client.id == CrmProject.client_id)
        .where(CrmProject.id.in_(pids)))).all()} if pids else {}
    model = load_margin_model("mine")            # 自動對映只認私帳案 → burn 表都是私帳案
    today = date.today()
    items = []
    for pid, total, cnt, last_date in matched:
        name, status, budget, contract, tax_rate, ptype, cname = projs.get(pid, ("", "", None, 0, None, "", ""))
        last_day = tw_day(last_date)
        # 建議預算：合約未稅 ×（1−該案型預期毛利）÷ 日成本 × 每日工時（core.finance_logic）
        suggested = suggested_hours(model, contract, tax_rate, ptype)
        items.append({
            "project_id": pid, "project_name": name or "", "status": status or "", "client": cname or "",
            "hours_used": round(total or 0, 1), "budget_hours": budget, **budget_burn(total, budget),
            "suggested_hours": suggested, "project_type": ptype or "",
            "rows": cnt, "last_entry": last_day.isoformat() if last_day else None,
            "stale": is_stale(status or "", last_day, today),
        })
    items.sort(key=lambda x: (x["pct"] is None, -(x["pct"] or 0)))
    return items
