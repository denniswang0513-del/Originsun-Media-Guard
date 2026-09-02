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
from core.hr_logic import Misses, explain_miss, remap_target, resolve_project
from core.schemas import (TimesheetBudgetRequest, TimesheetIngestRequest, TimesheetManualRequest,
                          TimesheetProjectMapRequest, TimesheetPullSettings)
from routers.crm._shared import project_names_map
from services import timesheet_puller
from services.timesheet_ingest import ingest, parse_date as _parse_date
from services.timesheet_lookup import load_project_lookup

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
        if (r.hours or 0) <= 0:
            raise HTTPException(status_code=422, detail="時數需大於 0")
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
            hours=float(r.hours),
            status="draft",
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
