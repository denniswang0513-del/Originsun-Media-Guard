"""api_me.py — 個人工作台 API（N0 個人帳號化）。

供獨立頁 /my.html 使用：登入者只看見「自己的」資料 —— own-scope 一律經
core.identity.resolve_current_staff（token → users.staff_id → crm_staff），
絕不接受 client 傳入的 staff_id / username。

權限 = 細粒度 key（owner 在「使用者管理」逐帳號勾選控制哪些卡開放）：
    me_projects  我的專案（派工）        — 鍵 crm_project_staff.staff_id（乾淨）
    me_profile   我的個人資料/履歷        — 鍵 crm_staff.id（乾淨；PUT 白名單）
    me_todos     我的待辦（公布欄）       — 鍵 username（不需綁定人員檔案）
    me_finance   我的工時+請款（摘要）    — ⚠ name-match（Timesheet.staff_name /
                 CrmPaymentRequest.payee_name 是字串），單人 owner 階段安全；
                 全員推行前必須先回填 staff_id（見 ROADMAP N2）。
    me_leave     我的請假（N-hr H2）      — 鍵 crm_staff.id；自助送單/撤回 +
                 特休餘額；管理端簽核在 routers/api_hr.py。

管理員（Lv3）經 grant_admin_all_modules 自動擁有全部 key。
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from sqlalchemy import func, select  # type: ignore

from core.auth import ME_MODULE_KEYS, check_admin_or_module, grant_admin_all_modules, payload_grants
from core.db_guard import db_factory_or_503
from core.hr_logic import (midnight_of, budget_burn, day_iso, hours_rollup,
                           month_key, month_span, months_back, parse_ymd, project_metrics, tw_day)
from core.identity import require_bound_staff, require_zone_staff, resolve_current_staff
from core.hr_logic import STAFF_ACTIVE, staff_rank
from core.journal_logic import shell_status, week_start_of
from core.leave_logic import cancel_mode, in_crew, vocab as leave_vocab
from core.schemas import (LeaveCancel, MeLeaveCreate, MeLeavePreview, MeProfileUpdate, MeTimesheetBatch,
                          MeTimesheetUpdate, MeTodoUpdate)
from core.shoot_logic import CANCELLED as SHOOT_CANCELLED
from db.models import (BulletinItem, Client, CrmPaymentRequest, CrmProject,
                       CrmProjectStaff, CrmShoot, CrmStaff, HrLeaveRequest, PreprodLocation, Timesheet, WorkJournal)
from services import leave_service
from services.timesheet_lookup import budgets_for
from services.timesheet_self import (add_rows, delete_row, list_rows, metrics_input, month_or_422,
                                     own_filter, rows_by_month, ts_dict, update_row)
from routers.api_shoots import _crew_list
from services.timesheet_self import board_days

router = APIRouter(prefix="/api/v1/me", tags=["me"])


# 提案企劃卡的閘門 — 對齊 routers/api_proposals._check 的守衛集合（owner 2026-08-11
# 拍板「權限全通」：打得開 /project.html 的人，工作台就有入口卡）。
PROPOSAL_PLAN_KEYS = ("preprod_proposals", "preprod_plan", "crm_projects")


def _profile_dict(s) -> dict:
    """crm_staff 安全子集 — 不含費率/身分證/銀行/website_* 欄位。"""
    return {
        "name": s.name, "role": s.role or "",
        "phone": s.phone or "", "email": s.email or "",
        "address": s.address or "",
        "emergency_contact": s.emergency_contact or "",
        "portfolio_url": s.portfolio_url or "",
        "photo_url": s.photo_url or "",
        "bio": s.bio or "",
        "skills": s.skills or [], "education": s.education or [],
        "experience": s.experience or [], "awards": s.awards or [],
        "employment_type": s.employment_type or "",
        "hire_date": day_iso(s.hire_date) or "",
        "status": s.status or STAFF_ACTIVE,
    }


async def _todos_for(session, username: str) -> list:
    """我的待辦（公布欄「與我有關」且未完成）—— workspace 與 /today 同一份。"""
    rows = (await session.execute(
        select(BulletinItem)
        .where(BulletinItem.mine_filter(username))
        .where(BulletinItem.status != "done")
        .order_by(BulletinItem.pinned.desc(), BulletinItem.sort_order,
                  BulletinItem.created_at)
    )).scalars().all()
    return [{
        "id": o.id, "title": o.title, "note": o.note or "",
        "status": o.status, "priority": o.priority,
        "pinned": bool(o.pinned),
        "assigned_to_me": o.assignee_username == username,
        "created_at": o.created_at.isoformat() if o.created_at else None,
    } for o in rows]


@router.get("/workspace")
async def my_workspace(request: Request):
    """個人工作台 bundle — 只回帳號有權限的區塊；未綁定人員檔案時
    人員鍵區塊（profile/projects/finance）回空並帶 bound=False。"""
    payload = check_admin_or_module(request, *ME_MODULE_KEYS, "journal", "media_log",
                                    *PROPOSAL_PLAN_KEYS)
    mods = grant_admin_all_modules(payload.get("access_level"), payload.get("modules") or [])
    allowed = [k for k in ME_MODULE_KEYS if k in mods]
    # 週誌卡宣告式閘門 — my.html 依 allowed 決定要不要抓 /api/v1/journal/mine
    # （資料不進 bundle；避免前端用「打端點看 403」探測權限的偏差模式）
    if "journal" in mods:
        allowed.append("journal")
    # 影像紀錄卡同款宣告式閘門 — my.html 自行打 /crm/media-log/overview（唯讀瀏覽）
    if "media_log" in mods:
        allowed.append("media_log")
    # 提案企劃卡 — 純入口連結（/project.html），資料不進 bundle
    if any(k in mods for k in PROPOSAL_PLAN_KEYS):
        allowed.append("proposal_plan")
    ident = await resolve_current_staff(request)
    staff = ident["staff"]
    # token 裡簽死的 modules 跟帳號現在的不一樣（管理員改了／開機回填補了鑰匙）→ 順手回一顆新 token，
    # my.html 換掉再抓一次；不然整區「今天與這週」在 7 天 token 到期前都看不到（2026-09-08 劉禮瑜）
    from routers.api_auth import _find_user, refreshed_token_if_drifted
    fresh = await refreshed_token_if_drifted(payload, await _find_user(payload.get("sub") or ""))
    out = {
        "username": ident["username"],
        "staff_id": ident["staff_id"],
        **({"token": fresh} if fresh else {}),
        "bound": staff is not None,
        "allowed": allowed,
        # 具人事管理權限（hr_leave 模組或 admin）→ /my.html 頂欄顯示「人事管理」
        # 深連結（官網 STAFF 入口一路通到內部簽核頁）
        "hr_manager": payload_grants(payload, "hr_leave"),   # 捆鑰匙由 payload_grants 展開
    }
    factory = db_factory_or_503()
    async with factory() as session:
        if "me_profile" in allowed and staff is not None:
            out["profile"] = _profile_dict(staff)

        if "me_projects" in allowed and staff is not None:
            rows = (await session.execute(
                select(CrmProjectStaff, CrmProject.name, CrmProject.status,
                       Client.short_name)
                .outerjoin(CrmProject, CrmProject.id == CrmProjectStaff.project_id)
                .outerjoin(Client, Client.id == CrmProject.client_id)
                .where(CrmProjectStaff.staff_id == ident["staff_id"])
                .order_by(CrmProjectStaff.id.desc())
            )).all()
            out["projects"] = [{
                "project_id": r[0].project_id,
                "project_name": r[1] or "", "project_status": r[2] or "",
                "client_name": r[3] or "",
                "role_in_project": r[0].role_in_project or "",
                "phase": r[0].phase or "",
                "days": r[0].days, "actual_days": r[0].actual_days,
                "cost": r[0].cost, "payment_status": r[0].payment_status or "",
                "notes": r[0].notes or "",
            } for r in rows]

        if "me_todos" in allowed:
            # 鍵 username — 不需綁定人員檔案也能用
            out["todos"] = await _todos_for(session, ident["username"] or "")

        if "me_finance" in allowed and staff is not None:
            name = staff.name                       # 應付款那段還是認收款人姓名
            ts_rows = (await session.execute(
                select(Timesheet.project_name,
                       func.sum(Timesheet.hours), func.count(Timesheet.id),
                       func.max(Timesheet.work_date))
                .where(own_filter(ident))           # own-scope 同「我的工時」那一條
                .where(Timesheet.hours > 0)         # 只算做過的：「我的一週」往後排的計畫列（0 h）不能算進來，
                .group_by(Timesheet.project_name)   # 不然只排沒做的案會憑空出現，「最後填報」還變成未來日期排到最上面
                .order_by(func.max(Timesheet.work_date).desc())
            )).all()
            month_start = month_span("")[0]
            month_hours = (await session.execute(
                select(func.coalesce(func.sum(Timesheet.hours), 0.0))
                .where(own_filter(ident))
                .where(Timesheet.work_date >= month_start)
            )).scalar() or 0.0
            out["timesheet"] = {
                "total_hours": round(sum(r[1] or 0 for r in ts_rows), 1),
                "month_hours": round(month_hours, 1),
                "by_project": [{
                    "project_name": r[0] or "(空白)",
                    "hours": round(r[1] or 0, 1), "rows": r[2],
                    "last_entry": day_iso(r[3]),
                } for r in ts_rows[:10]],
            }

            _unpaid = CrmPaymentRequest.payment_status != "已付款"
            totals = (await session.execute(
                select(func.coalesce(func.sum(CrmPaymentRequest.amount), 0),
                       func.count(CrmPaymentRequest.id).filter(_unpaid),
                       func.coalesce(func.sum(CrmPaymentRequest.amount).filter(_unpaid), 0))
                .where(CrmPaymentRequest.payee_name == name)
            )).one()
            recent = (await session.execute(
                select(CrmPaymentRequest)
                .where(CrmPaymentRequest.payee_name == name)
                .order_by(CrmPaymentRequest.request_date.desc().nulls_last())
                .limit(5)
            )).scalars().all()
            out["payments"] = {
                "total_amount": int(totals[0] or 0),
                "unpaid_count": int(totals[1] or 0),
                "unpaid_amount": int(totals[2] or 0),
                # 摘要即可 — 不回 payee_id / 銀行資訊
                "recent": [{
                    "date": day_iso(p.request_date) or "",
                    "amount": p.amount or 0, "summary": p.summary or "",
                    "status": p.payment_status or "",
                    "project": p.project_label or "",
                } for p in recent],
            }

        # 假勤那張卡自己打 /me/leave/summary（時數帳）——bundle 這邊原本還算一份舊規則的
        # 「特休 = annual_leave_days − 當年已核准」塞進 out["leave"]，前端沒有任何人讀它。
        # 兩份「還剩多少」的規則遲早會漂開，而且每次開頁白花兩個查詢，所以整段拿掉。
    return out


@router.put("/profile")
async def update_my_profile(body: MeProfileUpdate, request: Request):
    """本人編輯自己的人員檔案 — 僅白名單欄位；target 一律由 token 解析。"""
    ident = await require_bound_staff(request, "me_profile")
    factory = db_factory_or_503()
    # 白名單 = MeProfileUpdate schema 本身（pydantic 已擋掉其他欄位），不另列清單
    data = body.model_dump(exclude_unset=True)
    async with factory() as session:
        from db.models import CrmStaff
        s = await session.get(CrmStaff, ident["staff_id"])
        if s is None:
            raise HTTPException(status_code=404, detail="人員檔案不存在")
        for k, v in data.items():
            if isinstance(v, str):
                v = v.strip() or None
            setattr(s, k, v)
        s.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(s)
        return {"status": "ok", "profile": _profile_dict(s)}


@router.put("/todos/{item_id}")
async def update_my_todo(item_id: str, body: MeTodoUpdate, request: Request):
    """本人待辦改狀態（todo/doing/done）— 只允許動「與我有關」的項目。"""
    payload = check_admin_or_module(request, "me_todos")
    if body.status not in ("todo", "doing", "done"):
        raise HTTPException(status_code=422, detail="status 需為 todo/doing/done")
    username = (payload or {}).get("sub") or ""
    factory = db_factory_or_503()
    async with factory() as session:
        obj = (await session.execute(
            select(BulletinItem).where(BulletinItem.id == item_id)
            .where(BulletinItem.mine_filter(username))
        )).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到項目或非你的待辦")
        obj.status = body.status
        obj.done_at = datetime.now() if body.status == "done" else None
        await session.commit()
    return {"status": "ok"}


# ── 我的假勤（docs/LEAVE_PLAN.md §7.4）──────────────────────────────────────
#
# 規則在 core.leave_logic、查詢在 services.leave_service；這裡只有守衛與 HTTP 形狀。
# summary 任一把 me_* 鑰匙可讀（卡片上的三個數字）；送單／撤回要 me_leave。

@router.get("/leave/summary")
async def my_leave_summary(request: Request):
    """{vocab, balances:{特休:{available,reserved,expiring}, 補休:{…}}, sick:{used_days,cap_days},
    requests:[最近 20 筆（已核准帶 cancel_mode）], pending_count, hire_date, annual_days_by_law}。
    2026-09-08：只認請假那把（原本任一把 me_* 都能拿到自己的餘額 —— 畫面早就藏了，API 跟著收）。"""
    ident = await require_bound_staff(request, "me_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        out = await leave_service.staff_leave_summary(session, ident["staff"])
    out["vocab"] = leave_vocab()
    return out


@router.post("/leave/preview")
async def preview_my_leave(body: MeLeavePreview, request: Request):
    """算時數＋錯誤／警告，不寫入：{hours, days, errors:[{code,msg}], warnings:[{code,msg}], balance}。"""
    ident = await require_bound_staff(request, "me_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        return await leave_service.evaluate(session, ident["staff_id"], ident["staff"].name, body)


@router.post("/leave")
async def apply_my_leave(body: MeLeaveCreate, request: Request):
    """本人送請假單（staff_id 由 token 解析；固定進待審，管理端在人事 tab 簽核）。
    errors 非空 → 422（detail 是逗號串起來的訊息；結構化的請先打 /leave/preview）。"""
    ident = await require_bound_staff(request, "me_leave")
    if not (body.reason or "").strip():
        raise HTTPException(status_code=422, detail="事由必填（規章：提出時簡述理由）")
    factory = db_factory_or_503()
    async with factory() as session:
        holidays = await leave_service.holidays_map(session)
        ev = await leave_service.evaluate(session, ident["staff_id"], ident["staff"].name, body, holidays=holidays)
        if ev["errors"]:
            raise HTTPException(status_code=422, detail="；".join(e["msg"] for e in ev["errors"]))
        hours, part = leave_service.hours_from_body(body, holidays)
        obj = leave_service.build_request(ident["staff_id"], ident["staff"].name, body, hours, part, ident["username"])
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
        result = leave_service.request_dict(obj, holidays)
    result["warnings"] = ev["warnings"]
    # 通知管理者（best-effort；精簡 agent 可能沒帶 notifier）
    try:
        from notifier import notify_tab_async
        await notify_tab_async(
            "leave_request",
            staff_name=result["staff_name"], leave_type=result["leave_type"],
            start=result["start_date"], end=result["end_date"],
            days=f"{result['days']:g}", reason=result["reason"] or "-",
        )
    except Exception:
        pass
    return result


async def _cancel_my_leave(leave_id: str, request: Request, note: str) -> dict:
    """待審→已撤回；已核准依 core.leave_logic.cancel_mode：free→已撤回（釋放 allocations）／
    apply→消假待審（cancel_note 必填）／locked→409。其餘狀態 409。"""
    ident = await require_bound_staff(request, "me_leave")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = (await session.execute(
            select(HrLeaveRequest)
            .where(HrLeaveRequest.id == leave_id)
            .where(HrLeaveRequest.staff_id == ident["staff_id"])
        )).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到這張請假單（或不是你的）")
        holidays = await leave_service.holidays_map(session)
        mode = None
        if obj.status == "待審":
            obj.status = "已撤回"
        elif obj.status == "已核准":
            mode = cancel_mode(obj.start_date, date.today(), holidays)
            if mode == "locked":
                raise HTTPException(status_code=409, detail="颱風假公告當日不可消假（規章）")
            if mode == "free":
                await leave_service.release_allocations(session, obj.id)
                obj.status = "已撤回"
                obj.approved_by = None
                obj.approved_at = None
            else:
                if not note:
                    raise HTTPException(status_code=422, detail="距開始不到兩天，撤回要填說明由主管決定（規章：臨時消假要與主管討論）")
                obj.status = "消假待審"
        else:
            raise HTTPException(status_code=409, detail=f"此單狀態是「{obj.status}」，不能撤回")
        if note:
            obj.cancel_note = note
        await session.commit()
        await session.refresh(obj)
        out = leave_service.request_dict(obj, holidays)
    out["mode"] = mode or "free"
    return out


@router.post("/leave/{leave_id}/cancel")
async def cancel_my_leave(leave_id: str, body: LeaveCancel, request: Request):
    return await _cancel_my_leave(leave_id, request, (body.note or "").strip())


@router.delete("/leave/{leave_id}")
async def cancel_my_leave_legacy(leave_id: str, request: Request):
    """舊分頁的撤回（DELETE）：同 POST /cancel，不再硬刪 —— 待審變已撤回。"""
    out = await _cancel_my_leave(leave_id, request, "")
    return {"deleted": leave_id, **out}


# ── 今天與這週（docs/JOURNAL_WORKLOG_PLAN.md §8–§11；員工頁第一區）──────────────
#
# 守衛＝任何 me_* 鑰匙＋綁定人員檔案（409 原句只在 core.identity.require_bound_staff）。

async def _me_bound(request: Request, *keys: str) -> dict:
    """「今天與這週」那一區的端點：總開關（me_today_zone）＋**那個子視圖自己的鑰匙**＋綁定人員檔案
    （owner 2026-09-08：一顆功能一把、整塊一個總開關，在權限管理逐人開；me_profile 只開基本資料卡）。
    工作追蹤模組（timesheets）本來就整區能用。守衛正本 core.identity.require_zone_staff。"""
    return await require_zone_staff(request, *keys)


async def _shoots_between(session, d0: date, d1: date) -> list:
    """[d0, d1] 有排（未取消）的場次，帶案名與地點字：[{shoot, project_name, location, crew}]。"""
    rows = (await session.execute(
        select(CrmShoot, CrmProject.name)
        .outerjoin(CrmProject, CrmProject.id == CrmShoot.project_id)
        .where(CrmShoot.date <= d1)
        .where(func.coalesce(CrmShoot.end_date, CrmShoot.date) >= d0)
        .where(CrmShoot.status != SHOOT_CANCELLED)
        .order_by(CrmShoot.date, CrmShoot.start_time))).all()
    loc_ids = {s.location_id for s, _ in rows if s.location_id and not (s.location_text or "").strip()}
    loc_names = {}
    if loc_ids:
        loc_names = dict((await session.execute(
            select(PreprodLocation.id, PreprodLocation.name).where(PreprodLocation.id.in_(loc_ids)))).all())
    return [{"shoot": s, "project_name": pname or "",
             "location": (s.location_text or "").strip() or loc_names.get(s.location_id, "") or "",
             "crew": _crew_list(s.crew)} for s, pname in rows]


def _shoot_days(s) -> list:
    """一場拍攝涵蓋的每一天（多日拍攝展開）。"""
    end = s.end_date or s.date
    n = max(0, (end - s.date).days)
    return [s.date + timedelta(days=i) for i in range(n + 1)]


@router.get("/today")
async def my_today(request: Request):
    """今天那一條：我在 crew 的場次、我的待辦、待審請假、上週回顧狀態（none／draft／submitted）。"""
    ident = await _me_bound(request, "me_worklog")      # 今天那條住在「今天的專案紀錄」裡
    today = date.today()
    factory = db_factory_or_503()
    async with factory() as session:
        shoots = [{"id": x["shoot"].id, "title": (x["shoot"].title or "").strip() or x["project_name"],
                   "project_id": x["shoot"].project_id, "project_name": x["project_name"],
                   "location": x["location"], "start_time": x["shoot"].start_time or "",
                   "end_time": x["shoot"].end_time or "", "crew": [c["name"] for c in x["crew"] if c.get("name")]}
                  for x in await _shoots_between(session, today, today)
                  if in_crew(x["crew"], ident["staff_id"], ident["staff"].name)]   # 比 staff_id、退回比姓名（正本 core.leave_logic）
        todos = await _todos_for(session, ident["username"] or "")
        leaves = (await session.execute(
            select(HrLeaveRequest).where(HrLeaveRequest.staff_id == ident["staff_id"])
            .where(HrLeaveRequest.status == "待審")
            .order_by(HrLeaveRequest.start_date))).scalars().all()
        last_week = week_start_of(today) - timedelta(days=7)
        shell = (await session.execute(
            select(WorkJournal).where(WorkJournal.username == (ident["username"] or ""))
            .where(WorkJournal.week_start == last_week))).scalar_one_or_none()
        # 本週里程碑（owner 2026-09-07）：幾個、今天到期哪幾個；失敗不擋今天那條
        try:
            from services.milestone_service import today_summary
            milestones = await today_summary(session, today)
        except Exception:      # noqa: BLE001 — 表還沒建／查詢壞掉都不該讓「今天」整條消失
            milestones = None
    return {
        "date": today.isoformat(), "shoots": shoots, "todos": todos, "milestones": milestones,
        "leave_pending": [{"date_from": day_iso(l.start_date) or "", "date_to": day_iso(l.end_date) or "",
                           "status": l.status} for l in leaves],
        "last_week_journal": shell_status(shell), "last_week_start": last_week.isoformat(),
    }


@router.get("/team_week")
async def team_week(request: Request, start: str = ""):
    """團隊的一週（§11）：人×日格子＝既有看板的週模式（案名＋小時＋內容、計畫淺灰），疊場次與休假。
    people 的計算直接用 services.timesheet_self.board_days（不抄第二份）。"""
    ident = await _me_bound(request, "me_team_week")
    if (start or "").strip() and parse_ymd(start) is None:
        raise HTTPException(status_code=422, detail="start 需為 YYYY-MM-DD")
    week = week_start_of(parse_ymd(start).date() if (start or "").strip() else date.today())
    days = [(week + timedelta(days=i)).isoformat() for i in range(7)]
    d0 = midnight_of(week)
    factory = db_factory_or_503()
    async with factory() as session:
        board = await board_days(session, d0, 7)
        shoot_rows = await _shoots_between(session, week, week + timedelta(days=6))
        leaves = (await session.execute(
            select(HrLeaveRequest).where(HrLeaveRequest.status == "已核准")
            .where(HrLeaveRequest.start_date < d0 + timedelta(days=7))
            .where(HrLeaveRequest.end_date >= d0))).scalars().all()
        # 人員的在職狀態：清單排序用（在職 → 兼職 → 其他；core.hr_logic.staff_rank）
        board_names = {p["name"] for day in board for p in day["people"]}
        staff_st = {n: st for n, st in (await session.execute(
            select(CrmStaff.name, CrmStaff.status).where(CrmStaff.name.in_(list(board_names))))).all()} if board_names else {}
    people: dict = {}
    for day in board:
        for p in day["people"]:
            cells = people.setdefault(p["name"], {})
            # 不吐 project_id：私帳案 id 對任何 me_* 員工都不該外洩（同 _team_row）；彈窗用案名查
            cells[day["date"]] = [{"project": it["project_name"], "note": it["task_note"], "hours": it["hours"],
                                   "planned_hours": it["planned_hours"], "status": it["status"],
                                   "stage_name": it["stage_name"], "work_type": it["work_type"]} for it in p["items"]]
    shoots: dict = {d: [] for d in days}
    for x in shoot_rows:
        s = x["shoot"]
        for d in _shoot_days(s):
            k = d.isoformat()
            if k in shoots:
                shoots[k].append({"title": (s.title or "").strip() or x["project_name"], "project_name": x["project_name"],
                                  "location": x["location"], "start_time": s.start_time or "",
                                  "crew": [c["name"] for c in x["crew"] if c.get("name")]})
    leave: dict = {d: [] for d in days}
    for l in leaves:
        a, b = tw_day(l.start_date), tw_day(l.end_date)
        if not a or not b:
            continue
        for d in days:
            if a.isoformat() <= d <= b.isoformat() and l.staff_name not in leave[d]:
                leave[d].append(l.staff_name)
    return {"week_start": week.isoformat(), "days": days, "me": ident["staff"].name,
            "people": [{"name": n, "cells": c} for n, c in sorted(people.items(), key=lambda kv: (staff_rank(staff_st.get(kv[0])), kv[0]))],
            "shoots": shoots, "leave": leave}



# ── 我的工時：自己填、看自己的、改／刪自己填的（docs/TIMESHEET_SELF_ENTRY_PLAN.md 階段 1）──
#
# 守衛／序列化／讀改刪只有 services.timesheet_self 一份（CRM tab 的「我的一天」同吃，
# 只差模組鑰匙）；own-scope 只認 token 解析出的 staff_id，舊 Sheet 列退回姓名比對。

def _me_ident(request: Request):
    return require_bound_staff(request, "me_finance")


def _team_ident(request: Request):
    """/me/team/*：me_finance（員工頁）或 timesheets（CRM 工作追蹤分頁「人員」連到 /hours.html）任一把，
    再綁定人員檔案（權限稽核第二批 2026-09-08）。/me/timesheets* 仍只認 me_finance（own-scope 的列）。"""
    return require_bound_staff(request, "timesheets", "me_finance")


def _team_row(r) -> dict:
    """團隊頁的一列：ts_dict 去掉 project_id（回 Sheet 案名與時數，不給 CRM 私帳案 id 當連結）。"""
    d = ts_dict(r)
    d.pop("project_id")
    return d


@router.get("/timesheets")
async def my_timesheets(request: Request, month: str = ""):
    """本人該月所有列（Sheet＋手填），含 editable 與合計。month=YYYY-MM，預設本月。"""
    ident = await _me_ident(request)
    m0, m1 = month_or_422(month)
    factory = db_factory_or_503()
    async with factory() as session:
        items = await list_rows(session, ident, m0, m1)
    return {"month": month_key(m0), "items": items,
            "total_hours": round(sum(i["hours"] for i in items), 1)}


@router.post("/timesheets/batch")
async def add_my_timesheets(body: MeTimesheetBatch, request: Request):
    ident = await _me_ident(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return await add_rows(session, ident, body.rows)


@router.put("/timesheets/{row_id}")
async def update_my_timesheet(row_id: str, body: MeTimesheetUpdate, request: Request):
    ident = await _me_ident(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return await update_row(session, ident, row_id, body)


@router.delete("/timesheets/{row_id}")
async def delete_my_timesheet(row_id: str, request: Request):
    ident = await _me_ident(request)
    factory = db_factory_or_503()
    async with factory() as session:
        return await delete_row(session, ident, row_id)


# ── 團隊工時（大家看得到彼此；docs/TIMESHEET_SELF_ENTRY_PLAN.md §5）──
#
# 閘門＝「看得到自己就看得到大家」（owner 2026-09-03「我希望大家可以看到彼此的工時」）：
# 守 _team_ident（timesheets／me_finance 任一 ＋ 綁定）。回的是 Sheet 原字案名與時數，沒有金額、沒有
# CRM 專案 id 的連結 —— 私帳「專案列」可見性那條線守的是錢，這裡是團隊自己的工時表。

@router.get("/team/hours")
async def team_hours(request: Request, month: str = ""):
    """團隊月表：每人合計／填了幾天／每週小計／各案小計，全體各案合計；參考工時＝工作日×8。"""
    await _team_ident(request)
    m0, m1 = month_or_422(month)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet.staff_name, Timesheet.work_date, Timesheet.project_name, Timesheet.hours)
            .where(Timesheet.work_date >= m0).where(Timesheet.work_date < m1)
        )).all()
    data = [(n, tw_day(d), p, h) for n, d, p, h in rows]     # rollup 只算實際（計畫列不算）
    return {"month": month_key(m0), **hours_rollup(data, m0.year, m0.month)}


@router.get("/team/projects")
async def team_projects(request: Request, months: int = 12):
    """專案工時匯總：近 N 個月每個 Sheet 案名的總時數、人數、預算（對到案才有）、最後填報。"""
    await _team_ident(request)
    months = max(1, min(int(months or 12), 36))
    m0, _ = month_span("")
    since = months_back(m0, months - 1)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet.project_name, Timesheet.project_id,
                   func.sum(Timesheet.hours), func.count(func.distinct(Timesheet.staff_name)),
                   func.max(Timesheet.work_date))
            .where(Timesheet.work_date >= since).where(Timesheet.hours > 0)
            .group_by(Timesheet.project_name, Timesheet.project_id)
        )).all()
        budgets = await budgets_for(session, [pid for _, pid, *_ in rows])
    items = []
    for pname, pid, total, people, last in rows:
        b = budgets.get(pid)
        total = round(float(total or 0), 1)
        items.append({"project_name": pname or "(空白)", "hours": total, "people": people,
                      "budget_hours": b, **budget_burn(total, b),
                      "last_entry": day_iso(last)})
    items.sort(key=lambda x: -x["hours"])
    return {"since": month_key(since), "months": months, "items": items,
            "total": round(sum(i["hours"] for i in items), 1)}


@router.get("/team/project")
async def team_project_detail(request: Request, name: str = ""):
    """單一案（Sheet 原字）的工時明細：各人合計、各月走勢、最近 60 列。"""
    await _team_ident(request)
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name 必填")
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet).where(Timesheet.project_name == name).where(Timesheet.hours > 0)
            .order_by(Timesheet.work_date.desc()))).scalars().all()
    m = project_metrics(metrics_input(rows))
    return {
        "project_name": name, "total": m["total"], "by_person": m["by_person"],
        "by_month": rows_by_month(rows),
        "recent": [_team_row(r) for r in rows[:60]],
    }


@router.get("/projects_burn")
async def my_projects_burn(request: Request):
    """員工端「專案查詢」：工作追蹤專案表的唯讀版（owner 2026-09-05「讓員工可以看到這個」）。

    只給全案的工時數字（已投入／預算／剩餘／消耗率／列數／最後填報），沒有金額、沒有
    「建議預算」（那是從預期毛利算出來的，等於間接揭露錢）、沒有未對映診斷。
    /timesheets/summary 對 timesheets 分頁鑰匙也回同一份抹過的鍵（api_timesheets._redact_summary）。
    """
    await _me_bound(request, "me_project_lookup")
    from services.timesheet_lookup import burn_rows
    factory = db_factory_or_503()
    async with factory() as session:
        items = await burn_rows(session)
    from routers.api_timesheets import SUMMARY_PUBLIC_KEYS      # /timesheets/summary 抹私帳後的那份鍵，兩邊同一份
    return {"projects": [{k: it.get(k) for k in SUMMARY_PUBLIC_KEYS} for it in items]}
