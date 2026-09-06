"""services/timesheet_conflicts.py — Sheet 與總表改過的同一列撞到時的「待決」（owner 2026-09-03）。

ingest 記衝突（services.timesheet_ingest），這裡只做列出與決定：
- keep_mine：用總表的 → Sheet 那個版本留指紋（下次不再進來）
- use_sheet：用 Sheet 的 → 把 Sheet 內容套回那列（備註／分類／計畫保留），row_hash 換成 Sheet 版本
- keep_both：兩列都留 → Sheet 版本照一般拉取插進來
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from core.hr_logic import day_iso, resolve_project
from core.schemas import TimesheetRow
from services.timesheet_ingest import ingest, ingest_context, parse_date
from services.timesheet_lookup import load_project_lookup
from services.timesheet_self import add_tombstone, ts_dict

CHOICES = ("keep_mine", "use_sheet", "keep_both")


def _sheet_view(inc: dict) -> dict:
    """Sheet 原字 → 跟 ts_dict 同名的欄，前端才能兩列並排比。"""
    return {"date": day_iso(parse_date(inc.get("date", ""))) or (inc.get("date") or ""),
            "staff_name": inc.get("staff") or "", "project_name": inc.get("project") or "",
            "task_note": inc.get("task") or "", "hours": float(inc.get("hours") or 0)}


async def list_conflicts(session) -> list:
    """待決的衝突：{id, created_at, mine: ts_dict(總表那列), sheet: Sheet 新版}。總表那列已不在的自動結案。"""
    from db.models import Timesheet, TimesheetConflict
    rows = (await session.execute(
        select(TimesheetConflict).where(TimesheetConflict.resolved_at.is_(None))
        .order_by(TimesheetConflict.created_at.desc()))).scalars().all()
    out = []
    for c in rows:
        r = await session.get(Timesheet, c.row_id)
        if r is None:                        # 總表那列後來被刪了：沒東西可比 → 衝突作廢（不種指紋：Sheet 的新版本下次照一般拉取進來，要不要留由總表決定）
            await session.delete(c)
            continue
        out.append({"id": c.id, "created_at": c.created_at.isoformat() if c.created_at else None,
                    "mine": ts_dict(r, with_note=True), "sheet": _sheet_view(c.incoming or {})})
    await session.commit()
    return out


async def resolve_conflict(session, cid: str, choice: str, who: str) -> dict:
    from db.models import Timesheet, TimesheetConflict
    if choice not in CHOICES:
        raise HTTPException(status_code=422, detail=f"choice 要是 {'/'.join(CHOICES)}")
    c = await session.get(TimesheetConflict, cid)
    if c is None:
        raise HTTPException(status_code=404, detail="找不到這筆衝突")
    if c.resolved_at is not None:
        raise HTTPException(status_code=409, detail=f"已經決定過（{c.resolution}）")
    inc = c.incoming or {}
    r = await session.get(Timesheet, c.row_id)
    if choice == "keep_mine":
        await add_tombstone(session, c.incoming_hash, who, staff_name=inc.get("staff", ""),
                            project_name=inc.get("project", ""), hours=inc.get("hours", 0))
    elif choice == "use_sheet":
        if r is None:
            raise HTTPException(status_code=409, detail="總表那列已不在，只能「兩列都留」讓 Sheet 版本進來")
        pname = (inc.get("project") or "").strip()
        pid, _why = resolve_project(pname, await load_project_lookup(session))
        r.work_date = parse_date(inc.get("date", "")) or r.work_date
        new_staff = (inc.get("staff") or "").strip()
        if new_staff and new_staff != (r.staff_name or ""):
            from core.hr_logic import resolve_staff
            from services.timesheet_lookup import load_staff_index
            r.staff_name = new_staff
            r.staff_id, _swhy = resolve_staff(new_staff, await load_staff_index(session))   # 人換了 id 要跟著換
        r.project_id, r.project_name = pid, pname
        from core.hr_logic import manual_dup_key
        r.sheet_key = manual_dup_key(r.staff_name, r.work_date, r.project_name)
        r.task_note = (inc.get("task") or "").strip() or None
        r.hours = float(inc.get("hours") or 0)
        r.row_hash = c.incoming_hash              # 之後這個 Sheet 版本就是「已有」
        # edited_at 保留：這列還是 owner 決定過的，Sheet 再變仍要問
    else:                                          # keep_both：照一般拉取插進來（不看衝突鍵，不然又記一次）
        ctx = await ingest_context(session, [inc.get("staff", "")])
        ctx["edited_keys"], ctx["conflict_hashes"], ctx["tombstones"] = {}, set(), set()
        res = await ingest(session, [TimesheetRow(**{k: inc.get(k, "") for k in ("date", "staff", "project", "task")}, hours=float(inc.get("hours") or 0))], "sheet", ctx)
        if not (res or {}).get("inserted"):
            why = "已有同人同日同案的手填列（手填優先）" if (res or {}).get("skipped_manual_priority") else "這一版已經在總表裡了"
            raise HTTPException(status_code=409, detail=f"Sheet 版本沒有插進來：{why}")
        c = await session.get(TimesheetConflict, cid)   # ingest commit 過，重抓
    c.resolution, c.resolved_at, c.resolved_by = choice, datetime.now(timezone.utc), who or None
    await session.commit()
    return {"status": "ok", "resolution": choice}
