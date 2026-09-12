# -*- coding: utf-8 -*-
"""routers/timesheets/reports.py —— 工作追蹤 API 的一段：儀表板（大家四格＋主管層）與 CSV 匯出

2026-09-12 從 routers/api_timesheets.py（1,100 行）拆成套件；URL 全部不變（同一顆 router 在 _shared）。
共用守衛／小工具在 ._shared；掃原始碼的測試用 _srcscan.timesheets_src()。
"""
import csv
import io
from datetime import datetime, timedelta
from fastapi import HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from urllib.parse import quote
from core.auth import check_admin_or_module, payload_grants
from core.db_guard import db_factory_or_503
from core.hr_logic import HOURS_PER_WORKDAY, active_fillers, fillers_on, hours_rollup, midnight_of, missing_fillers, month_span, prev_workday, tw_day, type_composition
from core.journal_logic import week_start_of
from db.models import Timesheet
from services.timesheet_lookup import burn_rows
from services.timesheet_self import month_or_422, ts_dict
from ._shared import SUMMARY_PUBLIC_KEYS, router


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
