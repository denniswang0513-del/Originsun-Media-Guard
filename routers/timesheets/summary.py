# -*- coding: utf-8 -*-
"""routers/timesheets/summary.py —— 工作追蹤 API 的一段：手填工時的專案下拉、管理端代填、人員月視圖、burn 摘要、最近同步列

2026-09-12 從 routers/api_timesheets.py（1,100 行）拆成套件；URL 全部不變（同一顆 router 在 _shared）。
共用守衛／小工具在 ._shared；掃原始碼的測試用 _srcscan.timesheets_src()。
"""
from fastapi import HTTPException, Request
from sqlalchemy import func as safunc, select
from core.auth import _extract_token, check_admin_or_module, current_username, payload_grants
from core.db_guard import db_factory_or_503
from core.hr_logic import explain_miss, month_key
from core.schemas import TimesheetManualRequest
from db.models import CrmStaff, Timesheet
from services.timesheet_lookup import burn_rows, load_project_lookup
from services.timesheet_manual import project_options
from services.timesheet_self import add_rows, month_or_422, ts_dict
from ._shared import _CANDS_CACHE, _redact_summary, _ts_or_bound, router


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
