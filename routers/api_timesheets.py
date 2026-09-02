"""
api_timesheets.py — 工作追蹤 API（docs/WORK_TRACKING_UI_PLAN.md；前身 N2 階段 0 的工時檢核）

資料進來的路：主控端定時拉整本 Sheet（services/timesheet_puller，公開連結 xlsx export；設定在
/pull）或 Apps Script 推 /ingest（帶 X-Timesheet-Token）→ 都走 services.timesheet_ingest 去重寫入
timesheets → 專案對映走 core.hr_logic.resolve_project（對映表／去客戶前綴，撞案不猜）。
員工自己填走 /mine*（services.timesheet_self，own-scope）。

看的路：/board（每日看板）、/project／/compare（專案檔案、類似專案並排）、/person（人員檔案）、
/summary（burn）、/dashboard、/export.csv；規則全在 core.hr_logic（純函式），這裡只做 I/O。
預算只從 PUT /budgets（Sheet「專案狀態」一次性）與 PUT /project_budget 進。

token：settings.json `timesheet.ingest_token`（首次取用自動生成）。
"""

import csv
import io
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from fastapi.responses import Response
from sqlalchemy import func as safunc, select, update

import core.state as state
from config import load_settings, save_settings
from core.auth import check_admin, check_admin_or_module, current_username, payload_grants
from core.db_guard import db_factory_or_503
from core.hr_logic import (WORK_TYPES, Misses, active_fillers, budget_burn, explain_miss, hours_rollup,
                           is_stale, local_day, missing_fillers, month_span, months_back, norm_work_type,
                           prev_workday, project_metrics, remap_target, resolve_project, row_state,
                           similar_projects, split_sheet_name, type_composition)
from core.schemas import (MeTimesheetBatch, MeTimesheetUpdate, TimesheetBudgetRequest, TimesheetBudgetSet,
                          TimesheetDigestSettings, TimesheetIngestRequest, TimesheetManualRequest,
                          TimesheetProjectMapRequest, TimesheetPullSettings, TimesheetRowAdminUpdate)
from db.models import CrmProject, CrmQuotation, CrmQuotationItem, CrmStaff, Timesheet, TimesheetProjectMap
from routers.crm._shared import project_names_map
from services import timesheet_digest, timesheet_puller
from services.timesheet_ingest import ingest, parse_date as _parse_date
from services.timesheet_lookup import load_project_lookup
from services.timesheet_self import (add_rows, admin_delete_row, admin_update_row, bound_ident, delete_row,
                                     list_rows, ts_dict, update_row)

router = APIRouter(prefix="/api/v1/timesheets", tags=["timesheets"])

_MAX_ROWS_PER_CALL = 1000


def _get_or_create_ingest_token() -> str:
    s = load_settings()
    tok = (s.get("timesheet") or {}).get("ingest_token") or ""
    if not tok:
        tok = "tsk_" + secrets.token_hex(24)
        s.setdefault("timesheet", {})["ingest_token"] = tok
        save_settings(s)
    return tok


def _require_mine_admin(request: Request, level: str = "view") -> None:
    """對映相關端點（列私帳案／指定對映／回填／灌預算）的守衛。

    🔴 私帳**專案**對沒有 mine scope 的人整列不存在（core.ledger.hide_mine_projects；
    Lv3 不隱含 finance_mine）。讀清單守 view；把時數改掛到私帳案、往私帳案寫預算
    是寫私帳，守 full（同 routers/crm 其他寫私帳的端點）—— 一支守衛，規則只有這一份。
    """
    check_admin(request)
    from core.ledger import MINE, require_entity
    require_entity(request, MINE, level=level)


def _month_or_422(month: str):
    try:
        return month_span(month)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


def _day_or_422(day: str) -> datetime:
    d = _parse_date(day) if day else datetime.now()
    if d is None:
        raise HTTPException(status_code=422, detail=f"日期格式錯誤：{day}")
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


@router.get("/ingest_token")
async def get_ingest_token(request: Request):
    """取同步 token（admin）— 貼進 Apps Script 的 TOKEN 常數。"""
    check_admin(request)
    return {"token": _get_or_create_ingest_token()}


@router.post("/ingest")
async def ingest_rows(req: TimesheetIngestRequest, request: Request):
    """Apps Script 批次上行。冪等：row_hash 重複的列自動跳過。"""
    expected = _get_or_create_ingest_token()
    got = request.headers.get("X-Timesheet-Token", "")
    if not got or not secrets.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="X-Timesheet-Token 無效")
    if len(req.rows) > _MAX_ROWS_PER_CALL:
        raise HTTPException(status_code=422, detail=f"單次上限 {_MAX_ROWS_PER_CALL} 列，分批送")
    if not state.db_online:
        # Apps Script 端會重試，給明確訊息（刻意不同於通用 503）
        raise HTTPException(status_code=503, detail="資料庫離線，稍後重送（Apps Script 會重試）")
    factory = db_factory_or_503()
    # 寫入規則（去重／手填優先／對映）只有 services.timesheet_ingest 那一份 —— 拉取 runner 也走它
    async with factory() as session:
        return await ingest(session, req.rows, req.source)


# ── 主控端定時拉 Sheet／週一 digest（services；取代 Apps Script 推）──────────

@router.get("/pull")
async def get_pull(request: Request):
    check_admin(request)
    return timesheet_puller.get_pull_settings()


@router.put("/pull")
async def put_pull(req: TimesheetPullSettings, request: Request):
    """改 enabled／sheet_id／cron（admin）；cron 錯 → 422。"""
    check_admin(request)
    try:
        return timesheet_puller.update_pull_settings(req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"cron 格式錯誤：{e}")


@router.post("/pull")
async def run_pull_now(request: Request):
    """立刻拉一次（admin；不看 enabled，dev 也能手動測）。"""
    check_admin(request)
    return await timesheet_puller.run_pull(force=True)


@router.get("/digest")
async def get_digest(request: Request):
    check_admin(request)
    return timesheet_digest.settings.get()


@router.put("/digest")
async def put_digest(req: TimesheetDigestSettings, request: Request):
    check_admin(request)
    try:
        return timesheet_digest.settings.update(req.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"cron 格式錯誤：{e}")


@router.post("/digest")
async def send_digest_now(request: Request, preview: bool = False):
    """立刻算上週 digest；preview=1 只回文字不發送。"""
    check_admin(request)
    if preview:
        factory = db_factory_or_503()
        async with factory() as session:
            d = await timesheet_digest.build_digest(session)
        return {"status": "preview", "text": d["text"]}
    return await timesheet_digest.send_digest(force=True)


# ── 每日看板 ＋ 我的一天（P1）──────────────────────────────────────────────────
#
# 閘門＝timesheets 模組（進得了 tab 就看得到大家每天做了什麼，owner 拍板）；
# 「我的一天」的讀改刪再加「綁定人員檔案」（services.timesheet_self.bound_ident）。

@router.get("/board")
async def day_board(request: Request, date: str = "", days: int = 1):
    """每日看板：從 date 起 days 天（1＝當天、7＝週模式），每天每個人做了什麼（實際＋計畫）。
    只顯示有列的人；不排名、不標紅（主管層另做）。"""
    check_admin_or_module(request, "timesheets")
    days = max(1, min(int(days or 1), 14))
    d0 = _day_or_422(date)
    d1 = d0 + timedelta(days=days)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet).where(Timesheet.work_date >= d0).where(Timesheet.work_date < d1)
            .order_by(Timesheet.work_date, Timesheet.staff_name, Timesheet.created_at)
        )).scalars().all()
    by_day: dict = {}
    for r in rows:
        it = ts_dict(r)
        by_day.setdefault(it["date"], {}).setdefault(it["staff_name"] or "(空白)", []).append(it)
    out_days = []
    for i in range(days):
        k = (d0 + timedelta(days=i)).date().isoformat()
        people = [{"name": n, "items": its, "hours": round(sum(x["hours"] for x in its), 1),
                   "planned": round(sum(x["planned_hours"] or 0 for x in its), 1)}
                  for n, its in sorted(by_day.get(k, {}).items())]
        out_days.append({"date": k, "people": people})
    return {"from": d0.date().isoformat(), "days": days, "items": out_days}


@router.get("/mine")
async def my_day(request: Request, date: str = ""):
    """我的一天：本人該日的工作項（實際＋計畫）＋ 計畫／實際合計 ＋ 昨天的列（供「複製昨天」）。"""
    ident = await bound_ident(request, "timesheets")
    d0 = _day_or_422(date)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = await list_rows(session, ident, d0 - timedelta(days=1), d0 + timedelta(days=1))
    day = d0.date().isoformat()
    today_items = [i for i in rows if i["date"] == day]
    return {
        "date": day, "staff_name": ident["staff"].name,
        "items": today_items,
        "planned_total": round(sum(i["planned_hours"] or 0 for i in today_items), 1),
        "actual_total": round(sum(i["hours"] for i in today_items), 1),
        "yesterday": [i for i in rows if i["date"] < day],
        "work_types": list(WORK_TYPES),
    }


@router.post("/mine/rows")
async def my_add_rows(body: MeTimesheetBatch, request: Request):
    ident = await bound_ident(request, "timesheets")
    factory = db_factory_or_503()
    async with factory() as session:
        return await add_rows(session, ident, body.rows)


@router.put("/mine/{row_id}")
async def my_update_row(row_id: str, body: MeTimesheetUpdate, request: Request):
    """改自己的一列（含「完成」：把 hours 填上，計畫列就變實際）。"""
    ident = await bound_ident(request, "timesheets")
    factory = db_factory_or_503()
    async with factory() as session:
        return await update_row(session, ident, row_id, body)


@router.delete("/mine/{row_id}")
async def my_delete_row(row_id: str, request: Request):
    ident = await bound_ident(request, "timesheets")
    factory = db_factory_or_503()
    async with factory() as session:
        return await delete_row(session, ident, row_id)


# ── 總表（像發票總表那樣一列一列看；管理員逐列調細節與備註）──────────────────

@router.get("/rows")
async def ledger_rows(request: Request, month: str = ""):
    """該月所有列（每人每案每項）。篩選（人／案／關鍵字／來源）在前端做 —— 一個月至多千列。
    editable 只給管理員（一般成員看得到、改不了）。"""
    is_admin = payload_grants(check_admin_or_module(request, "timesheets"))
    m0, m1 = _month_or_422(month)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet).where(Timesheet.work_date >= m0).where(Timesheet.work_date < m1)
            .order_by(Timesheet.work_date.desc(), Timesheet.staff_name, Timesheet.created_at))).scalars().all()
    return {"month": m0.strftime("%Y-%m"), "editable": is_admin,
            "items": [ts_dict(r) for r in rows], "work_types": list(WORK_TYPES)}


@router.put("/rows/{row_id}")
async def ledger_update_row(row_id: str, body: TimesheetRowAdminUpdate, request: Request):
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return await admin_update_row(session, row_id, body)


@router.delete("/rows/{row_id}")
async def ledger_delete_row(row_id: str, request: Request):
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return await admin_delete_row(session, row_id)


# ── 專案檔案頁／類似專案並排／人員檔案頁／改預算（P2）──────────────────────────

async def _project_candidates(session) -> list:
    """類似專案的候選池：每個 Sheet 案名的總時數與客戶前綴（一次 group by）。"""
    rows = (await session.execute(
        select(Timesheet.project_name, safunc.sum(Timesheet.hours))
        .where(Timesheet.project_name != "").group_by(Timesheet.project_name))).all()
    return [{"name": n, "client": split_sheet_name(n)[0], "total": float(h or 0)} for n, h in rows]


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


def _metrics_input(rows):
    """Timesheet 列 → project_metrics 的輸入（日、人、分類、小時）；計畫列由 project_metrics 自己略過。"""
    return [(local_day(r.work_date), r.staff_name, r.work_type, r.hours) for r in rows]


def _by_month(rows) -> list:
    acc: dict = {}
    for r in rows:
        d = local_day(r.work_date)
        if d and (r.hours or 0) > 0:
            acc[d.strftime("%Y-%m")] = round(acc.get(d.strftime("%Y-%m"), 0.0) + float(r.hours), 1)
    return sorted(acc.items())


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
async def project_file(request: Request, name: str = ""):
    """專案檔案頁（Sheet 案名）：摘要、分類組成、時間軸（逐日工作項）、各人、各月、預算、
    報價人日、類似專案（自動推薦，人再挑）。"""
    check_admin_or_module(request, "timesheets")
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name 必填")
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet).where(Timesheet.project_name == name)
            .order_by(Timesheet.work_date.desc(), Timesheet.staff_name))).scalars().all()
        pid = next((r.project_id for r in rows if r.project_id), None)
        proj = await session.get(CrmProject, pid) if pid else None
        quote_days = await _quote_days(session, pid)
        cands = await _project_candidates(session)
    m = project_metrics(_metrics_input(rows))
    budget = getattr(proj, "budget_hours", None) if proj else None
    return {
        "project_name": name, "project_id": pid or "", "status": getattr(proj, "status", "") if proj else "",
        "mapped": bool(pid), **m,
        "budget_hours": budget, **budget_burn(m["total"], budget),
        "quote_days": quote_days,
        "by_month": _by_month(rows),
        "timeline": _day_log(rows, limit=90),
        "similar": similar_projects(name, split_sheet_name(name)[0], m["total"], cands),
    }


@router.get("/compare")
async def compare_projects(request: Request, names: str = ""):
    """類似專案並排：names 用 | 分隔，每案回 project_metrics（總時數／人數／起訖／分類組成／各人）。"""
    check_admin_or_module(request, "timesheets")
    wanted = [n.strip() for n in (names or "").split("|") if n.strip()][:6]
    if not wanted:
        raise HTTPException(status_code=422, detail="names 必填（| 分隔）")
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet).where(Timesheet.project_name.in_(wanted)))).scalars().all()
    return {"items": [{"project_name": n, **project_metrics(_metrics_input(r for r in rows if r.project_name == n))}
                      for n in wanted]}


@router.get("/person")
async def person_file(request: Request, name: str = "", month: str = ""):
    """人員檔案頁：該月逐日流水、每日時數（熱圖）、案別組成、分類組成、近 12 個月走勢。"""
    check_admin_or_module(request, "timesheets")
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name 必填")
    m0, m1 = _month_or_422(month)
    factory = db_factory_or_503()
    async with factory() as session:
        # 一次撈 12 個月（一個人一年幾百列），該月的列從裡面切
        year_rows = (await session.execute(
            select(Timesheet).where(Timesheet.staff_name == name)
            .where(Timesheet.work_date >= months_back(m0, 11)).where(Timesheet.work_date < m1)
            .order_by(Timesheet.work_date.desc(), Timesheet.created_at))).scalars().all()
    month_key = m0.strftime("%Y-%m")
    rows = [r for r in year_rows if (local_day(r.work_date) or m0.date()).strftime("%Y-%m") == month_key]
    heat: dict = {}
    projects: dict = {}
    for r in rows:
        d = local_day(r.work_date)
        if d and (r.hours or 0) > 0:
            heat[d.isoformat()] = round(heat.get(d.isoformat(), 0.0) + float(r.hours), 1)
            projects[r.project_name or "(空白)"] = round(projects.get(r.project_name or "(空白)", 0.0) + float(r.hours), 1)
    return {
        "name": name, "month": month_key,
        "total": round(sum(heat.values()), 1), "days_filled": len(heat),
        "heat": heat,
        "days": _day_log(rows),
        "projects": sorted(projects.items(), key=lambda x: -x[1]),
        "composition": type_composition((r.work_type, r.hours) for r in rows),
        "trend": _by_month(year_rows),
    }


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


# ── 儀表板／匯出（P3）────────────────────────────────────────────────────────

async def burn_summary_core(session) -> list:
    """burn 表的專案那一半（/summary 與 /dashboard 共用）。"""
    matched = (await session.execute(
        select(Timesheet.project_id, safunc.sum(Timesheet.hours), safunc.count(Timesheet.id),
               safunc.max(Timesheet.work_date))
        .where(Timesheet.project_id.isnot(None)).group_by(Timesheet.project_id))).all()
    pids = [m[0] for m in matched]
    projs = {p.id: p for p in (await session.execute(
        select(CrmProject).where(CrmProject.id.in_(pids)))).scalars()} if pids else {}
    items = []
    today = datetime.now().date()
    for pid, total, cnt, last_date in matched:
        p = projs.get(pid)
        budget = getattr(p, "budget_hours", None) if p else None
        last_day = local_day(last_date)
        items.append({
            "project_id": pid,
            "project_name": getattr(p, "name", "") if p else "",
            "status": getattr(p, "status", "") if p else "",
            "hours_used": round(total or 0, 1), "budget_hours": budget,
            **budget_burn(total, budget),
            "rows": cnt,
            "last_entry": last_day.isoformat() if last_day else None,
            "stale": is_stale(getattr(p, "status", "") if p else "", last_day, today),
        })
    items.sort(key=lambda x: (x["pct"] is None, -(x["pct"] or 0)))
    return items


@router.get("/dashboard")
async def dashboard(request: Request):
    """六格：大家的（今日在做什麼、本週全體、本月分類組成、burn 前五）＋
    主管層（負載排名、昨天漏填、有計畫沒結果）—— 主管層只給管理員（不給全員比較）。"""
    payload = check_admin_or_module(request, "timesheets")
    is_admin = payload_grants(payload)          # 不帶模組鑰匙＝純管理員判定
    now = datetime.now()
    today = now.date()
    week_mon = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    m0, _ = month_span("")
    since = min(week_mon, m0) - timedelta(days=30)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet.staff_name, Timesheet.work_date, Timesheet.project_name,
                   Timesheet.work_type, Timesheet.hours, Timesheet.status)
            .where(Timesheet.work_date >= since))).all()
        burn = (await burn_summary_core(session))[:5]
    data = [(n, local_day(d), p, wt, float(h or 0), st) for n, d, p, wt, h, st in rows]
    today_rows = [x for x in data if x[1] == today]
    week = hours_rollup([(n, d, p, h) for n, d, p, _wt, h, _st in data if d and d >= week_mon.date()],
                        now.year, now.month)
    out = {
        "today": {"people": len({n for n, *_ in today_rows}), "items": len(today_rows),
                  "hours": round(sum(x[4] for x in today_rows), 1)},
        "week": {"total": week["total"], "people": len(week["people"]),
                 "reference_per_person": 5 * 8, "from": week_mon.date().isoformat()},
        "month_composition": type_composition((wt, h) for _n, d, _p, wt, h, _st in data if d and d >= m0.date()),
        "burn_top": burn,
    }
    if is_admin:
        yday = prev_workday(today)
        filled = {n for n, d, _p, _wt, h, _st in data if d == yday and h > 0}
        out["manager"] = {
            "load": [{"name": p["name"], "hours": p["total"]} for p in week["people"]],
            "missing_yesterday": {"date": yday.isoformat(), "names": missing_fillers(active_fillers(data, today), filled)},
            "plans_open": sorted({n for n, d, _p, _wt, _h, st in data if st == "plan" and d and d < today}),
        }
    return out


@router.get("/export.csv")
async def export_csv(request: Request, month: str = "", project: str = ""):
    """匯出 CSV（給會計／結算）：month=YYYY-MM 或 project=Sheet 案名，至少一個。"""
    check_admin_or_module(request, "timesheets")
    if not month and not project:
        raise HTTPException(status_code=422, detail="month 或 project 至少一個")
    q = select(Timesheet).order_by(Timesheet.work_date, Timesheet.staff_name)
    if month:
        m0, m1 = _month_or_422(month)
        q = q.where(Timesheet.work_date >= m0).where(Timesheet.work_date < m1)
    if project:
        q = q.where(Timesheet.project_name == project.strip())
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(q)).scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["日期", "人員", "專案", "分類", "內容", "計畫小時", "實際小時", "來源", "狀態"])
    for r in rows:
        it = ts_dict(r)
        w.writerow([it["date"], it["staff_name"], it["project_name"], it["work_type"], it["task_note"],
                    it["planned_hours"] if it["planned_hours"] is not None else "", it["hours"], it["source"], it["status"]])
    fname = f"timesheets_{month or project}.csv"
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


# ── 對映表／回填／預算（docs/TIMESHEET_IMPORT_PLAN.md Phase A-3）────────────────

@router.get("/projects")
async def timesheet_projects(request: Request):
    """挑選視窗用的私帳案清單：只有 id／名稱／客戶。

    不用 `/crm/projects?entity=mine`：那支回整包金額欄位給一個只要名字的用途、
    走 MoneyRedactRoute 白繞一圈。這裡同一份 load_project_lookup。

    守衛見 `_require_mine_admin` —— 沒指名的管理員按「指定專案」會拿到 403
    「沒有該帳本的檢視權限」，而不是打開一個空視窗。
    """
    _require_mine_admin(request)
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
        # 一次載入要改的案（367 案逐案 session.get 是 367 趟）
        projs = (await session.execute(
            select(CrmProject).where(CrmProject.id.in_(wanted)))).scalars().all() if wanted else []
        for proj in projs:
            if getattr(proj, "budget_hours", None) != wanted[proj.id]:
                proj.budget_hours = wanted[proj.id]
                applied += 1
        await session.commit()
    return {"status": "ok", "applied": applied, **misses.report()}   # 鍵名同 /ingest


# ── 手填工時（與 Sheet 同步共存；N-hr 人事管理 v1）─────────────────────

async def project_options(session, staff_name: str | None = None) -> list:
    """補登用專案下拉：進行中（製作/結案）+ 該員最近填過的專案名。"""
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


async def insert_manual_rows(session, staff_id: str, staff_name: str, rows) -> dict:
    """手填列落庫（source='manual'、status＝plan／draft、帶 staff_id）。

    row_hash 用 manual_ 前綴 uuid（不與 Sheet 冪等 hash 相干 — 手填允許同日同案
    多筆；Sheet 端撞手填由 ingest 的 manual_dup_key 檢查擋）。caller 負責 commit。
    """
    # 名稱對映走跟 Sheet 同一支 resolver —— 手填一次、Sheet 一次落到不同 project_id，
    # Burn 表就分兩列。下拉給了 id 就用 id（那是使用者明確選的）。
    lk = await load_project_lookup(session)
    # 下拉只給 id 沒給名的那幾列才回查案名（有界 IN，同其他呼叫端）
    id_to_name = await project_names_map(
        session, [r.project_id for r in rows if not (r.project_name or "").strip()])

    inserted = 0
    misses = Misses()
    for r in rows:
        # 實際或計畫至少一個 > 0；分類在清單內（規則在 core.hr_logic，422 帶原句）
        try:
            status = row_state(r.hours, r.planned_hours)
            wt = norm_work_type(r.work_type)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        wd = _parse_date(r.work_date)
        if wd is None:
            raise HTTPException(status_code=422, detail=f"日期格式錯誤：{r.work_date}")
        pname = (r.project_name or "").strip()
        pid, why = (r.project_id, "map") if r.project_id else resolve_project(pname, lk)
        pname = pname or (id_to_name.get(pid or "") or "").strip()
        misses.note(why, pname)
        session.add(Timesheet(
            id=uuid.uuid4().hex,
            work_date=wd,
            staff_name=staff_name,
            staff_id=staff_id,
            project_id=pid,
            project_name=pname,
            task_note=(r.task_note or "").strip() or None,
            hours=float(r.hours or 0),
            planned_hours=float(r.planned_hours) if r.planned_hours is not None else None,
            work_type=wt,
            status=status,
            source="manual",
            row_hash="manual_" + uuid.uuid4().hex,
        ))
        inserted += 1
    return {"inserted": inserted, **misses.report()}


@router.get("/project_options")
async def get_project_options(request: Request, staff_name: str = ""):
    """內部補登 grid 的專案下拉（timesheets 模組可用）。"""
    check_admin_or_module(request, "timesheets")
    factory = db_factory_or_503()
    async with factory() as session:
        return {"projects": await project_options(session, staff_name or None)}


@router.post("/manual")
async def add_manual_rows(body: TimesheetManualRequest, request: Request):
    """管理端批次手填（指定人員；員工自助走 /mine/rows 或 /api/v1/me/timesheets/batch）。"""
    check_admin_or_module(request, "timesheets")
    if not body.rows:
        raise HTTPException(status_code=422, detail="至少一列")
    factory = db_factory_or_503()
    async with factory() as session:
        staff = await session.get(CrmStaff, body.staff_id)
        if staff is None:
            raise HTTPException(status_code=404, detail="人員不存在")
        result = await insert_manual_rows(session, staff_id=staff.id,
                                          staff_name=staff.name, rows=body.rows)
        await session.commit()
    return result


@router.get("/by_staff")
async def hours_by_staff(request: Request, month: str = ""):
    """人員月視圖：每人 × 每專案 時數彙總（month=YYYY-MM，預設本月）。"""
    check_admin_or_module(request, "timesheets")
    m0, m1 = _month_or_422(month)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet.staff_name, Timesheet.project_name,
                   safunc.sum(Timesheet.hours), safunc.count(Timesheet.id))
            .where(Timesheet.work_date >= m0)
            .where(Timesheet.work_date < m1)
            .group_by(Timesheet.staff_name, Timesheet.project_name)
            .order_by(Timesheet.staff_name)
        )).all()
    by_staff: dict = {}
    for sname, pname, total, cnt in rows:
        e = by_staff.setdefault(sname or "(空白)", {"name": sname or "(空白)",
                                                   "total_hours": 0.0, "projects": []})
        h = round(float(total or 0), 1)
        e["total_hours"] = round(e["total_hours"] + h, 1)
        e["projects"].append({"project_name": pname or "(空白)", "hours": h, "rows": cnt})
    staff_list = sorted(by_staff.values(), key=lambda x: -x["total_hours"])
    for e in staff_list:
        e["projects"].sort(key=lambda p: -p["hours"])
    return {"month": m0.strftime("%Y-%m"), "staff": staff_list,
            "total_hours": round(sum(e["total_hours"] for e in staff_list), 1)}


@router.get("/summary")
async def burn_summary(request: Request):
    """每專案 burn 摘要：已投入時數 / 預算 / 消耗率。未對映專案以名稱聚合列出。

    🔴 守 mine：自動對映只認私帳案（owner 2026-09-02），所以這張表上每一個案名
    都是私帳案名 —— 可見性在端點決定一次，不逐欄位擋。
    """
    _require_mine_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        items = await burn_summary_core(session)     # 專案那一半與 /dashboard 共用
        unmatched = (await session.execute(
            select(Timesheet.project_name, safunc.sum(Timesheet.hours), safunc.count(Timesheet.id))
            .where(Timesheet.project_id.is_(None))
            .group_by(Timesheet.project_name)
            .order_by(safunc.sum(Timesheet.hours).desc())
        )).all()
        total_rows = (await session.execute(select(safunc.count(Timesheet.id)))).scalar() or 0
        # 未對映的每一個名字：為什麼沒對到（撞案／找不到／內部桶）。撞案附候選、
        # 找不到附相似建議 —— owner 在 tab 上直接指定，不用回頭翻報告。
        lk = await load_project_lookup(session)
    out_unmatched = [{"project_name": (n or "") or "(空白)", "hours_used": round(h or 0, 1), "rows": c,
                      **explain_miss(n or "", lk)}       # candidates 已是 {id,name,client}
                     for n, h, c in unmatched]
    return {"projects": items, "unmatched": out_unmatched, "total_rows": total_rows}


@router.get("/recent")
async def recent_rows(request: Request, limit: int = 50):
    """最近同步進來的列（抽查用，admin）。"""
    check_admin(request)
    factory = db_factory_or_503()
    limit = max(1, min(limit, 200))
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet).order_by(Timesheet.created_at.desc()).limit(limit)
        )).scalars().all()
    return {"rows": [{
        "date": r.work_date.strftime("%Y-%m-%d") if r.work_date else None,
        "staff": r.staff_name,
        "project": r.project_name,
        "matched": bool(r.project_id),
        "task": r.task_note,
        "hours": r.hours,
        "source": r.source,
    } for r in rows]}
