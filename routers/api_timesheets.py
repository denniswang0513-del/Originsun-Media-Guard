"""
api_timesheets.py — 工時 API（N2 階段 0：Google Sheet 自動同步，藍圖 §3.6）

流程：主控端定時拉整本 Sheet（services/timesheet_puller，公開連結 xlsx export；設定在
/pull）→ services.timesheet_ingest 去重寫入 timesheets → 專案對映走
core.hr_logic.resolve_project（對映表／去客戶前綴，撞案不猜）。Apps Script 推 /ingest
（帶 X-Timesheet-Token）仍可用，走同一條寫入。預算只從 PUT /budgets 進（Sheet「專案狀態」頁）。
/summary 給 burn 檢核。

token：settings.json `timesheet.ingest_token`（首次取用自動生成）。
"""

import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request  # type: ignore

import core.state as state
from config import load_settings, save_settings
from core.auth import check_admin, check_admin_or_module, current_username
from core.db_guard import db_factory_or_503
from core.ledger import MINE, require_entity
from core.hr_logic import (WORK_TYPES, Misses, explain_miss, norm_work_type, remap_target,
                           resolve_project, row_state)
from core.schemas import (MeTimesheetBatch, MeTimesheetUpdate, TimesheetBudgetRequest,
                          TimesheetIngestRequest, TimesheetManualRequest, TimesheetProjectMapRequest,
                          TimesheetPullSettings)
from routers.crm._shared import project_names_map
from services import timesheet_puller
from services.timesheet_ingest import ingest, parse_date as _parse_date
from services.timesheet_lookup import load_project_lookup
from services.timesheet_self import apply_update, bound_ident, own_row, ts_dict

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
    require_entity(request, MINE, level=level)


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


# ── 主控端定時拉 Sheet（services/timesheet_puller；取代 Apps Script 推）──────────

@router.get("/pull")
async def get_pull(request: Request):
    """拉取設定與上次結果（admin）。"""
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


# ── 工作追蹤 P1（docs/WORK_TRACKING_UI_PLAN.md）：每日看板 ＋ 我的一天 ─────────────
#
# 閘門＝timesheets 模組（進得了 tab 就看得到大家每天做了什麼，owner 拍板）；
# 「我的一天」的讀改刪再加「綁定人員檔案」（services.timesheet_self.bound_ident）。

def _day_span(day: str):
    d = _parse_date(day) if day else datetime.now()
    if d is None:
        raise HTTPException(status_code=422, detail=f"日期格式錯誤：{day}")
    d0 = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return d0, d0 + __import__("datetime").timedelta(days=1)


@router.get("/work_types")
async def work_types(request: Request):
    check_admin_or_module(request, "timesheets")
    return {"work_types": list(WORK_TYPES)}


@router.get("/board")
async def day_board(request: Request, date: str = "", days: int = 1):
    """每日看板：從 date 起 days 天（1＝當天、7＝週模式），每天每個人做了什麼（實際＋計畫）。
    只顯示有列的人；不排名、不標紅（主管層另做）。"""
    check_admin_or_module(request, "timesheets")
    days = max(1, min(int(days or 1), 14))
    d0, _ = _day_span(date)
    d1 = d0 + __import__("datetime").timedelta(days=days)
    factory = db_factory_or_503()
    from sqlalchemy import select
    from db.models import Timesheet
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet).where(Timesheet.work_date >= d0).where(Timesheet.work_date < d1)
            .order_by(Timesheet.work_date, Timesheet.staff_name, Timesheet.created_at)
        )).scalars().all()
    by_day: dict = {}
    for r in rows:
        day = r.work_date.astimezone().date().isoformat() if r.work_date else ""
        person = by_day.setdefault(day, {}).setdefault(r.staff_name or "(空白)", [])
        person.append({
            "id": r.id, "project_name": r.project_name or "", "task_note": r.task_note or "",
            "hours": round(float(r.hours or 0), 2),
            "planned_hours": round(float(r.planned_hours), 2) if r.planned_hours is not None else None,
            "work_type": r.work_type or "", "status": r.status or "", "source": r.source or "",
        })
    out_days = []
    cur = d0
    while cur < d1:
        k = cur.date().isoformat()
        people = [{"name": n, "items": its, "hours": round(sum(i["hours"] for i in its), 1),
                   "planned": round(sum(i["planned_hours"] or 0 for i in its), 1)}
                  for n, its in sorted(by_day.get(k, {}).items())]
        out_days.append({"date": k, "people": people})
        cur += __import__("datetime").timedelta(days=1)
    return {"from": d0.date().isoformat(), "days": days, "items": out_days}


@router.get("/mine")
async def my_day(request: Request, date: str = ""):
    """我的一天：本人該日的工作項（實際＋計畫）＋ 計畫／實際合計 ＋ 昨天的列（供「複製昨天」）。"""
    ident = await bound_ident(request, "timesheets")
    d0, d1 = _day_span(date)
    y0 = d0 - __import__("datetime").timedelta(days=1)
    factory = db_factory_or_503()
    from sqlalchemy import or_, select
    from db.models import Timesheet
    sid, name = ident["staff_id"], ident["staff"].name
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet)
            .where(or_(Timesheet.staff_id == sid, Timesheet.staff_name == name))
            .where(Timesheet.work_date >= y0).where(Timesheet.work_date < d1)
            .order_by(Timesheet.work_date, Timesheet.created_at)
        )).scalars().all()
    # 🔴 asyncpg 讀回的是 aware UTC、d0 是 naive 本地 —— 直接比會 TypeError；一律歸一到本地日再比
    day0 = d0.date()
    today_items = [ts_dict(r, sid) for r in rows if r.work_date and r.work_date.astimezone().date() >= day0]
    yday_items = [ts_dict(r, sid) for r in rows if r.work_date and r.work_date.astimezone().date() < day0]
    return {
        "date": d0.date().isoformat(), "staff_name": name,
        "items": today_items,
        "planned_total": round(sum(i["planned_hours"] or 0 for i in today_items), 1),
        "actual_total": round(sum(i["hours"] for i in today_items), 1),
        "yesterday": yday_items,
        "work_types": list(WORK_TYPES),
    }


@router.post("/mine/rows")
async def my_add_rows(body: MeTimesheetBatch, request: Request):
    """本人一次填多列（實際或計畫；同 insert_manual_rows 規則）。"""
    ident = await bound_ident(request, "timesheets")
    if not body.rows:
        raise HTTPException(status_code=422, detail="至少一列")
    factory = db_factory_or_503()
    async with factory() as session:
        result = await insert_manual_rows(
            session, staff_id=ident["staff_id"], staff_name=ident["staff"].name, rows=body.rows)
        await session.commit()
    return result


@router.put("/mine/{row_id}")
async def my_update_row(row_id: str, body: MeTimesheetUpdate, request: Request):
    """改自己的一列（含「完成」：把 hours 填上，計畫列就變實際）。"""
    ident = await bound_ident(request, "timesheets")
    factory = db_factory_or_503()
    async with factory() as session:
        r = await own_row(session, row_id, ident)
        await apply_update(session, r, body)
        await session.commit()
        return ts_dict(r, ident["staff_id"])


@router.delete("/mine/{row_id}")
async def my_delete_row(row_id: str, request: Request):
    ident = await bound_ident(request, "timesheets")
    factory = db_factory_or_503()
    async with factory() as session:
        r = await own_row(session, row_id, ident)
        await session.delete(r)
        await session.commit()
    return {"deleted": row_id}


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
    from db.models import TimesheetProjectMap
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
    from sqlalchemy import func as safunc, select, update
    from db.models import Timesheet
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
    from sqlalchemy import select
    from db.models import CrmProject
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
    from sqlalchemy import func, select
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
            select(Timesheet.project_name, func.max(Timesheet.work_date))
            .where(Timesheet.staff_name == staff_name)
            .group_by(Timesheet.project_name)
            .order_by(func.max(Timesheet.work_date).desc())
            .limit(10)
        )).all()
        opts.extend({"id": None, "name": p} for p, _ in recent if p and p not in have)
    return opts


async def insert_manual_rows(session, staff_id: str, staff_name: str, rows) -> dict:
    """手填列落庫（source='manual'、status='draft'、帶 staff_id）。

    row_hash 用 manual_ 前綴 uuid（不與 Sheet 冪等 hash 相干 — 手填允許同日同案
    多筆；Sheet 端撞手填由 ingest 的 manual_dup_key 檢查擋）。caller 負責 commit。
    """
    from db.models import Timesheet
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
    """管理端批次手填（指定人員；員工自助走 POST /api/v1/me/timesheets）。"""
    check_admin_or_module(request, "timesheets")
    if not body.rows:
        raise HTTPException(status_code=422, detail="至少一列")
    factory = db_factory_or_503()
    from db.models import CrmStaff
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
    try:
        base = datetime.strptime(month, "%Y-%m") if month else datetime.now().replace(day=1)
    except ValueError:
        raise HTTPException(status_code=422, detail="month 格式需 YYYY-MM")
    m0 = base.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    m1 = m0.replace(year=m0.year + 1, month=1) if m0.month == 12 else m0.replace(month=m0.month + 1)
    factory = db_factory_or_503()
    from sqlalchemy import func, select
    from db.models import Timesheet
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet.staff_name, Timesheet.project_name,
                   func.sum(Timesheet.hours), func.count(Timesheet.id))
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

    from sqlalchemy import select, func as safunc
    from db.models import Timesheet, CrmProject

    async with factory() as session:
        matched = (await session.execute(
            select(Timesheet.project_id,
                   safunc.sum(Timesheet.hours),
                   safunc.count(Timesheet.id),
                   safunc.max(Timesheet.work_date))
            .where(Timesheet.project_id.isnot(None))
            .group_by(Timesheet.project_id)
        )).all()
        pids = [m[0] for m in matched]
        projs = {}
        if pids:
            for p in (await session.execute(
                    select(CrmProject).where(CrmProject.id.in_(pids)))).scalars():
                projs[p.id] = p

        items = []
        for pid, total, cnt, last_date in matched:
            p = projs.get(pid)
            budget = getattr(p, "budget_hours", None) if p else None
            pct = round(total / budget * 100, 1) if budget else None
            items.append({
                "project_id": pid,
                "project_name": getattr(p, "name", "") if p else "",
                "status": getattr(p, "status", "") if p else "",
                "hours_used": round(total or 0, 1),
                "budget_hours": budget,
                "remaining": round(budget - total, 1) if budget else None,
                "pct": pct,
                "rows": cnt,
                "last_entry": last_date.strftime("%Y-%m-%d") if last_date else None,
            })
        items.sort(key=lambda x: (x["pct"] is None, -(x["pct"] or 0)))

        unmatched = (await session.execute(
            select(Timesheet.project_name,
                   safunc.sum(Timesheet.hours),
                   safunc.count(Timesheet.id))
            .where(Timesheet.project_id.is_(None))
            .group_by(Timesheet.project_name)
            .order_by(safunc.sum(Timesheet.hours).desc())
        )).all()

        total_rows = (await session.execute(select(safunc.count(Timesheet.id)))).scalar() or 0
        # 未對映的每一個名字：為什麼沒對到（撞案／找不到／內部桶）。撞案附候選、
        # 找不到附相似建議 —— owner 在 tab 上直接指定，不用回頭翻報告。
        lk = await load_project_lookup(session)
    out_unmatched = []
    for n, h, c in unmatched:
        name = n or ""
        item = {"project_name": name or "(空白)", "hours_used": round(h or 0, 1), "rows": c,
                **explain_miss(name, lk)}       # candidates 已是 {id,name,client}
        out_unmatched.append(item)
    return {
        "projects": items,
        "unmatched": out_unmatched,
        "total_rows": total_rows,
    }


@router.get("/recent")
async def recent_rows(request: Request, limit: int = 50):
    """最近同步進來的列（抽查用，admin）。"""
    check_admin(request)
    factory = db_factory_or_503()

    from sqlalchemy import select
    from db.models import Timesheet

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
