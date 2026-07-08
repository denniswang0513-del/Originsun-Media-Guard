"""
api_timesheets.py — 工時 API（N2 階段 0：Google Sheet 自動同步，藍圖 §3.6）

流程：Google Apps Script 定時把 Sheet 新列 POST 到 /ingest（帶 X-Timesheet-Token）
→ 去重寫入 timesheets → 專案名精確對映 crm_projects.name → 帶 budget 欄時
鏡射 crm_projects.budget_hours。/summary 給 burn 檢核（已投入/預算/消耗率）。

token：settings.json `timesheet.ingest_token`（首次取用自動生成）。
Apps Script 端腳本見 docs/appsscript/timesheet_sync.gs。
"""

import hashlib
import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request  # type: ignore

import core.state as state
from config import load_settings, save_settings
from core.auth import check_admin
from core.db_guard import db_factory_or_503
from core.schemas import TimesheetIngestRequest

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


def _parse_date(raw: str):
    """Sheet 日期容錯：2026/6/30、2026-06-30、2026/06/30。解析失敗回 None（列仍收）。"""
    raw = (raw or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _row_hash(date_s: str, staff: str, project: str, task: str, hours) -> str:
    key = f"{(date_s or '').strip()}|{(staff or '').strip()}|{(project or '').strip()}|{(task or '').strip()}|{hours}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


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

    from sqlalchemy import select
    from db.models import Timesheet, CrmProject

    inserted = 0
    skipped = 0
    unmatched: set[str] = set()

    async with factory() as session:
        # 專案名 → id 對映表（一次載入；精確比對，模糊留人工）
        proj_rows = (await session.execute(select(CrmProject.id, CrmProject.name))).all()
        name_to_id = {(n or "").strip(): pid for pid, n in proj_rows}

        # 既有 hash 一次撈（避免逐列查詢；量大時仍遠小於全表掃描成本）
        hashes = [_row_hash(r.date, r.staff, r.project, r.task, r.hours) for r in req.rows]
        existing = set(
            (await session.execute(select(Timesheet.row_hash).where(Timesheet.row_hash.in_(hashes)))).scalars()
        )

        budget_updates: dict[str, float] = {}
        seen_in_batch: set[str] = set()
        for r, h in zip(req.rows, hashes):
            if h in existing or h in seen_in_batch:
                skipped += 1
                continue
            seen_in_batch.add(h)
            pname = (r.project or "").strip()
            pid = name_to_id.get(pname)
            if pid is None and pname:
                unmatched.add(pname)
            session.add(Timesheet(
                id=uuid.uuid4().hex,
                work_date=_parse_date(r.date),
                staff_name=(r.staff or "").strip(),
                project_id=pid,
                project_name=pname,
                task_note=(r.task or "").strip() or None,
                hours=float(r.hours or 0),
                status="import",
                source="sheet",
                row_hash=h,
            ))
            inserted += 1
            # Sheet 帶預算欄時鏡射到專案（階段0 預算 source of truth 還在 Sheet）
            if pid and r.budget is not None and r.budget > 0:
                budget_updates[pid] = float(r.budget)

        for pid, bh in budget_updates.items():
            proj = await session.get(CrmProject, pid)
            if proj is not None and getattr(proj, "budget_hours", None) != bh:
                proj.budget_hours = bh

        await session.commit()

    return {
        "inserted": inserted,
        "skipped": skipped,
        "unmatched_projects": sorted(unmatched),
        "budget_mirrored": len(budget_updates),
    }


@router.get("/summary")
async def burn_summary(request: Request):
    """每專案 burn 摘要：已投入時數 / 預算 / 消耗率。未對映專案以名稱聚合列出。"""
    check_admin(request)
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

    return {
        "projects": items,
        "unmatched": [{"project_name": n or "(空白)", "hours_used": round(h or 0, 1), "rows": c}
                      for n, h, c in unmatched],
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
