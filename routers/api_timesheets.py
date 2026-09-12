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
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request  # type: ignore
from fastapi.responses import Response
from sqlalchemy import func as safunc, or_, select, update

import core.state as state
from config import load_settings, save_settings
from core.auth import _extract_token, check_admin, check_admin_or_module, current_username, payload_grants, check_logged_in
from core.db_guard import db_factory_or_503
from core.hr_logic import _TW
from core.journal_logic import week_start_of
from core.hr_logic import (midnight_of, fillers_on, HOURS_PER_WORKDAY, WORK_TYPES, Misses, active_fillers, bucket_hours, budget_burn,
                           day_iso, explain_miss, hours_rollup, missing_fillers, month_key, month_span, months_back,
                           parse_ymd, prev_workday, project_metrics, remap_target, resolve_project, similar_projects,
                           split_sheet_name, stages_by_category, tw_day, type_composition)
from core.identity import resolve_current_staff
from core.schemas import (MeTimesheetBatch, MeTimesheetUpdate, TimesheetBudgetRequest, TimesheetBudgetSet,
                          TimesheetDigestSettings, TimesheetIngestRequest, TimesheetManualRequest,
                          TimesheetConflictResolve, TimesheetProjectMapRequest, TimesheetPullSettings,
                          TimesheetMergeRequest, TimesheetMergeUndo, TimesheetRowAdminUpdate, TimesheetRowsBatch)
from db.models import CrmProject, CrmQuotation, CrmQuotationItem, CrmStaff, Timesheet, TimesheetProjectMap, WorkStageNode
from routers.crm._shared import project_names_map
from services import timesheet_digest, timesheet_puller
from services.timesheet_conflicts import list_conflicts, resolve_conflict
from services.timesheet_ingest import ingest, parse_date as _parse_date
from services.timesheet_lookup import burn_rows, load_project_lookup, project_names
from services.timesheet_manual import project_options
from services.timesheet_self import (add_rows, board_days, admin_batch_update, admin_delete_row, admin_update_row, delete_row,
                                     list_rows, metrics_input, month_or_422, rows_by_month, search_rows, ts_dict, update_row)

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


def _day_or_422(day: str) -> datetime:
    d = _parse_date(day) if day else datetime.now()
    if d is None:
        raise HTTPException(status_code=422, detail=f"日期格式錯誤：{day}")
    return midnight_of(d)


def _has_ts_module(request: Request) -> bool:
    """有工作追蹤模組（或管理員）才看得到會洩露私帳金額的衍生值（建議預算）。"""
    return payload_grants(_extract_token(request) or {}, "timesheets")


async def _ts_or_bound(request: Request) -> Optional[str]:
    """唯讀端點放寬（docs/JOURNAL_WORKLOG_PLAN.md §10／§11）：timesheets 模組 **或** 登入＋綁定人員檔案
    （員工頁的專案查詢、工作階段下拉、專案下拉）。只給讀；寫入端點（改預算等）守衛不變。
    回傳：有 timesheets 模組 → None（整份）；靠綁定人員進來的 → 該員姓名（端點要縮到「本人」時用）。
    🔴 這支不碰私帳 wall：_require_mine_admin 守的端點（/projects 私帳案清單、/summary）不走這裡。"""
    if payload_grants(check_logged_in(request), "timesheets"):   # 有工作追蹤（或管理員）→ 整份；匿名在這裡 401
        return None
    ident = await resolve_current_staff(request)
    if ident["staff"] is None:
        raise HTTPException(status_code=403, detail="權限不足（需要工作追蹤模組，或帳號綁定人員檔案）")
    return ident["staff"].name


# 員工頁（/my.html）的鑰匙是 me_*，工作追蹤分頁是 timesheets；「我的一天」兩邊都要能用，
# 綁定人員檔案的 409 原句仍只在 core.identity.require_bound_staff。


async def _mine_ident(request: Request) -> dict:
    """「我的一天／今天的專案紀錄／我的一週」的列：總開關（me_today_zone）＋「今天的專案紀錄」或「我的一週」任一把
    （我的一週的卡就是格子的列，兩邊都要能讀寫）＋綁定人員檔案；timesheets 模組恆過。
    （owner 2026-09-08：一顆功能一把、整塊一個總開關；me_profile 只開基本資料卡）"""
    from core.identity import require_zone_staff
    return await require_zone_staff(request, "me_worklog", "me_week_plan")


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
        _CANDS_CACHE["val"] = None       # 工時變了，類似專案候選重算
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
# 「我的一天」的讀改刪再加「綁定人員檔案」（core.identity.require_bound_staff）。

# 看板計算搬到 services.timesheet_self.board_days（/board 與 /me/team_week 同一份）


@router.get("/board")
async def day_board(request: Request, date: str = "", days: int = 1):
    """每日看板：從 date 起 days 天（1＝當天、7＝週模式），每天每個人做了什麼（實際＋計畫）。
    只顯示有列的人；不排名、不標紅（主管層另做）。"""
    check_admin_or_module(request, "timesheets")
    days = max(1, min(int(days or 1), 14))
    d0 = _day_or_422(date)
    factory = db_factory_or_503()
    async with factory() as session:
        out_days = await board_days(session, d0, days)
        # 管理視角「今天還沒填」（docs/WORK_TRACKING_V2_PLAN.md §4-3）：在職／合夥而且那天沒有任何實際或草稿列的人
        # （只有計畫卡不算填了）。週末不點名。兼職不進來（他們不是每天要填）。
        from core.hr_logic import active_staff_where
        active = [n for (n,) in (await session.execute(select(CrmStaff.name).where(active_staff_where()))).all() if n]
    for day in out_days:
        weekend = datetime.fromisoformat(day["date"]).weekday() >= 5
        filled = {p["name"] for p in day["people"] if any(it.get("status") != "plan" for it in p["items"])}
        day["absent"] = [] if weekend else sorted(n for n in active if n not in filled)
    return {"from": d0.date().isoformat(), "days": days, "items": out_days}


@router.get("/options")
async def timesheet_options(request: Request):
    """格子用的字彙：工作分類 ＋ 每個分類自己的階段（只回 active；編輯器要含停用的走 /crm/work-stages/nodes）。
    守衛放寬到綁定人員（員工頁的專案紀錄格子也要它）。"""
    await _ts_or_bound(request)
    factory = db_factory_or_503()
    async with factory() as session:
        nodes = (await session.execute(select(WorkStageNode))).scalars().all()
    return {"work_types": list(WORK_TYPES), "stages": stages_by_category(nodes)}


# ── 兼職排班（owner 2026-09-08）：有「兼職排班」鑰匙的正職（在職／合夥）幫狀態是「兼職」的人排「我的一週」的卡 ──
# 卡＝他的一列工時（status=plan、時數 0、planned_by＝排的人）。只碰計畫列、時數不收；兼職自己那邊完全不變
# （卡出現在他的格子、他填時數／挪／刪都照舊）。刻意不走 own-scope 的 /mine/*（那邊絕不收 client 給的 staff_id），
# 另開這一組、守衛明講「誰能幫誰」。

_PARTTIME = "兼職"


async def _plan_for_ident(request: Request, session, staff_id: str) -> dict:
    """守衛＋解析：回 {"username": 排的人, "target": CrmStaff, "ident": 給 timesheet_self 用的 own-scope dict}。
    管理員／工作追蹤模組 → 任何人；否則「綁定＋me_plan_parttime＋本人是在職／合夥」且「對方是兼職」。"""
    from core.hr_logic import is_active_staff
    from core.identity import require_bound_staff
    payload = check_logged_in(request)
    who = payload.get("username") or payload.get("sub") or ""
    full = payload_grants(payload, "timesheets")
    target = await session.get(CrmStaff, staff_id)
    if target is None:
        raise HTTPException(status_code=404, detail="找不到這個人員")
    if not full:
        me = await require_bound_staff(request, "me_plan_parttime")
        if not is_active_staff(getattr(me["staff"], "status", None)):
            raise HTTPException(status_code=403, detail="只有在職／合夥可以幫兼職排班")
        if (target.status or "").strip() != _PARTTIME:
            raise HTTPException(status_code=403, detail="只能幫狀態是「兼職」的人排")
    return {"username": who, "target": target, "ident": {"staff_id": target.id, "staff": target, "username": who}}


async def _plan_row_of(session, target, row_id: str):
    """對方的**計畫列**（status=plan）才給改／刪；實際紀錄與別人的列一律 403。"""
    from services.timesheet_self import get_row
    r = await get_row(session, row_id)
    if r.staff_id != target.id or (r.status or "") != "plan":
        raise HTTPException(status_code=403, detail="只能改這個人的計畫列（有時數的紀錄不能動）")
    return r


@router.get("/plan-for/targets")
async def plan_for_targets(request: Request):
    """可以幫誰排：狀態是「兼職」的人員（管理員／工作追蹤模組看全部在職的人也行，但視窗只列兼職）。"""
    from core.identity import require_bound_staff
    from core.hr_logic import is_active_staff
    if not payload_grants(check_logged_in(request), "timesheets"):
        me = await require_bound_staff(request, "me_plan_parttime")
        if not is_active_staff(getattr(me["staff"], "status", None)):   # 跟 _plan_for_ident 同一道：兼職／離職不能列名單
            raise HTTPException(status_code=403, detail="只有在職／合夥可以幫兼職排班")
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(CrmStaff.id, CrmStaff.name).where(CrmStaff.status == _PARTTIME).order_by(CrmStaff.name))).all()
    return {"targets": [{"id": i, "name": n} for i, n in rows]}


@router.get("/plan-for/{staff_id}/rows")
async def plan_for_rows(staff_id: str, request: Request, from_: str = Query("", alias="from"), to: str = ""):
    """對方那一週的列（實際＋計畫都回，視窗只讓動計畫列）。"""
    from services.timesheet_self import list_rows
    d0 = _day_or_422(from_)
    d1 = _day_or_422(to) + timedelta(days=1)
    factory = db_factory_or_503()
    async with factory() as session:
        ctx = await _plan_for_ident(request, session, staff_id)
        items = await list_rows(session, ctx["ident"], d0, d1)
    return {"staff_id": staff_id, "staff_name": ctx["target"].name, "items": items}


@router.post("/plan-for/{staff_id}/rows")
async def plan_for_add(staff_id: str, body: MeTimesheetBatch, request: Request):
    """幫對方加卡：一律計畫列（plan=True、時數不收），planned_by＝排的人；貼一則 Chat（best-effort）。"""
    from db.models import Timesheet
    from services.timesheet_self import add_rows
    factory = db_factory_or_503()
    async with factory() as session:
        ctx = await _plan_for_ident(request, session, staff_id)
        for r in body.rows:
            r.plan = True
            r.hours = None
            r.planned_hours = None
        _CANDS_CACHE["val"] = None
        result = await add_rows(session, ctx["ident"], body.rows)
        for rid in result.get("ids") or []:
            obj = await session.get(Timesheet, rid)
            if obj is not None:
                obj.planned_by = ctx["username"] or None
        await session.commit()
    days = sorted({r.work_date for r in body.rows})
    try:
        from notifier import notify_tab_async
        await notify_tab_async("plan_parttime", planner=ctx["username"] or "?", staff_name=ctx["target"].name,
                               count=len(body.rows), days="、".join(days))
    except Exception:
        pass
    return result


@router.put("/plan-for/{staff_id}/{row_id}")
async def plan_for_update(staff_id: str, row_id: str, body: MeTimesheetUpdate, request: Request):
    """改對方的計畫卡（內容／分類／階段／挪日期）。時數不收 —— 那是他自己在格子裡填的。"""
    from services.timesheet_self import update_row
    factory = db_factory_or_503()
    async with factory() as session:
        ctx = await _plan_for_ident(request, session, staff_id)
        await _plan_row_of(session, ctx["target"], row_id)
        body.hours = None
        body.planned_hours = None
        body.plan = True
        _CANDS_CACHE["val"] = None
        return await update_row(session, ctx["ident"], row_id, body)


@router.delete("/plan-for/{staff_id}/{row_id}")
async def plan_for_delete(staff_id: str, row_id: str, request: Request):
    from services.timesheet_self import delete_row
    factory = db_factory_or_503()
    async with factory() as session:
        ctx = await _plan_for_ident(request, session, staff_id)
        await _plan_row_of(session, ctx["target"], row_id)
        _CANDS_CACHE["val"] = None
        return await delete_row(session, ctx["ident"], row_id)


@router.get("/mine")
async def my_day(request: Request, date: str = ""):
    """我的一天：本人該日的工作項（實際＋計畫）＋ 計畫／實際合計 ＋ 昨天的列（供「複製昨天」）。
    合計兩欄留給工作追蹤分頁；員工頁（/my.html）不畫它們（owner 鐵則：不做個人合計卡）。"""
    ident = await _mine_ident(request)
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


@router.get("/mine/incomplete")
async def my_incomplete_days(request: Request, days: int = Query(30, ge=1, le=120)):
    """近 N 天「存了草稿但還沒填時數」的日期（owner 2026-09-07：今天那條提醒「日期 專案紀錄未完成」）。
    路徑要排在 /mine/{row_id} 前面（不然 incomplete 會被當成 row_id）。"""
    from core.hr_logic import PENDING_STATUS
    from db.models import Timesheet
    from services.timesheet_self import own_filter
    from core.hr_logic import tw_day
    ident = await _mine_ident(request)
    d0 = datetime.now(_TW).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet.work_date, safunc.count()).where(own_filter(ident))
            .where(Timesheet.status == PENDING_STATUS).where(Timesheet.work_date >= d0)
            .group_by(Timesheet.work_date).order_by(Timesheet.work_date))).all()
    return {"days": [{"date": tw_day(d).isoformat(), "count": int(n or 0)} for d, n in rows]}   # 台北日期（UTC 午夜存的列 .date() 會少一天）


@router.post("/mine/merge")
async def my_merge_day(body: TimesheetMergeRequest, request: Request):
    """合併同案（owner 2026-09-07）：同案同分類同階段的列併成一列。dry_run 回預覽（哪幾組、各幾列幾小時）；
    正式跑併列＋記合併紀錄（可復原）。規則在 core.hr_logic.merge_plan，I/O 在 services.timesheet_self.merge_day。"""
    from services.timesheet_self import merge_day
    ident = await _mine_ident(request)
    d0 = _day_or_422(body.date)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await merge_day(session, ident, d0, dry_run=body.dry_run)


@router.post("/mine/merge/undo")
async def my_merge_undo(body: TimesheetMergeUndo, request: Request):
    """復原一次合併：被併掉的列照原 id 放回去、本體還原（合併後對它的修改會被蓋掉）。"""
    from services.timesheet_self import undo_merge
    ident = await _mine_ident(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await undo_merge(session, ident, body.log_id)


@router.get("/mine/merge/last")
async def my_last_merge(request: Request, date: str = ""):
    """那一天最近一筆還沒復原的合併（前端用來決定「復原合併」鈕出不出現）。"""
    from services.timesheet_self import last_merge
    ident = await _mine_ident(request)
    d0 = _day_or_422(date)
    factory = db_factory_or_503()
    async with factory() as session:
        return {"last": await last_merge(session, ident, d0)}


@router.get("/mine/rows")
async def my_rows(request: Request, from_: str = Query("", alias="from"), to: str = "", q: str = "",
                  project_id: str = "", stage_id: str = ""):
    """查自己的紀錄（§2-A11）：from／to（YYYY-MM-DD，含；預設近 30 天）、q（內容／備註／案名）、
    project_id、stage_id；**只列不算**，最多 500 列。"""
    ident = await _mine_ident(request)
    for label, raw in (("to", to), ("from", from_)):
        if (raw or "").strip() and parse_ymd(raw) is None:
            raise HTTPException(status_code=422, detail=f"{label} 日期格式錯誤：{raw}")
    to_dt = midnight_of(parse_ymd(to) or datetime.now())
    from_dt = parse_ymd(from_) or (to_dt - timedelta(days=29))
    if from_dt > to_dt:
        raise HTTPException(status_code=422, detail="from 不能晚於 to")
    factory = db_factory_or_503()
    async with factory() as session:
        items = await search_rows(session, ident, from_dt, to_dt + timedelta(days=1),
                                  q=q, project_id=project_id, stage_id=stage_id, limit=500)
    return {"from": from_dt.date().isoformat(), "to": to_dt.date().isoformat(), "items": items}


@router.post("/mine/rows")
async def my_add_rows(body: MeTimesheetBatch, request: Request):
    ident = await _mine_ident(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await add_rows(session, ident, body.rows)


@router.put("/mine/{row_id}")
async def my_update_row(row_id: str, body: MeTimesheetUpdate, request: Request):
    """改自己的一列（含「完成」：把 hours 填上，計畫列就變實際）。"""
    ident = await _mine_ident(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await update_row(session, ident, row_id, body)


@router.delete("/mine/{row_id}")
async def my_delete_row(row_id: str, request: Request):
    ident = await _mine_ident(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await delete_row(session, ident, row_id)


# ── 總表（像發票總表那樣一列一列看；管理員逐列調細節與備註）──────────────────

@router.get("/rows")
async def ledger_rows(request: Request, month: str = "", to: str = "", date: str = "", from_: str = Query("", alias="from"),
                      to_day: str = Query("", alias="to_day"), staff_id: str = ""):
    """month（起）～ to（迄，含；最多 12 個月）的所有列（每人每案每項）。篩選（人／案／關鍵字／來源）
    在前端做。editable 只給管理員（一般成員看得到、改不了）。

    管理視角的「看誰的」（docs/WORK_TRACKING_V2_PLAN.md §4-1／4-4，2026-09-12）多三種切法：`date`（一天）、
    `from`＋`to_day`（一段日期，最多 62 天；`to` 已被月份佔走）、`staff_id`（只看一個人）。給了 date 或 from 就不看 month。
    列的 `editable` 跟整包一樣只給管理員（PUT／DELETE /rows 是 check_admin）—— 格子照這個欄位決定畫成可改還是唯讀。"""
    is_admin = payload_grants(check_admin_or_module(request, "timesheets"))
    if (date or "").strip() or (from_ or "").strip():
        d0 = _day_or_422(date or from_)
        d1 = d0 + timedelta(days=1) if (date or "").strip() else _day_or_422(to_day or from_) + timedelta(days=1)
        if d1 <= d0:
            raise HTTPException(status_code=422, detail="迄日不能早於起日")
        if (d1 - d0).days > 62:
            raise HTTPException(status_code=422, detail="區間最多 62 天")
        m0, m1, label = d0, d1, {"date": d0.date().isoformat(), "to_day": (d1 - timedelta(days=1)).date().isoformat()}
    else:
        m0, m1 = month_or_422(month)
        t0 = None
        if to:
            t0, t1 = month_or_422(to)
            if t0 < m0:
                raise HTTPException(status_code=422, detail="迄月不能早於起月")
            if (t0.year - m0.year) * 12 + (t0.month - m0.month) >= 12:
                raise HTTPException(status_code=422, detail="區間最多 12 個月")
            m1 = t1
        label = {"month": month_key(m0), "to": month_key(t0) if to else ""}
    factory = db_factory_or_503()
    async with factory() as session:
        q = select(Timesheet).where(Timesheet.work_date >= m0).where(Timesheet.work_date < m1)
        if (staff_id or "").strip():
            q = q.where(Timesheet.staff_id == staff_id.strip())
        rows = (await session.execute(q.order_by(Timesheet.work_date.desc(), Timesheet.staff_name, Timesheet.created_at))).scalars().all()
    return {**label, "editable": is_admin,
            "items": [{**ts_dict(r, with_note=is_admin), "editable": is_admin} for r in rows], "work_types": list(WORK_TYPES)}


@router.get("/people")
async def timesheet_people(request: Request):
    """管理視角「看誰的」切換器：在職／合夥／兼職人員（id、name、status），照團隊的一週的順序。
    刻意不重用 /crm/staff（那支要 crm_staff 鑰匙、而且帶薪資欄位）。"""
    check_admin_or_module(request, "timesheets")
    from core.hr_logic import staff_rank, is_active_staff
    factory = db_factory_or_503()
    async with factory() as session:
        staff = (await session.execute(select(CrmStaff.id, CrmStaff.name, CrmStaff.status))).all()
    people = [{"id": sid, "name": name, "status": (st or "").strip()} for sid, name, st in staff
              if name and (is_active_staff(st) or (st or "").strip() == "兼職")]
    people.sort(key=lambda p: (staff_rank(p["status"]), p["name"]))
    return {"people": people}


@router.post("/rows/batch")
async def ledger_batch(body: TimesheetRowsBatch, request: Request):
    """總表批次調整（管理員）：勾選的列一次改專案／分類／備註／管理員備註。"""
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await admin_batch_update(session, body.ids, body.model_dump(exclude_unset=True, exclude={"ids"}),
                                        current_username(request))


@router.put("/rows/{row_id}")
async def ledger_update_row(row_id: str, body: TimesheetRowAdminUpdate, request: Request):
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await admin_update_row(session, row_id, body, current_username(request))


@router.get("/conflicts")
async def ledger_conflicts(request: Request):
    """待決的衝突（Sheet 與總表改過的同一列內容不同）；管理員。"""
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return {"items": await list_conflicts(session)}


@router.post("/conflicts/{cid}/resolve")
async def ledger_resolve_conflict(cid: str, body: TimesheetConflictResolve, request: Request):
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await resolve_conflict(session, cid, body.choice, current_username(request))


@router.delete("/rows/{row_id}")
async def ledger_delete_row(row_id: str, request: Request):
    check_admin(request)
    factory = db_factory_or_503()
    async with factory() as session:
        _CANDS_CACHE["val"] = None
        return await admin_delete_row(session, row_id, current_username(request))


# ── 專案檔案頁／類似專案並排／人員檔案頁／改預算（P2）──────────────────────────

_CANDS_CACHE: dict = {"at": 0.0, "val": None}


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


# ── 儀表板／匯出（P3）────────────────────────────────────────────────────────

@router.get("/dashboard")
async def dashboard(request: Request):
    """大家的四格（今日在做什麼、本週全體、本月分類組成、burn 前五）＋
    主管層（負載排名、昨天漏填、有計畫沒結果）—— 主管層只給管理員（不給全員比較）。"""
    payload = check_admin_or_module(request, "timesheets")
    is_admin = payload_grants(payload)          # 不帶模組鑰匙＝純管理員判定
    now = datetime.now()
    today = now.date()
    week_mon = midnight_of(week_start_of(now))      # 週一（規則只有 core.journal_logic.week_start_of 一份）
    m0, _ = month_span("")
    since = min(week_mon, m0) - timedelta(days=30)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet.staff_name, Timesheet.work_date, Timesheet.project_name,
                   Timesheet.work_type, Timesheet.hours, Timesheet.status)
            .where(Timesheet.work_date >= since))).all()
        burn = (await burn_rows(session))[:5]
    # burn 前五跟 /summary 同一份列：沒有私帳 scope 的人只拿 SUMMARY_PUBLIC_KEYS（burn_rows 2026-09-12 起帶
    # 合約未稅／預期毛利／人力成本；建議預算也是從毛利算出來的）—— 這裡原本整列直接回，是個洞
    from core.money import viewer_has_mine_scope
    if not viewer_has_mine_scope(request):
        burn = [{k: it.get(k) for k in SUMMARY_PUBLIC_KEYS} for it in burn]
    data = [(n, tw_day(d), p, wt, float(h or 0), st) for n, d, p, wt, h, st in rows]
    today_rows = [x for x in data if x[1] == today]
    week = hours_rollup([(n, d, p, h) for n, d, p, _wt, h, _st in data if d and d >= week_mon.date()],
                        now.year, now.month)
    out = {
        "today": {"people": len({n for n, *_ in today_rows}), "items": len(today_rows),
                  "hours": round(sum(x[4] for x in today_rows), 1)},
        "week": {"total": week["total"], "people": len(week["people"]),
                 "reference_per_person": 5 * HOURS_PER_WORKDAY, "from": week_mon.date().isoformat()},
        "month_composition": type_composition((wt, h) for _n, d, _p, wt, h, _st in data if d and d >= m0.date()),
        "burn_top": burn,
    }
    if is_admin:
        yday = prev_workday(today)
        ndh = [(n, d, h) for n, d, _p, _wt, h, _st in data]      # active_fillers／fillers_on 的共同輸入
        out["manager"] = {
            "load": [{"name": p["name"], "hours": p["total"]} for p in week["people"]],
            "missing_yesterday": {"date": yday.isoformat(),
                                  "names": missing_fillers(active_fillers(ndh, today), fillers_on(ndh, yday))},
            "plans_open": sorted({n for n, d, _p, _wt, _h, st in data if st == "plan" and d and d < today}),
        }
    return out


@router.get("/export.csv")
async def export_csv(request: Request, month: str = "", project: str = "", project_id: str = ""):
    """匯出 CSV（給會計／結算）：month=YYYY-MM、project=Sheet 案名或 project_id=整個案，至少一個。"""
    check_admin_or_module(request, "timesheets")
    if not month and not project and not project_id:
        raise HTTPException(status_code=422, detail="month、project 或 project_id 至少一個")
    q = select(Timesheet).order_by(Timesheet.work_date, Timesheet.staff_name)
    if month:
        m0, m1 = month_or_422(month)
        q = q.where(Timesheet.work_date >= m0).where(Timesheet.work_date < m1)
    if project:
        q = q.where(Timesheet.project_name == project.strip())
    if project_id:
        q = q.where(Timesheet.project_id == project_id.strip())
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(q)).scalars().all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["日期", "人員", "專案", "分類", "內容", "備註", "計畫小時", "實際小時", "來源", "狀態"])
    for r in rows:
        it = ts_dict(r)
        w.writerow([it["date"], it["staff_name"], it["project_name"], it["work_type"], it["task_note"], it["remark"],
                    it["planned_hours"] if it["planned_hours"] is not None else "", it["hours"], it["source"], it["status"]])
    fname = f"timesheets_{month or project or project_id}.csv"
    return Response(content="﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


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


# ── 手填工時（與 Sheet 同步共存；N-hr 人事管理 v1）─────────────────────

@router.get("/project_options")
async def get_project_options(request: Request):
    """專案下拉（補登 grid／我的一天／總表／員工頁／手機工作紀錄）：timesheets 模組拿整份；綁定人員檔案的員工也給，
    多帶「本人最近填過的」。守衛同 /options、/project（_ts_or_bound）——owner 2026-09-07「在職員工要能完整使用
    今天與這週」：以前只認 timesheets／me_finance 兩把鑰匙，只有 me_profile 的員工（連婕妤）拿到 403、前端吞掉就變空清單。
    只有 me_finance、沒綁人員檔案的帳號照舊拿整份（原本的行為，不因放寬而收回）。"""
    try:
        staff_name = await _ts_or_bound(request)
    except HTTPException as e:
        if e.status_code != 403 or not payload_grants(_extract_token(request) or {}, "me_finance"):
            raise
        staff_name = None
    factory = db_factory_or_503()
    async with factory() as session:
        return {"projects": await project_options(session, staff_name)}


@router.post("/manual")
async def add_manual_rows(body: TimesheetManualRequest, request: Request):
    """管理端批次手填（指定人員；員工自助走 /mine/rows 或 /api/v1/me/timesheets/batch）。"""
    check_admin_or_module(request, "timesheets")
    factory = db_factory_or_503()
    async with factory() as session:
        staff = await session.get(CrmStaff, body.staff_id)
        if staff is None:
            raise HTTPException(status_code=404, detail="人員不存在")
        _CANDS_CACHE["val"] = None
        # 跟員工自填同一支 add_rows（階段先驗 422、鏡射 stage_name、待辦 done）—— 管理視角替人填的列
        # 之前少了階段，格子存了、階段欄卻是空的（2026-09-12）。add_rows 自己 commit。
        ident = {"staff_id": staff.id, "staff": staff, "username": current_username(request)}
        return await add_rows(session, ident, body.rows)


@router.get("/by_staff")
async def hours_by_staff(request: Request, month: str = ""):
    """人員月視圖：每人 × 每專案 時數彙總（month=YYYY-MM，預設本月）。"""
    check_admin_or_module(request, "timesheets")
    m0, m1 = month_or_422(month)
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
    return {"month": month_key(m0), "staff": staff_list,
            "total_hours": round(sum(e["total_hours"] for e in staff_list), 1)}


@router.get("/summary")
async def burn_summary(request: Request):
    """每專案 burn 摘要：已投入時數 / 預算 / 消耗率。未對映專案以名稱聚合列出。

    權限稽核第二批（2026-09-08）：timesheets 分頁鑰匙可讀（之前是 check_admin＋私帳 wall，非 Lv3 開
    「專案」視圖第一支就 403）。沒有私帳 scope（finance_mine 指名制，Lv3 不隱含）的人拿的是
    `_redact_summary` 抹過的那份：專案列只留 SUMMARY_PUBLIC_KEYS（同 /me/projects_burn），未對映列
    不帶撞案候選／相似建議（那是私帳案名＋客戶）。
    """
    check_admin_or_module(request, "timesheets")
    factory = db_factory_or_503()
    async with factory() as session:
        items = await burn_rows(session)     # 專案那一半與 /dashboard 共用（services）
        unmatched = (await session.execute(
            select(Timesheet.project_name, safunc.sum(Timesheet.hours), safunc.count(Timesheet.id))
            .where(Timesheet.project_id.is_(None))
            .group_by(Timesheet.project_name)
            .order_by(safunc.sum(Timesheet.hours).desc())
        )).all()
        # 未對映的每一個名字：為什麼沒對到（撞案／找不到／內部桶）。撞案附候選、
        # 找不到附相似建議 —— owner 在 tab 上直接指定，不用回頭翻報告。
        lk = await load_project_lookup(session)
    out_unmatched = [{"project_name": (n or "") or "(空白)", "hours_used": round(h or 0, 1), "rows": c,
                      **explain_miss(n or "", lk)}       # candidates 已是 {id,name,client}
                     for n, h, c in unmatched]
    # 已對映＋未對映正好是整張表，不用再 count 一次
    from core.finance_logic import project_type_vocab
    out = {"projects": items, "unmatched": out_unmatched,
           "project_types": project_type_vocab(i["project_type"] for i in items),
           "total_rows": sum(i["rows"] for i in items) + sum(c for _n, _h, c in unmatched)}
    from core.money import viewer_has_mine_scope
    return out if viewer_has_mine_scope(request) else _redact_summary(out)


#: /summary 對沒有私帳 scope 的人只回這些鍵（＝ /me/projects_burn 的唯讀面）：沒有 suggested_hours
#: （從私帳預期毛利倒算的，等於間接揭露錢）。
SUMMARY_PUBLIC_KEYS = ("project_id", "project_name", "client", "status", "project_type", "hours_used",
                       "budget_hours", "remaining", "pct", "rows", "last_entry", "stale")
#: 未對映列留給非私帳讀者的鍵：Sheet 案名與時數（團隊頁本來就看得到），不帶 candidates／suggestions。
UNMATCHED_PUBLIC_KEYS = ("project_name", "hours_used", "rows", "reason")


def _redact_summary(out: dict) -> dict:
    """抹掉 /summary 裡跟私帳有關的欄位（純函式，給 burn_summary 用）。"""
    out["projects"] = [{k: it.get(k) for k in SUMMARY_PUBLIC_KEYS} for it in out["projects"]]
    out["unmatched"] = [{k: u.get(k) for k in UNMATCHED_PUBLIC_KEYS} for u in out["unmatched"]]
    return out


@router.get("/recent")
async def recent_rows(request: Request, limit: int = 50):
    """最近同步進來的列（抽查用；timesheets 分頁鑰匙可讀）。"""
    check_admin_or_module(request, "timesheets")
    factory = db_factory_or_503()
    limit = max(1, min(limit, 200))
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet).order_by(Timesheet.created_at.desc()).limit(limit)
        )).scalars().all()
    return {"rows": [ts_dict(r) for r in rows]}
