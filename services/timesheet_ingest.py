"""services/timesheet_ingest.py — 工時列寫入 timesheets 的唯一一條路。

三個入口都走這裡：Apps Script／腳本打的 `POST /timesheets/ingest`（HTTP 只做 token 與
上限檢查）、主控端定時拉 Sheet 的 runner（services/timesheet_puller）、以後任何批次。
規則：row_hash 去重（同內容不重複入庫）、同 (人, 日, 專案) 已有手填列 → Sheet 列跳過
（手填優先）、專案名對映走 core.hr_logic.resolve_project（撞案不猜）、人員走 resolve_staff。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from core.hr_logic import Misses, manual_dup_key, resolve_project, resolve_staff, sheet_key_tuple
from services.timesheet_lookup import load_project_lookup, load_staff_index


def parse_date(raw: str):
    """Sheet 日期容錯：2026/6/30、2026-06-30、2026/06/30。解析失敗回 None（列仍收）。"""
    raw = (raw or "").strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def row_hash(date_s: str, staff: str, project: str, task: str, hours) -> str:
    key = f"{(date_s or '').strip()}|{(staff or '').strip()}|{(project or '').strip()}|{(task or '').strip()}|{hours}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


async def ingest_context(session, staff_names) -> dict:
    """ingest 每批都要的三份查表（專案對映／人員索引／手填優先鍵）。拉整本 Sheet 分 20 批時
    建一次給每批用 —— 原本每批各建一次＝80 個查詢做 23 個的事。"""
    from sqlalchemy import select
    from db.models import Timesheet, TimesheetConflict, TimesheetTombstone
    names = {n for n in ((s or "").strip() for s in staff_names) if n}
    tombstones = set((await session.execute(select(TimesheetTombstone.row_hash))).scalars())   # 總表刪過的 Sheet 列
    # 總表改過的 Sheet 列（同人同日同案鍵 → 列 id）：Sheet 那格之後又變＝衝突，等人選，不自動插／蓋
    # 鍵＝改鍵欄位前記下的 sheet_key（沒有就現值）；同鍵可能好幾列（一人一天一案多條），存成 list，配對時看內容
    edited = (await session.execute(
        select(Timesheet.id, Timesheet.staff_name, Timesheet.work_date, Timesheet.project_name,
               Timesheet.sheet_key, Timesheet.task_note, Timesheet.hours)
        .where(Timesheet.source != "manual").where(Timesheet.edited_at.isnot(None)))).all()
    edited_keys: dict = {}
    for rid, n, d, p, sk, task, hrs in edited:
        edited_keys.setdefault(sheet_key_tuple(sk) or manual_dup_key(n, d, p), []).append((rid, (task or "").strip(), float(hrs or 0)))
    conflict_hashes = set((await session.execute(select(TimesheetConflict.incoming_hash))).scalars())   # 記過的不重記
    manual_keys: set = set()
    if names:
        m_rows = (await session.execute(
            select(Timesheet.staff_name, Timesheet.work_date, Timesheet.project_name)
            .where(Timesheet.source == "manual").where(Timesheet.staff_name.in_(names)))).all()
        manual_keys = {manual_dup_key(n, d, p) for n, d, p in m_rows}
    return {"lk": await load_project_lookup(session), "staff_index": await load_staff_index(session),
            "manual_keys": manual_keys, "tombstones": tombstones,
            "edited_keys": edited_keys, "conflict_hashes": conflict_hashes}


def _match_edited(cands, r):
    """同（人,日,案）鍵底下總表改過的列 → 哪一列是這條 Sheet 新版的「同一列」：內容（做了什麼）相同優先，
    其次時數相同；都對不上就不是同一列（一人一天一案本來就可以好幾條），照一般插入。"""
    if not cands:
        return None
    task = (getattr(r, "task", "") or "").strip()
    try:
        hrs = float(getattr(r, "hours", 0) or 0)
    except (TypeError, ValueError):
        hrs = 0.0
    for rid, t, h in cands:
        if task and t == task:
            return rid
    for rid, t, h in cands:
        if abs(h - hrs) < 1e-6:
            return rid
    return None


async def ingest(session, rows, source: str, ctx: dict | None = None) -> dict:
    """`rows`：有 .date/.staff/.project/.task/.hours 的物件（core.schemas.TimesheetRow）。
    回 {inserted, skipped, skipped_manual_priority, ambiguous_projects, unmatched_projects,
    staff_ambiguous, staff_unmatched}。一個 session 一個交易。"""
    from sqlalchemy import select
    from db.models import Timesheet

    inserted = 0
    skipped = 0
    misses, staff_misses = Misses(), Misses()      # 專案：撞案／找不到；人員：同名兩人／沒這人

    # 專案對映：對映表 → 精確 → 去客戶前綴（唯一才算）→ 撞案不猜（core.hr_logic）
    ctx = ctx or await ingest_context(session, (r.staff for r in rows))
    lk, staff_index, manual_keys, tombstones = ctx["lk"], ctx["staff_index"], ctx["manual_keys"], ctx["tombstones"]
    edited_keys, conflict_hashes = ctx["edited_keys"], ctx["conflict_hashes"]

    # 既有 hash 一次撈（避免逐列查詢；量大時仍遠小於全表掃描成本）
    hashes = [row_hash(r.date, r.staff, r.project, r.task, r.hours) for r in rows]
    existing = set(
        (await session.execute(select(Timesheet.row_hash).where(Timesheet.row_hash.in_(hashes)))).scalars()
    ) if hashes else set()

    # 雙來源去重（藍圖 §3.6 階段3）：同 (人, 日, 專案) 已有手填列 → Sheet 列跳過（手填優先；鍵集在 ctx）
    skipped_manual = 0
    skipped_deleted = 0
    conflicts = 0
    skipped_conflict = 0
    seen_in_batch: set[str] = set()
    for r, h in zip(rows, hashes):
        if h in existing or h in seen_in_batch:
            skipped += 1
            continue
        if h in tombstones:                      # 總表刪過：以總表為準，不插回來
            skipped_deleted += 1
            continue
        seen_in_batch.add(h)
        wd = parse_date(r.date)
        if manual_keys and manual_dup_key(r.staff, wd, r.project) in manual_keys:
            skipped_manual += 1
            continue
        if h in conflict_hashes:                 # 這個 Sheet 版本已經記過衝突（待決或已決）
            skipped_conflict += 1
            continue
        rid = _match_edited(edited_keys.get(manual_dup_key(r.staff, wd, r.project)), r)
        if rid:                                  # 總表改過的同一列，Sheet 那格之後又變了 → 記衝突等 owner 選
            from db.models import TimesheetConflict
            session.add(TimesheetConflict(id=uuid.uuid4().hex, row_id=rid, incoming_hash=h,
                                          incoming={"date": r.date, "staff": r.staff, "project": r.project, "task": r.task, "hours": r.hours}))
            conflict_hashes.add(h)
            conflicts += 1
            continue
        pname = (r.project or "").strip()
        pid, why = resolve_project(pname, lk)
        misses.note(why, pname)
        sname = (r.staff or "").strip()
        sid, swhy = resolve_staff(sname, staff_index)
        staff_misses.note(swhy, sname)
        session.add(Timesheet(
            id=uuid.uuid4().hex,
            work_date=wd,
            staff_name=sname,
            staff_id=sid,
            project_id=pid,
            project_name=pname,
            task_note=(r.task or "").strip() or None,
            hours=float(r.hours or 0),
            status="import",
            source=source,
            row_hash=h,
        ))
        inserted += 1

    await session.commit()
    return {
        "inserted": inserted,
        "skipped": skipped,
        "skipped_manual_priority": skipped_manual,   # 手填優先擋下的 Sheet 列
        "skipped_deleted": skipped_deleted,          # 總表刪過、留了指紋的 Sheet 列
        "conflicts": conflicts,                      # 這次新記的衝突（總表改過的列，Sheet 又變）
        "skipped_conflict": skipped_conflict,        # 已記過衝突的 Sheet 版本
        **misses.report("projects"),      # 撞案：owner 用 project_map 指定
        **staff_misses.report("staff"),   # 同名兩人：不猜，留 NULL
    }
