# -*- coding: utf-8 -*-
"""routers/timesheets/projects.py —— 工作追蹤 API 的一段：專案檔案頁／類似專案並排／人員檔案頁／建議預算與改預算

2026-09-12 從 routers/api_timesheets.py（1,100 行）拆成套件；URL 全部不變（同一顆 router 在 _shared）。
共用守衛／小工具在 ._shared；掃原始碼的測試用 _srcscan.timesheets_src()。
"""
from fastapi import HTTPException, Request
from sqlalchemy import or_, func as safunc, select
from typing import Optional
from core.auth import check_admin_or_module
from core.db_guard import db_factory_or_503
from core.hr_logic import HOURS_PER_WORKDAY, bucket_hours, budget_burn, day_iso, month_key, months_back, project_metrics, similar_projects, split_sheet_name, tw_day, type_composition
from core.schemas import TimesheetBudgetSet
from db.models import CrmProject, CrmQuotation, CrmQuotationItem, Timesheet
from services.timesheet_lookup import burn_rows, project_names
from services.timesheet_self import metrics_input, month_or_422, rows_by_month, ts_dict
from ._shared import _CANDS_CACHE, _has_ts_module, _require_mine_admin, _ts_or_bound, router


# ── 專案檔案頁／類似專案並排／人員檔案頁／改預算（P2）──────────────────────────



async def _project_candidates(session) -> list:
    """類似專案的候選池：每個 Sheet 案名的總時數與客戶前綴（一次 group by 全表；每開一個專案檔案都算一次太貴，
    5 分鐘內共用——工時本來就是每週拉一次、手填零星）。"""
    import time as _t
    if _CANDS_CACHE["val"] is not None and _t.time() - _CANDS_CACHE["at"] < 300:
        return _CANDS_CACHE["val"]
    rows = (await session.execute(
        select(Timesheet.project_name, safunc.sum(Timesheet.hours))
        .where(Timesheet.project_name != "").group_by(Timesheet.project_name))).all()
    val = [{"name": n, "client": split_sheet_name(n)[0], "total": float(h or 0)} for n, h in rows]
    _CANDS_CACHE.update(at=_t.time(), val=val)
    return val


async def _write_budget_hours(session, wanted: dict) -> int:
    """把 {project_id: hours} 寫進 crm_projects.budget_hours（一次 IN 載入；只數真的變的）。套建議與灌 Sheet 預算同一份。"""
    projs = (await session.execute(
        select(CrmProject).where(CrmProject.id.in_(wanted)))).scalars().all() if wanted else []
    applied = 0
    for p in projs:
        if p.budget_hours != wanted[p.id]:
            p.budget_hours = wanted[p.id]
            applied += 1
    await session.commit()
    return applied


async def _quote_days(session, project_id: str) -> Optional[float]:
    """報價人日：該案最新版報價單裡單位是「天／人日」的數量合計（只帶人日，不帶錢）。"""
    if not project_id:
        return None
    q = (await session.execute(
        select(CrmQuotation.id).where(CrmQuotation.project_id == project_id)
        .order_by(CrmQuotation.version.desc()).limit(1))).scalar()
    if not q:
        return None
    days = (await session.execute(
        select(safunc.sum(CrmQuotationItem.quantity))
        .where(CrmQuotationItem.quotation_id == q)
        .where(CrmQuotationItem.unit.in_(("天", "人日", "日"))))).scalar()
    return float(days) if days else None


def _suggested_for(proj):
    """專案檔案頁的建議預算（同 burn 表那條規則；沒案／沒合約／案型不在表上 → None）。"""
    if proj is None:
        return None
    from core.finance_logic import load_margin_model
    from services.timesheet_lookup import suggested_hours
    return suggested_hours(load_margin_model("mine"), proj.contract_amount, proj.tax_rate, proj.project_type)


def _day_log(rows, limit: Optional[int] = None) -> list:
    """逐日流水：[{date, items:[ts_dict…]}, …] 新的在前（專案時間軸與人員逐日同一形狀）。"""
    days: dict = {}
    for r in rows:
        it = ts_dict(r)
        if it["date"]:
            days.setdefault(it["date"], []).append(it)
    out = [{"date": k, "items": v} for k, v in sorted(days.items(), reverse=True)]
    return out[:limit] if limit else out


@router.get("/project")
async def project_file(request: Request, name: str = "", project_id: str = ""):
    """專案檔案頁＝這個案的**整個**執行狀態：摘要、分類組成、逐日時間軸（全部，不截）、各人、各月、
    預算、報價人日、類似專案（自動推薦，人再挑）。

    給 project_id（burn 表點進來）或 Sheet 案名對到了案 → 撈**整個案**（所有對到它的 Sheet 案名），
    標題用 CRM 案名；沒對映的 Sheet 案名才只撈那個名字（owner 2026-09-03：要看每個專案的執行狀態，
    不是近 90 天）。守衛放寬到綁定人員（§10 專案查詢開放給員工看全案數字；唯讀）。"""
    await _ts_or_bound(request)
    name = (name or "").strip()
    pid = (project_id or "").strip()
    if not name and not pid:
        raise HTTPException(status_code=422, detail="name 或 project_id 至少一個")
    factory = db_factory_or_503()
    async with factory() as session:
        if not pid:      # Sheet 案名 → 對到案就升級成整個案
            pid = (await session.execute(
                select(Timesheet.project_id).where(Timesheet.project_name == name)
                .where(Timesheet.project_id.isnot(None)).limit(1))).scalar() or ""
        cond = (Timesheet.project_id == pid) if pid else (Timesheet.project_name == name)
        rows = (await session.execute(
            select(Timesheet).where(cond).order_by(Timesheet.work_date.desc(), Timesheet.staff_name))).scalars().all()
        proj = await session.get(CrmProject, pid) if pid else None
        quote_days = await _quote_days(session, pid)
        cands = await _project_candidates(session)
        # 里程碑（按週）：專案檔案頁畫這個案每週的里程碑（owner 2026-09-08）；沒對到案就沒有
        from services.milestone_service import project_milestones
        milestone_weeks = await project_milestones(session, pid) if pid else []
    m = project_metrics(metrics_input(rows))
    budget = getattr(proj, "budget_hours", None)
    sheet_names = sorted({r.project_name for r in rows if r.project_name})
    title = (getattr(proj, "name", "") or name or (sheet_names[0] if sheet_names else ""))
    sim_name = name or (sheet_names[0] if sheet_names else title)     # 類似案用 Sheet 案名的規則（客戶前綴）
    return {
        "project_name": title, "project_id": pid or "", "status": getattr(proj, "status", ""),
        "mapped": bool(pid), "sheet_names": sheet_names, **m,
        "budget_hours": budget, **budget_burn(m["total"], budget),
        "quote_days": quote_days,
        "quote_hours": quote_days * HOURS_PER_WORKDAY if quote_days else None,
        # 建議預算是從私帳合約×預期毛利算的：只綁人員檔案的員工拿得到就等於能反推私帳合約 → 只給 timesheets 模組
        "suggested_hours": _suggested_for(proj) if _has_ts_module(request) else None,
        "project_type": getattr(proj, "project_type", "") or "",
        "by_month": rows_by_month(rows),
        "timeline": _day_log(rows),                 # 全部逐日，不截（前端按月分段）
        "similar": similar_projects(sim_name, split_sheet_name(sim_name)[0], m["total"], cands),
        "milestone_weeks": milestone_weeks,         # [{week_start, items:[milestone_dict]}]
    }


@router.get("/compare")
async def compare_projects(request: Request, names: str = ""):
    """類似專案並排：names 用 | 分隔，每案回 project_metrics（總時數／人數／起訖／分類組成／各人）。"""
    check_admin_or_module(request, "timesheets")
    wanted = [n.strip() for n in (names or "").split("|") if n.strip()][:6]
    if not wanted:
        raise HTTPException(status_code=422, detail="names 必填（| 分隔；id:<project_id> 代表整個案）")
    ids = [w[3:] for w in wanted if w.startswith("id:")]
    plain = [w for w in wanted if not w.startswith("id:")]
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet).where(or_(Timesheet.project_name.in_(plain), Timesheet.project_id.in_(ids))))).scalars().all()
        titles = await project_names(session, ids)
    def pick(w):
        return (r for r in rows if (r.project_id == w[3:] if w.startswith("id:") else r.project_name == w))
    return {"items": [{"key": w, "project_name": titles.get(w[3:], w[3:]) if w.startswith("id:") else w,
                       **project_metrics(metrics_input(pick(w)))} for w in wanted]}


@router.get("/person")
async def person_file(request: Request, name: str = "", month: str = ""):
    """人員檔案頁：該月逐日流水、每日時數（熱圖）、案別組成、分類組成、近 12 個月走勢。"""
    check_admin_or_module(request, "timesheets")
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name 必填")
    m0, m1 = month_or_422(month)
    factory = db_factory_or_503()
    async with factory() as session:
        # 一次撈 12 個月（一個人一年幾百列），該月的列從裡面切
        year_rows = (await session.execute(
            select(Timesheet).where(Timesheet.staff_name == name)
            .where(Timesheet.work_date >= months_back(m0, 11)).where(Timesheet.work_date < m1)
            .order_by(Timesheet.work_date.desc(), Timesheet.created_at))).scalars().all()
    mk = month_key(m0)
    rows = [r for r in year_rows if month_key(tw_day(r.work_date)) == mk]   # 查詢已排除 NULL 日期
    heat = bucket_hours((day_iso(r.work_date), r.hours) for r in rows)
    projects = bucket_hours((r.project_name or "(空白)", r.hours) for r in rows)
    return {
        "name": name, "month": mk,
        "total": round(sum(heat.values()), 1), "days_filled": len(heat),
        "heat": heat,
        "days": _day_log(rows),
        "projects": sorted(projects.items(), key=lambda x: -x[1]),
        "composition": type_composition((r.work_type, r.hours) for r in rows),
        "trend": rows_by_month(year_rows),
    }


@router.post("/budgets/suggest")
async def apply_suggested_budgets(request: Request, overwrite: bool = False):
    """把「預期毛利 × 日成本」算出來的建議預算寫進 budget_hours。預設只填**沒設**的案；
    overwrite=1 才連已設的一起蓋（Sheet 灌進來的預算是 owner 的決定，不預設洗掉）。"""
    _require_mine_admin(request, level="full")
    factory = db_factory_or_503()
    applied = 0
    async with factory() as session:
        items = await burn_rows(session)
        targets = {i["project_id"]: i["suggested_hours"] for i in items
                   if i["suggested_hours"] and (overwrite or not i["budget_hours"])}
        applied = await _write_budget_hours(session, targets)
    return {"status": "ok", "applied": applied}


@router.put("/project_budget")
async def set_project_budget(req: TimesheetBudgetSet, request: Request):
    """專案檔案頁直接改預算小時（不用回 Sheet 改）。Sheet 拉取不會覆蓋（預算只從 PUT /budgets 進，那是一次性）。"""
    _require_mine_admin(request, level="full")
    factory = db_factory_or_503()
    async with factory() as session:
        p = await session.get(CrmProject, req.project_id)
        if p is None:
            raise HTTPException(status_code=404, detail="找不到專案")
        p.budget_hours = float(req.budget_hours) if req.budget_hours else None
        await session.commit()
        return {"status": "ok", "project_id": p.id, "budget_hours": p.budget_hours}
