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
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from sqlalchemy import func, select  # type: ignore

from core.auth import check_admin_or_module, grant_admin_all_modules
from core.db_guard import db_factory_or_503
from core.hr_logic import (budget_burn, by_month, day_iso, hours_rollup, leave_balance, leave_to_dict,
                           month_span, months_back, project_metrics, tw_day)
from core.identity import resolve_current_staff
from core.schemas import (MeLeaveCreate, MeProfileUpdate, MeTimesheetBatch,
                          MeTimesheetUpdate, MeTodoUpdate)
from db.models import (BulletinItem, Client, CrmPaymentRequest, CrmProject,
                       CrmProjectStaff, HrLeaveRequest, Timesheet)
from services.timesheet_lookup import budgets_for
from services.timesheet_manual import project_options
from services.timesheet_self import add_rows, bound_ident, delete_row, list_rows, month_or_422, ts_dict, update_row
from routers.api_hr import approved_annual_used, new_leave_request

router = APIRouter(prefix="/api/v1/me", tags=["me"])

ME_MODULE_KEYS = ("me_projects", "me_profile", "me_todos", "me_finance", "me_leave",
                  "me_petty",     # 零用金卡（2026-08-19 從 me_finance 拆出）
                  "me_benefits")  # 福委會卡（2026-08-21，員工自己登記快樂/進修）

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
        "hire_date": s.hire_date.strftime("%Y-%m-%d") if s.hire_date else "",
        "status": s.status or "在職",
    }


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
    out = {
        "username": ident["username"],
        "staff_id": ident["staff_id"],
        "bound": staff is not None,
        "allowed": allowed,
        # 具人事管理權限（hr_leave 模組或 admin）→ /my.html 頂欄顯示「人事管理」
        # 深連結（官網 STAFF 入口一路通到內部簽核頁）
        "hr_manager": "hr_leave" in mods,
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
            rows = (await session.execute(
                select(BulletinItem)
                .where(BulletinItem.mine_filter(ident["username"] or ""))
                .where(BulletinItem.status != "done")
                .order_by(BulletinItem.pinned.desc(), BulletinItem.sort_order,
                          BulletinItem.created_at)
            )).scalars().all()
            out["todos"] = [{
                "id": o.id, "title": o.title, "note": o.note or "",
                "status": o.status, "priority": o.priority,
                "pinned": bool(o.pinned),
                "assigned_to_me": o.assignee_username == ident["username"],
                "created_at": o.created_at.isoformat() if o.created_at else None,
            } for o in rows]

        if "me_finance" in allowed and staff is not None:
            # ⚠ name-match（脆弱）：見模組 docstring。
            name = staff.name
            ts_rows = (await session.execute(
                select(Timesheet.project_name,
                       func.sum(Timesheet.hours), func.count(Timesheet.id),
                       func.max(Timesheet.work_date))
                .where(Timesheet.staff_name == name)
                .group_by(Timesheet.project_name)
                .order_by(func.max(Timesheet.work_date).desc())
            )).all()
            month_start = month_span("")[0]
            month_hours = (await session.execute(
                select(func.coalesce(func.sum(Timesheet.hours), 0.0))
                .where(Timesheet.staff_name == name)
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
                    "date": p.request_date.strftime("%Y-%m-%d") if p.request_date else "",
                    "amount": p.amount or 0, "summary": p.summary or "",
                    "status": p.payment_status or "",
                    "project": p.project_label or "",
                } for p in recent],
            }

        if "me_leave" in allowed and staff is not None:
            year = datetime.now().year
            used = await approved_annual_used(session, [ident["staff_id"]], year)
            rows = (await session.execute(
                select(HrLeaveRequest)
                .where(HrLeaveRequest.staff_id == ident["staff_id"])
                .order_by(HrLeaveRequest.start_date.desc().nulls_last())
                .limit(10)
            )).scalars().all()
            out["leave"] = {
                "quota": leave_balance(staff.annual_leave_days,
                                       used.get(ident["staff_id"], 0.0)),
                "recent": [leave_to_dict(r) for r in rows],
            }
    return out


@router.put("/profile")
async def update_my_profile(body: MeProfileUpdate, request: Request):
    """本人編輯自己的人員檔案 — 僅白名單欄位；target 一律由 token 解析。"""
    check_admin_or_module(request, "me_profile")
    ident = await resolve_current_staff(request)
    if ident["staff"] is None:
        raise HTTPException(status_code=409, detail="帳號尚未綁定人員檔案，請聯絡管理員")
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


@router.post("/leave")
async def apply_my_leave(body: MeLeaveCreate, request: Request):
    """本人送請假單（staff_id 由 token 解析；固定進待審，管理端在人事 tab 簽核）。"""
    payload = check_admin_or_module(request, "me_leave")
    ident = await resolve_current_staff(request)
    if ident["staff"] is None:
        raise HTTPException(status_code=409, detail="帳號尚未綁定人員檔案，請聯絡管理員")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = new_leave_request(ident["staff_id"], ident["staff"].name, body,
                                (payload or {}).get("sub"))
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
        result = leave_to_dict(obj)
    # 通知管理者（best-effort；精簡 agent 可能沒帶 notifier）
    try:
        from notifier import notify_tab_async
        await notify_tab_async(
            "leave_request",
            staff_name=result["staff_name"], leave_type=result["leave_type"],
            start=result["start_date"], end=result["end_date"],
            days=result["days"], reason=result["reason"] or "-",
        )
    except Exception:
        pass
    return result


@router.delete("/leave/{leave_id}")
async def cancel_my_leave(leave_id: str, request: Request):
    """撤回自己的待審請假單（已核准/已退回不可自行刪除，找管理者）。"""
    check_admin_or_module(request, "me_leave")
    ident = await resolve_current_staff(request)
    if ident["staff_id"] is None:
        raise HTTPException(status_code=409, detail="帳號尚未綁定人員檔案")
    factory = db_factory_or_503()
    async with factory() as session:
        obj = (await session.execute(
            select(HrLeaveRequest)
            .where(HrLeaveRequest.id == leave_id)
            .where(HrLeaveRequest.staff_id == ident["staff_id"])
        )).scalar_one_or_none()
        if obj is None:
            raise HTTPException(status_code=404, detail="找不到這張請假單（或不是你的）")
        if obj.status != "待審":
            raise HTTPException(status_code=409, detail="僅待審單可自行撤回")
        await session.delete(obj)
        await session.commit()
    return {"deleted": leave_id}


@router.get("/timesheet_options")
async def my_timesheet_options(request: Request):
    """補登工時的專案下拉：進行中（製作/結案）+ 本人最近填過的專案。"""
    check_admin_or_module(request, "me_finance")
    ident = await resolve_current_staff(request)
    factory = db_factory_or_503()
    async with factory() as session:
        staff_name = ident["staff"].name if ident["staff"] else None
        return {"projects": await project_options(session, staff_name)}


# ── 我的工時：自己填、看自己的、改／刪自己填的（docs/TIMESHEET_SELF_ENTRY_PLAN.md 階段 1）──
#
# 守衛／序列化／讀改刪只有 services.timesheet_self 一份（CRM tab 的「我的一天」同吃，
# 只差模組鑰匙）；own-scope 只認 token 解析出的 staff_id，舊 Sheet 列退回姓名比對。

def _me_ident(request: Request):
    return bound_ident(request, "me_finance")


@router.get("/timesheets")
async def my_timesheets(request: Request, month: str = ""):
    """本人該月所有列（Sheet＋手填），含 editable 與合計。month=YYYY-MM，預設本月。"""
    ident = await _me_ident(request)
    m0, m1 = month_or_422(month)
    factory = db_factory_or_503()
    async with factory() as session:
        items = await list_rows(session, ident, m0, m1)
    return {"month": m0.strftime("%Y-%m"), "items": items,
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
# 同 _me_ident（me_finance ＋ 綁定）。回的是 Sheet 原字案名與時數，沒有金額、沒有
# CRM 專案 id 的連結 —— 私帳「專案列」可見性那條線守的是錢，這裡是團隊自己的工時表。

@router.get("/team/hours")
async def team_hours(request: Request, month: str = ""):
    """團隊月表：每人合計／填了幾天／每週小計／各案小計，全體各案合計；參考工時＝工作日×8。"""
    await _me_ident(request)
    m0, m1 = month_or_422(month)
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet.staff_name, Timesheet.work_date, Timesheet.project_name, Timesheet.hours)
            .where(Timesheet.work_date >= m0).where(Timesheet.work_date < m1)
        )).all()
    data = [(n, tw_day(d), p, h) for n, d, p, h in rows]     # rollup 只算實際（計畫列不算）
    return {"month": m0.strftime("%Y-%m"), **hours_rollup(data, m0.year, m0.month)}


@router.get("/team/projects")
async def team_projects(request: Request, months: int = 12):
    """專案工時匯總：近 N 個月每個 Sheet 案名的總時數、人數、預算（對到案才有）、最後填報。"""
    await _me_ident(request)
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
    return {"since": since.strftime("%Y-%m"), "months": months, "items": items,
            "total": round(sum(i["hours"] for i in items), 1)}


@router.get("/team/project")
async def team_project_detail(request: Request, name: str = ""):
    """單一案（Sheet 原字）的工時明細：各人合計、各月走勢、最近 60 列。"""
    await _me_ident(request)
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name 必填")
    factory = db_factory_or_503()
    async with factory() as session:
        rows = (await session.execute(
            select(Timesheet).where(Timesheet.project_name == name).where(Timesheet.hours > 0)
            .order_by(Timesheet.work_date.desc()))).scalars().all()
    m = project_metrics((tw_day(r.work_date), r.staff_name, r.work_type, r.hours) for r in rows)
    return {
        "project_name": name, "total": m["total"], "by_person": m["by_person"],
        "by_month": by_month((tw_day(r.work_date), r.hours) for r in rows),
        "by_month": sorted(by_month.items()),
        "recent": [ts_dict(r) for r in rows[:60]],
    }
