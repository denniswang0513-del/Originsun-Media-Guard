"""services/leave_application.py — 請假「申請單」（owner 2026-09-15 三步：日期算小時 → 自己挑要扣的假 → 一整張單送出、一次核准、核准前可編輯）。

一張申請單（hr_leave_applications）＝挑的所有日期＋扣法（items：哪幾筆假、各幾小時）＋事由＋證明。
送出時就同步展開成子單（hr_leave_requests，一天一種假一張、application_id 指回來）：撞單／保留時數／病假上限／
日曆事件都照原本的子單機制走，不用另寫一套；編輯＝重建子單；核准／退回／撤回＝整張連子單一起。
純規則（庫存挑法 fit_items、展開成子單 plan_children）在 core.leave_logic；這裡只有 I/O 與 HTTP 形狀無關的組裝。
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException  # type: ignore
from sqlalchemy import select  # type: ignore

from core.hr_logic import day_iso, midnight_of
from core.leave_logic import (ACTIVE_STATUSES, HOURS_PER_DAY, LEDGER_TYPES, PICKABLE_RECORD_TYPES, RECORD_META, SICK_CAP_DAYS,
                              as_date, cancel_mode, fit_items, hours_to_days, normalize_dates, notice_warning, plan_children,
                              record_item_id, usable_credits, workdays_between, working_hours)
from db.models import HrLeaveAllocation, HrLeaveApplication, HrLeaveRequest
from services import leave_service
from services.leave_service import _err


def _loads(raw, default):
    try:
        v = json.loads(raw) if isinstance(raw, str) else (raw if raw is not None else default)
    except ValueError:
        return default
    return v if isinstance(v, type(default)) else default


def application_dict(app: HrLeaveApplication, children: list | None = None, today: date | None = None, holidays=None) -> dict:
    dates = _loads(app.dates, [])
    d = {
        "id": app.id, "staff_id": app.staff_id, "staff_name": app.staff_name or "",
        "dates": dates, "start_date": dates[0] if dates else "", "end_date": dates[-1] if dates else "",
        "part": app.part or "all", "start_time": app.start_time or "", "end_time": app.end_time or "",
        "items": _loads(app.items, []), "hours": float(app.hours or 0), "days": hours_to_days(app.hours or 0),
        "reason": app.reason or "", "status": app.status, "proof_path": app.proof_path or "",
        "created_by": app.created_by or "", "created_at": app.created_at.isoformat() if app.created_at else None,
        "approved_by": app.approved_by or "", "approved_at": app.approved_at.isoformat() if app.approved_at else None,
        "reject_note": app.reject_note or "", "cancel_note": app.cancel_note or "",
        "kinds": sorted({i.get("kind") for i in _loads(app.items, []) if i.get("kind")}),
        "proof_required": any(RECORD_META.get(i.get("kind"), {}).get("proof") for i in _loads(app.items, [])),
    }
    d["cancel_mode"] = cancel_mode(as_date(dates[0]), today or date.today(), holidays) if (app.status == "已核准" and dates) else None
    if children is not None:
        d["children"] = [leave_service.request_dict(c, holidays, today) for c in children]
    return d


async def children_of(session, app_id: str) -> list:
    rows = (await session.execute(select(HrLeaveRequest).where(HrLeaveRequest.application_id == app_id)
                                  .order_by(HrLeaveRequest.start_date, HrLeaveRequest.start_time))).scalars().all()
    return list(rows)


async def get_or_404(session, app_id: str, staff_id: str = "") -> HrLeaveApplication:
    app = await session.get(HrLeaveApplication, app_id)
    if app is None or (staff_id and app.staff_id != staff_id):
        raise HTTPException(status_code=404, detail="找不到這張申請單（或不是你的）")
    return app


# ── 庫存 ───────────────────────────────────────────────────────────────────

async def _reserved_by_pending(session, staff_id: str, exclude_app_id: str = "") -> dict:
    """待審申請單已經挑走的：{item_id: hours}（credit id 或 type:假別）。編輯自己那張時排除自己。"""
    rows = (await session.execute(select(HrLeaveApplication).where(HrLeaveApplication.staff_id == staff_id)
                                  .where(HrLeaveApplication.status == "待審"))).scalars().all()
    out: dict = {}
    for a in rows:
        if a.id == exclude_app_id:
            continue
        for it in _loads(a.items, []):
            out[it.get("id", "")] = out.get(it.get("id", ""), 0.0) + float(it.get("hours") or 0)
    return out


async def _legacy_pending(session, staff_id: str) -> dict:
    """{假別: 待審小時}，只算沒有申請單的單（手機送的舊路）；走時數帳的假別才算。"""
    from sqlalchemy import func  # type: ignore
    rows = (await session.execute(
        select(HrLeaveRequest.leave_type, func.coalesce(func.sum(leave_service._HOURS), 0.0))
        .where(HrLeaveRequest.staff_id == staff_id).where(HrLeaveRequest.status == "待審")
        .where(HrLeaveRequest.application_id.is_(None)).where(HrLeaveRequest.leave_type.in_(LEDGER_TYPES))
        .group_by(HrLeaveRequest.leave_type))).all()
    return {lt: round(float(h or 0), 2) for lt, h in rows}


async def inventory(session, staff_id: str, today: date | None = None, exclude_app_id: str = "") -> list:
    """員工自己的假（第 2 步清單）：走時數帳的每一筆 credit（特休／補休各批，available＝剩餘−待審申請單挑走的）＋
    不走時數帳的假別（病假有年上限、事假不給薪、婚假／喪假要附證明；公假還沒規劃，不在清單）。順序：credit 先到期先排，再記錄型假別。"""
    today = today or date.today()
    reserved = await _reserved_by_pending(session, staff_id, exclude_app_id)
    credits = (await leave_service.credits_for(session, [staff_id])).get(staff_id, [])
    live = usable_credits(credits, on=today)
    # 沒有申請單的待審舊單（手機送的）也保留時數，但只知道假別不知道扣哪筆：照先到期先扣的順序暫時壓在前面的 credit 上。
    # 🔴 只算 application_id 空的：申請單的子單也是待審，但它們已經在 reserved（或正在編輯、要排除）裡 —— 再算一次就會把
    #    自己那張的時數扣兩遍，編輯時看到「補休已經夠了、這筆沒扣到」（2026-09-15 真機抓到）。
    legacy = await _legacy_pending(session, staff_id)
    out = []
    for c in live:
        take_legacy = min(legacy.get(c["kind"], 0.0), max(float(c["remaining"]) - reserved.get(c["id"], 0.0), 0.0))
        legacy[c["kind"]] = round(legacy.get(c["kind"], 0.0) - take_legacy, 2)
        avail = round(float(c["remaining"]) - reserved.get(c["id"], 0.0) - take_legacy, 2)
        out.append({"id": c["id"], "kind": c["kind"], "credit_id": c["id"], "label": (c.get("reason") or "").split("（")[0].strip() or c["kind"],
                    "available": max(avail, 0.0), "remaining": c["remaining"], "expires_on": c.get("expires_on"),
                    "proof_required": False, "paid": "給薪", "unlimited": False})
    sick_used = (await leave_service.sick_used_for(session, [staff_id], today.year)).get(staff_id, 0.0)
    for kind in PICKABLE_RECORD_TYPES:
        meta = RECORD_META[kind]
        iid = record_item_id(kind)
        row = {"id": iid, "kind": kind, "credit_id": None, "label": kind, "expires_on": None, "proof_required": bool(meta.get("proof")),
               "paid": meta.get("paid", ""), "unlimited": "cap_days" not in meta, "available": None, "remaining": None}
        if kind == "病假":
            cap = SICK_CAP_DAYS * HOURS_PER_DAY
            row["available"] = max(round(cap - sick_used * HOURS_PER_DAY - reserved.get(iid, 0.0), 2), 0.0)
            row["remaining"] = row["available"]
            row["label"] = f"病假（今年還可 {hours_to_days(row['available']):g} 天）"
        elif "cap_days" in meta:
            row["available"] = float(meta["cap_days"] * HOURS_PER_DAY)
            row["remaining"] = row["available"]
        out.append(row)
    return out


# ── 試算 ───────────────────────────────────────────────────────────────────

def day_slots(dates: list, part: str, start_time, end_time, holidays) -> list:
    """每一天要幾小時（同一組時段套到每一天）：[{date, hours, part, start_time, end_time, errors}]。"""
    part = (part or "all").strip() or "all"
    out = []
    for d in dates:
        row = {"date": d, "hours": 0.0, "part": part, "start_time": start_time if part == "range" else None,
               "end_time": end_time if part == "range" else None, "errors": []}
        try:
            row["hours"] = float(working_hours(d, d, part, start_time, end_time, holidays))
        except ValueError as e:
            row["errors"].append(_err("bad_range", str(e)))
        out.append(row)
    return out


async def _overlaps(session, staff_id: str, dates: list, exclude_app_id: str = "") -> dict:
    """{date: 撞到的那張單描述}：自己還占著位子的單（待審／已核准／消假待審），排除自己這張申請單的子單。"""
    if not dates:
        return {}
    d0, d1 = as_date(dates[0]), as_date(dates[-1])
    rows = (await session.execute(select(HrLeaveRequest).where(HrLeaveRequest.staff_id == staff_id)
                                  .where(HrLeaveRequest.status.in_(ACTIVE_STATUSES))
                                  .where(HrLeaveRequest.start_date < midnight_of(d1) + timedelta(days=1))
                                  .where(HrLeaveRequest.end_date >= midnight_of(d0)))).scalars().all()
    out = {}
    for r in rows:
        if exclude_app_id and r.application_id == exclude_app_id:
            continue
        rs, re_ = day_iso(r.start_date), day_iso(r.end_date)
        for d in dates:
            if rs <= d <= re_ and d not in out:
                out[d] = f"與你 {rs}{'～' + re_ if re_ != rs else ''} 的{r.leave_type}（{r.status}）重疊"
    return out


async def evaluate(session, staff, body, today: date | None = None, holidays=None, exclude_app_id: str = "") -> dict:
    """三步的試算（不寫入）：{needed_hours, days, dates:[slot…], inventory, takes, remain, children, errors, warnings, proof_required}。
    errors 非空＝不能送；remain>0（挑不夠）也是 error。"""
    today = today or date.today()
    holidays = holidays if holidays is not None else await leave_service.holidays_map(session)
    errors, warnings = [], []
    raw_dates = list(body.dates or [])
    if not raw_dates and getattr(body, "start_date", None):
        # 起迄模式：展開成工作日（週末／假日自動跳過），迄日沒給＝同一天
        d0, d1 = as_date(body.start_date), as_date(getattr(body, "end_date", None) or body.start_date)
        if d0 and d1 and d1 >= d0:
            raw_dates = [d.isoformat() for d in workdays_between(d0, d1, holidays)]
        if not raw_dates:
            return {"needed_hours": 0, "days": 0, "dates": [], "inventory": [], "takes": [], "remain": 0, "children": [],
                    "errors": [_err("bad_range", "期間內沒有工作日（週末／假日不用請假）" if d0 and d1 and d1 >= d0 else "迄日不可早於起日")],
                    "warnings": [], "proof_required": False}
    try:
        dates = normalize_dates(raw_dates)
    except ValueError as e:
        return {"needed_hours": 0, "days": 0, "dates": [], "inventory": [], "takes": [], "remain": 0, "children": [],
                "errors": [_err("bad_range", str(e))], "warnings": [], "proof_required": False}
    slots = day_slots(dates, body.part, body.start_time, body.end_time, holidays)
    clash = await _overlaps(session, staff.id, dates, exclude_app_id)
    for s in slots:
        if s["date"] in clash:
            s["errors"].append(_err("overlap", clash[s["date"]]))
    needed = round(sum(s["hours"] for s in slots if not s["errors"]), 2)
    inv = await inventory(session, staff.id, today, exclude_app_id)
    by_id = {i["id"]: i for i in inv}
    picks = []
    for it in (body.items or []):
        iid = (it.get("id") if isinstance(it, dict) else getattr(it, "id", "")) or ""
        if iid not in by_id:
            errors.append(_err("bad_item", f"清單裡沒有這筆假：{iid}"))
            continue
        picks.append(by_id[iid])
    takes, remain = fit_items(picks, needed)
    if slots and not picks:
        errors.append(_err("no_items", "還沒挑要扣的假"))
    elif remain > 0:
        errors.append(_err("insufficient", f"挑的假只夠 {needed - remain:g} 小時，還差 {remain:g} 小時，再挑一筆"))
    children = plan_children([s for s in slots if not s["errors"]], takes) if not errors else []
    # 警告：不到一週、撞拍攝、病假年上限
    if dates:
        nw = notice_warning(as_date(dates[0]), today)
        if nw:
            warnings.append(_err("notice_short", nw))
        for sh in await leave_service.shoot_conflicts_for(session, staff.id, staff.name, as_date(dates[0]), as_date(dates[-1])):
            if sh["date"] in dates:
                warnings.append(_err("shoot_conflict", f"{sh['date']} 你在「{sh['title']}」拍攝名單"))
    sick_h = sum(t["take"] for t in takes if t["kind"] == "病假")
    if sick_h:
        used = (await leave_service.sick_used_for(session, [staff.id], today.year)).get(staff.id, 0.0)
        if used + hours_to_days(sick_h) > SICK_CAP_DAYS:
            warnings.append(_err("sick_cap", f"今年病假已用 {used:g} 天，這次後共 {used + hours_to_days(sick_h):g} 天，超過 {SICK_CAP_DAYS} 天上限"))
    for s in slots:
        for e in s["errors"]:
            errors.append(_err(e["code"], f"{s['date'][5:].replace('-', '/')}：{e['msg']}"))
    return {"needed_hours": needed, "days": hours_to_days(needed), "dates": slots, "inventory": inv, "takes": takes, "remain": remain,
            "children": children, "errors": errors, "warnings": warnings,
            "proof_required": any(t.get("proof_required") for t in takes)}


# ── 寫入 ───────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


async def save(session, staff, username: str, body, ev: dict, app: HrLeaveApplication | None = None) -> HrLeaveApplication:
    """建新單或改待審的單：申請單本體＋重建子單（舊子單只在待審時存在，全刪重建）。呼叫端已確認 ev['errors'] 空。"""
    dates = [s["date"] for s in ev["dates"]]
    items = [{"id": t["id"], "kind": t["kind"], "credit_id": t.get("credit_id"), "label": t.get("label", ""), "hours": t["take"]}
             for t in ev["takes"] if float(t.get("take") or 0) > 0]      # 挑了但沒扣到的（已經夠了）不存
    if app is None:
        app = HrLeaveApplication(id=uuid.uuid4().hex[:12], staff_id=staff.id, staff_name=staff.name, status="待審", created_by=username)
        session.add(app)
    else:
        for c in await children_of(session, app.id):
            await session.delete(c)
    app.dates, app.items = json.dumps(dates, ensure_ascii=False), json.dumps(items, ensure_ascii=False)
    app.part = (body.part or "all").strip() or "all"
    app.start_time = (body.start_time or "").strip() or None if app.part == "range" else None
    app.end_time = (body.end_time or "").strip() or None if app.part == "range" else None
    app.hours = float(ev["needed_hours"])
    app.reason = (body.reason or "").strip() or None
    await session.flush()
    for ch in ev["children"]:
        session.add(HrLeaveRequest(
            id=uuid.uuid4().hex[:12], staff_id=staff.id, staff_name=staff.name, application_id=app.id,
            leave_type=ch["kind"], start_date=midnight_of(as_date(ch["date"])), end_date=midnight_of(as_date(ch["date"])),
            hours=ch["hours"], days=hours_to_days(ch["hours"]), part=ch["part"],
            start_time=ch["start_time"] if ch["part"] == "range" else None, end_time=ch["end_time"] if ch["part"] == "range" else None,
            reason=app.reason, status="待審", created_by=username, proof_path=app.proof_path,   # 編輯重建也帶著證明
            alloc_plan=json.dumps(ch.get("allocations") or [], ensure_ascii=False),
        ))
    return app


async def approve(session, app: HrLeaveApplication, actor: str, today: date | None = None) -> list:
    """待審 → 已核准（整張）：每張子單照 alloc_plan 從指定的那幾筆扣（不夠 422，一筆都不扣）；要附證明的沒附 422。回子單 id 清單（給日曆同步）。"""
    if app.status != "待審":
        raise HTTPException(status_code=409, detail=f"此單狀態是「{app.status}」，只有待審可核准")
    items = _loads(app.items, [])
    if any(RECORD_META.get(i.get("kind"), {}).get("proof") for i in items) and not (app.proof_path or "").strip():
        kinds = "／".join(sorted({i["kind"] for i in items if RECORD_META.get(i.get("kind"), {}).get("proof")}))
        raise HTTPException(status_code=422, detail=f"{kinds}要先附上證明（員工在假勤頁上傳）才能核准")
    children = await children_of(session, app.id)
    all_credits = (await leave_service.credits_for(session, [app.staff_id])).get(app.staff_id, [])
    credits = {c["id"]: c for c in all_credits}
    live = {c["id"] for c in usable_credits(all_credits, on=today or date.today())}    # 可用、已生效、未到期（同 allocate 的判準）
    need: dict = {}
    for ch in children:
        for cid, h in _loads(ch.alloc_plan, []):
            need[cid] = need.get(cid, 0.0) + float(h)
    for cid, h in need.items():
        c = credits.get(cid)
        if c is None or cid not in live:
            raise HTTPException(status_code=422, detail=f"「{(c or {}).get('reason') or cid}」現在不能扣（到期、還沒生效或不是可用），請員工改挑別筆")
        if round(c["remaining"], 2) + 1e-9 < h:
            raise HTTPException(status_code=422, detail=f"「{c.get('reason') or cid}」剩 {c.get('remaining', 0):g} 小時、要扣 {h:g}，先到時數帳看一下")
    now = _now()
    for ch in children:
        for cid, h in _loads(ch.alloc_plan, []):
            session.add(HrLeaveAllocation(id=uuid.uuid4().hex[:12], request_id=ch.id, credit_id=cid, hours=float(h)))
        ch.status, ch.approved_by, ch.approved_at, ch.reject_note = "已核准", actor, now, None
    app.status, app.approved_by, app.approved_at, app.reject_note = "已核准", actor, now, None
    return [ch.id for ch in children]


async def set_status(session, app: HrLeaveApplication, status: str, actor: str = "", note: str = "", release: bool = False) -> list:
    """整張連子單一起改狀態（退回／撤回／消假待審／消假退回→已核准）。release＝把子單吃掉的 credit 放回去。回子單 id。"""
    children = await children_of(session, app.id)
    now = _now()
    for ch in children:
        if release:
            await leave_service.release_allocations(session, ch.id)
        ch.status = status
        if status == "已退回":
            ch.reject_note = note or ch.reject_note
        if status == "消假待審" and note:
            ch.cancel_note = note
        if actor and status in ("已退回", "已撤回"):
            ch.approved_by, ch.approved_at = actor, now
    app.status = status
    if status == "已退回":
        app.reject_note = note or app.reject_note
    if status == "消假待審" and note:
        app.cancel_note = note
    if actor and status in ("已退回", "已撤回"):
        app.approved_by, app.approved_at = actor, now
    return [ch.id for ch in children]


async def list_for_staff(session, staff_id: str, limit: int = 20, today: date | None = None, holidays=None) -> list:
    rows = (await session.execute(select(HrLeaveApplication).where(HrLeaveApplication.staff_id == staff_id)
                                  .order_by(HrLeaveApplication.created_at.desc()).limit(limit))).scalars().all()
    return [application_dict(a, None, today, holidays) for a in rows]


async def list_all(session, status: str = "", limit: int = 200, today: date | None = None, holidays=None, with_children: bool = True) -> list:
    stmt = select(HrLeaveApplication)
    if status:
        stmt = stmt.where(HrLeaveApplication.status == status)
    rows = (await session.execute(stmt.order_by(HrLeaveApplication.created_at.desc()).limit(limit))).scalars().all()
    out = []
    for a in rows:
        out.append(application_dict(a, await children_of(session, a.id) if with_children else None, today, holidays))
    return out
