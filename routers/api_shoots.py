"""routers/api_shoots.py — 拍攝場次（行事曆）：拍攝日＋使用器材，連專案與 Google 日曆。

規劃：docs/SHOOT_CALENDAR_PLAN.md。
- 場次 `crm_shoots`；器材不另建表，沿用 `equipment_checkouts`（加 shoot_id）：登記＝預約列（out_at 空），
  「已領」填 out_at＋器材轉出勤、「已還」填 returned_at＋器材轉在庫 —— 狀態翻轉跟器材庫走同一份
  helper（routers.api_equipment.mark_checked_out / mark_returned）。
- 每次寫入後重算專案 `crm_projects.shoot_date`（core.shoot_logic.derive_project_shoot_date）。
- Google 日曆同步 best-effort：失敗只寫 sync_error，不擋操作；/resync 可重試。
守衛：讀 check_logged_in；寫 check_admin_or_module(request, "crm_projects")（拍攝排程屬專案管理）；
日曆設定 check_admin。
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from core.auth import check_admin, check_admin_or_module, check_logged_in, payload_grants
from core.ledger import not_mine   # 手機版只看母公司案（同 api_crm_mobile._company_projects）：私帳案的場次不列、也不准建
from core.hr_logic import day_iso, iso_ts, tw_day
from routers.crm._shared import _check_project_write_auth, _parse_shoot_date
from services import google_calendar as gc
from core.schemas import (CalendarConfigPayload, ShootCreate, ShootEquipmentPayload,
                          ShootStatusPayload, ShootUpdate)
from core.shoot_logic import (CANCELLED, EQUIPMENT_STATES, SCHEDULED, SHOOT_STATUSES, checkout_state,
                              derive_project_shoot_date, event_body, overlaps)
# api_crm_mobile 那邊反過來在函式內延遲 import 這裡的 shoots_for_project，所以這裡可以直接 import
from routers.api_crm_mobile import _company_projects, _slim_project

try:
    from sqlalchemy import or_, select
    from db.models import (Client, CrmProject, CrmShoot, CrmStaff, Equipment, EquipmentCheckout,
                           PreprodLocation)
except ImportError:  # 機隊 agent 沒裝 DB 套件
    pass

from core.money import MoneyRedactRoute

# route_class=MoneyRedactRoute：/options 帶 _slim_project（合約金額、已收）給行事曆表單，沒 money_view 的人
# 出口統一抹掉（同 api_crm_mobile／routers/crm）—— 2026-09-06 code review 抓到這支漏了
router = APIRouter(prefix="/api/v1/shoots", tags=["shoots"],
                   dependencies=[Depends(check_logged_in)], route_class=MoneyRedactRoute)

_TW = ZoneInfo("Asia/Taipei")
WRITE_MODULE = "crm_projects"


_check_write = _check_project_write_auth      # crm_projects 寫入守衛只有 routers.crm._shared 一份工廠


def _can_write(request: Request) -> bool:
    return payload_grants(check_logged_in(request), WRITE_MODULE)


from core.db_guard import db_factory_or_503 as _require_factory  # noqa: E402


def _today() -> date:
    return datetime.now(_TW).date()


def _parse_date(raw, field: str, required: bool = False) -> date | None:
    raw = (raw or "").strip() if isinstance(raw, str) else raw
    if not raw:
        if required:
            raise HTTPException(status_code=422, detail=f"{field} 必填")
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} 格式須為 YYYY-MM-DD")


def _parse_time(raw, field: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        time.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} 格式須為 HH:MM")
    return raw[:5]


_d = tw_day          # DB 欄位（date 或 datetime）→ date：規則只有 core.hr_logic 一份（timestamptz 走台北日）
_iso = day_iso


_ts = iso_ts


def _crew_list(raw) -> list:
    try:
        v = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        v = []
    out = []
    for c in v if isinstance(v, list) else []:
        if isinstance(c, dict) and (c.get("name") or c.get("staff_id")):
            out.append({"staff_id": str(c.get("staff_id") or ""), "name": str(c.get("name") or "")[:64]})
    return out


# ── 讀：把場次組成 ShootRow ──────────────────────────────

async def _rows_for(session, shoots: list) -> list[dict]:
    """批次組 ShootRow（不 N+1）：專案／客戶、地點、器材預約列、衝突。"""
    if not shoots:
        return []
    ids = [s.id for s in shoots]
    pids = {s.project_id for s in shoots if s.project_id}
    lids = {s.location_id for s in shoots if s.location_id}
    proj = {}
    if pids:
        for p, cname in (await session.execute(
                select(CrmProject, Client.short_name).outerjoin(Client, Client.id == CrmProject.client_id)
                .where(CrmProject.id.in_(pids)))).all():
            proj[p.id] = (p.name or "", cname or "")
    locs = {}
    if lids:
        for lid, name in (await session.execute(
                select(PreprodLocation.id, PreprodLocation.name).where(PreprodLocation.id.in_(lids)))).all():
            locs[lid] = name or ""
    checkouts = (await session.execute(
        select(EquipmentCheckout).where(EquipmentCheckout.shoot_id.in_(ids)))).scalars().all()
    eids = {c.equipment_id for c in checkouts}
    equip = {}
    if eids:
        for e in (await session.execute(select(Equipment).where(Equipment.id.in_(eids)))).scalars().all():
            equip[e.id] = e
    # 衝突：同器材、別場（未取消）的未歸還列，日期區間重疊
    others = []
    if eids:
        others = (await session.execute(
            select(EquipmentCheckout, CrmShoot)
            .join(CrmShoot, CrmShoot.id == EquipmentCheckout.shoot_id)
            .where(EquipmentCheckout.equipment_id.in_(eids),
                   EquipmentCheckout.returned_at.is_(None),
                   EquipmentCheckout.shoot_id.notin_(ids),
                   CrmShoot.status != CANCELLED))).all()
    by_shoot: dict[str, list] = {s.id: [] for s in shoots}
    for c in checkouts:
        by_shoot.setdefault(c.shoot_id, []).append(c)
    out = []
    for s in shoots:
        d0, d1 = _d(s.date), _d(s.end_date)
        gear = []
        for c in sorted(by_shoot.get(s.id, []), key=lambda x: (equip.get(x.equipment_id).category or "", equip.get(x.equipment_id).name or "") if equip.get(x.equipment_id) else ("", "")):
            e = equip.get(c.equipment_id)
            conflict = False
            if c.returned_at is None and s.status != CANCELLED:
                for oc, osh in others:
                    if oc.equipment_id == c.equipment_id and overlaps(d0, d1, _d(osh.date), _d(osh.end_date)):
                        conflict = True
                        break
            gear.append({
                "checkout_id": c.id, "equipment_id": c.equipment_id,
                "name": (e.name if e else "") or "", "category": (e.category if e else "") or "",
                "state": checkout_state(c.out_at, c.returned_at), "conflict": conflict,
            })
        pname, cname = proj.get(s.project_id, ("", ""))
        crew = _crew_list(s.crew)
        out.append({
            "id": s.id, "project_id": s.project_id or "",
            "project_name": pname, "client_short_name": cname,
            "title": s.title or "", "date": _iso(s.date), "end_date": _iso(s.end_date),
            "start_time": s.start_time or "", "end_time": s.end_time or "",
            "location_id": s.location_id or "", "location_name": locs.get(s.location_id, ""),
            "location_text": s.location_text or "",
            "crew": crew, "notes": s.notes or "", "status": s.status or SCHEDULED,
            "cost_group_id": s.cost_group_id or "",
            "google_event_id": s.google_event_id or "", "synced_at": _ts(s.synced_at),
            "sync_error": s.sync_error or "",
            "crew_count": len(crew), "equipment_count": len(gear), "equipment": gear,
        })
    return out


async def _row(session, shoot) -> dict:
    return (await _rows_for(session, [shoot]))[0]


async def _project_or_404_not_mine(session, pid: str) -> str:
    """場次只能掛母公司案（手機版看不到私帳案，同 api_crm_mobile._company_projects）。"""
    if (await session.execute(select(CrmProject.id).where(CrmProject.id == pid, not_mine(CrmProject.entity)))).scalar() is None:
        raise HTTPException(status_code=404, detail="找不到專案")
    return pid


def _visible_shoots():
    """看得到的場次＝掛在母公司案上的（專案推到私帳後場次對所有人都消失）。清單與 by-id 同一句。"""
    return (select(CrmShoot).join(CrmProject, CrmProject.id == CrmShoot.project_id)
            .where(not_mine(CrmProject.entity)))


async def _shoot_or_404(session, sid: str):
    s = (await session.execute(_visible_shoots().where(CrmShoot.id == sid))).scalars().first()
    if not s:
        raise HTTPException(status_code=404, detail="找不到這場拍攝")
    return s


def _due_at(s):
    """預約列的應還日＝結束日（沒有就拍攝日）＋1。"""
    return _parse_shoot_date(((_d(s.end_date) or _d(s.date)) + timedelta(days=1)).isoformat())


async def shoots_for_project(session, project_id: str, upcoming: int = 5, past: int = 3) -> list[dict]:
    """專案抽屜用：未來 N 場（含今天）＋最近 M 場。"""
    today = _today()
    up = (await session.execute(
        select(CrmShoot).where(CrmShoot.project_id == project_id, CrmShoot.date >= today)
        .order_by(CrmShoot.date, CrmShoot.start_time).limit(upcoming))).scalars().all()
    pa = (await session.execute(
        select(CrmShoot).where(CrmShoot.project_id == project_id, CrmShoot.date < today)
        .order_by(CrmShoot.date.desc()).limit(past))).scalars().all()
    rows = await _rows_for(session, list(up) + list(reversed(pa)))
    rows.sort(key=lambda r: (r["date"] or "", r["start_time"]))
    return rows


# ── 寫：專案拍攝日、日曆同步 ────────────────────────────

async def _recompute_project_shoot_date(session, project_id: str) -> None:
    """專案的拍攝日從場次算（core.shoot_logic.derive_project_shoot_date）。"""
    if not project_id:
        return
    rows = (await session.execute(
        select(CrmShoot.date, CrmShoot.status).where(CrmShoot.project_id == project_id))).all()
    d = derive_project_shoot_date([(_d(r[0]), r[1]) for r in rows], _today())
    if not d:
        return      # 沒有活著的場次＝算不出來：不動專案上手填的拍攝日（2026-09-06 review：全取消曾把它洗成 NULL）
    p = (await session.execute(select(CrmProject).where(CrmProject.id == project_id))).scalars().first()
    if p is not None:
        # 日期欄寫入慣例＝該日 UTC 午夜（routers/crm/_shared._parse_shoot_date；tests/unit/test_shoot_date_convention）
        # —— 寫台北午夜會存成前一天 16:00Z，手機／桌機取 ISO 前 10 碼就少一天
        p.shoot_date = _parse_shoot_date(d.isoformat())


async def _sync_calendar(sid: str) -> dict:
    """best-effort 同步 Google 日曆，回**最新的 ShootRow**（寫入端點的唯一收尾，見 _finish）。
    憑證／日曆沒設就不動同步欄位（保持中性）；失敗只寫 sync_error。"""
    sa, cal_id, _err = await gc.load_config()
    factory = _require_factory()
    async with factory() as session:
        s = await _shoot_or_404(session, sid)
        row = await _row(session, s)
        if sa is None or not cal_id:
            return row
        now = datetime.now(timezone.utc)
        if s.status == CANCELLED:
            ok, e = (True, "") if not s.google_event_id else await asyncio.to_thread(gc.delete_event, sa, cal_id, s.google_event_id)
            if ok:
                s.google_event_id, s.synced_at, s.sync_error = None, now, None
            else:
                s.sync_error = e[:500]
        else:
            eid, e = await asyncio.to_thread(gc.upsert_event, sa, cal_id, s.google_event_id, event_body(row))
            if eid:
                s.google_event_id, s.synced_at, s.sync_error = eid, now, None
            else:
                s.sync_error = (e or "同步失敗")[:500]
        await session.commit()
        row.update(google_event_id=s.google_event_id or "", synced_at=_ts(s.synced_at), sync_error=s.sync_error or "")
        return row


async def _finish(sid: str) -> dict:
    """寫入端點的收尾：同步日曆 → 回 {"shoot": 最新 ShootRow}。"""
    return {"shoot": await _sync_calendar(sid)}


def _apply_fields(s, req, partial: bool) -> None:
    """ShootCreate / ShootUpdate 的欄位落到 model；partial＝只動有給的欄位。"""
    def given(k):
        return (k in req.model_fields_set) if partial else True
    if given("title"):
        s.title = (req.title or "").strip()[:128] or None
    if given("date") and (req.date or not partial):
        s.date = _parse_date(req.date, "date", required=True)
    if given("end_date"):
        ed = _parse_date(req.end_date, "end_date")
        s.end_date = ed if ed and ed > _d(s.date) else None
    if given("start_time"):
        s.start_time = _parse_time(req.start_time, "start_time") or None
    if given("end_time"):
        s.end_time = _parse_time(req.end_time, "end_time") or None
    if given("location_id"):
        s.location_id = (req.location_id or "").strip() or None
    if given("location_text"):
        s.location_text = (req.location_text or "").strip()[:255] or None
    if given("crew"):
        s.crew = json.dumps(_crew_list(req.crew), ensure_ascii=False)
    if given("notes"):
        s.notes = (req.notes or "").strip() or None
    if given("cost_group_id"):
        s.cost_group_id = (req.cost_group_id or "").strip() or None


async def _refresh_open_reservations(session, s) -> None:
    """這一場還沒歸還的預約列：應還日＝結束日＋1、專案跟著場次。"""
    for c in (await session.execute(
            select(EquipmentCheckout).where(EquipmentCheckout.shoot_id == s.id,
                                            EquipmentCheckout.returned_at.is_(None)))).scalars().all():
        c.due_at, c.project_id = _due_at(s), s.project_id


async def _reserve_equipment(session, s, equipment_ids: list[str]) -> None:
    """器材差異：新勾＝加預約列；取消勾且還沒領＝刪列；已領的不能拿掉（422）。"""
    want = [str(x).strip() for x in (equipment_ids or []) if str(x).strip()]
    want_set = set(want)
    if want_set:
        found = set((await session.execute(
            select(Equipment.id).where(Equipment.id.in_(want_set)))).scalars().all())
        missing = want_set - found
        if missing:
            raise HTTPException(status_code=422, detail="有器材不存在或已除役")
    existing = (await session.execute(
        select(EquipmentCheckout).where(EquipmentCheckout.shoot_id == s.id))).scalars().all()
    have = {c.equipment_id: c for c in existing}
    for eid, c in have.items():
        if eid not in want_set:
            if c.out_at is not None and c.returned_at is None:
                raise HTTPException(status_code=422, detail="已領走的器材不能從場次拿掉，請先歸還")
            await session.delete(c)
    for eid in want:
        if eid in have:
            continue
        session.add(EquipmentCheckout(
            id=uuid.uuid4().hex, equipment_id=eid, project_id=s.project_id, shoot_id=s.id,
            person=None, out_at=None,
            due_at=_due_at(s), returned_at=None))


# ── 端點 ───────────────────────────────────────────────

@router.get("/options")
async def shoot_options(request: Request, date_: str = Query("", alias="date"), end_date: str = Query("")):
    """表單字彙一次給：狀態、器材（帶日期時附 busy）、地點、人員、專案。"""
    d0 = _parse_date(date_, "date")
    d1 = _parse_date(end_date, "end_date") if d0 else None
    factory = _require_factory()
    async with factory() as session:
        equips = (await session.execute(
            select(Equipment).where(or_(Equipment.status.is_(None), Equipment.status != "除役"))
            .order_by(Equipment.category, Equipment.name))).scalars().all()
        busy: dict[str, dict] = {}
        if d0 and equips:
            rows = (await session.execute(
                select(EquipmentCheckout, CrmShoot, CrmProject.name)
                .outerjoin(CrmShoot, CrmShoot.id == EquipmentCheckout.shoot_id)
                .outerjoin(CrmProject, CrmProject.id == EquipmentCheckout.project_id)
                .where(EquipmentCheckout.returned_at.is_(None)))).all()
            for c, sh, pname in rows:
                if sh is not None:
                    if sh.status == CANCELLED or not overlaps(d0, d1, _d(sh.date), _d(sh.end_date)):
                        continue
                    busy[c.equipment_id] = {"shoot_id": sh.id, "project_name": pname or "", "date": _iso(sh.date)}
                elif c.out_at is not None:   # 器材庫直接領走、還沒還的（不是場次）
                    busy.setdefault(c.equipment_id, {"shoot_id": "", "project_name": pname or (c.person or ""),
                                                     "date": _iso(c.out_at)})
        locs = (await session.execute(
            select(PreprodLocation.id, PreprodLocation.name).order_by(PreprodLocation.name))).all()
        staff = (await session.execute(
            select(CrmStaff.id, CrmStaff.name, CrmStaff.role, CrmStaff.status).order_by(CrmStaff.name))).all()
        projects = (await session.execute(
            _company_projects().order_by(CrmProject.updated_at.desc(), CrmProject.id).limit(200))).all()
    cats = list(dict.fromkeys((e.category or "其他") for e in equips))
    return {
        "statuses": list(SHOOT_STATUSES), "scheduled_status": SCHEDULED,
        "done_status": SHOOT_STATUSES[1], "cancelled_status": CANCELLED,
        "equipment_states": list(EQUIPMENT_STATES),
        "categories": cats,
        "equipment": [{"id": e.id, "name": e.name or "", "category": e.category or "其他",
                       "status": e.status or "在庫", "busy": busy.get(e.id)} for e in equips],
        "locations": [{"id": i, "name": n or ""} for i, n in locs],
        "staff": [{"id": i, "name": n or "", "role": r or ""} for i, n, r, st in staff if (st or "在職") != "離職"],
        "projects": [_slim_project(p, cname) for p, cname in projects],
        "me": {"can_write": _can_write(request)},
    }


@router.get("")
async def list_shoots(request: Request, from_: str = Query("", alias="from"), to: str = Query(""),
                      project_id: str = Query(""), status: str = Query(""),
                      limit: int = Query(50), offset: int = Query(0)):
    d_from = _parse_date(from_, "from") or (_today() - timedelta(days=7))
    d_to = _parse_date(to, "to")
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    factory = _require_factory()
    async with factory() as session:
        q = _visible_shoots().where(CrmShoot.date >= d_from)
        if d_to:
            q = q.where(CrmShoot.date <= d_to)
        if project_id:
            q = q.where(CrmShoot.project_id == project_id)
        if status:
            q = q.where(CrmShoot.status == status)
        from sqlalchemy import func
        total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
        rows = (await session.execute(
            q.order_by(CrmShoot.date, CrmShoot.start_time, CrmShoot.id).limit(limit).offset(offset))).scalars().all()
        out = await _rows_for(session, list(rows))
    return {"shoots": out, "total": int(total)}


@router.get("/calendar/status")
async def calendar_status(request: Request):
    return await _calendar_status()


async def _calendar_status() -> dict:
    sa, cal_id, err = await gc.load_config()
    factory = _require_factory()
    async with factory() as session:
        last = (await session.execute(
            select(CrmShoot).where(CrmShoot.synced_at.isnot(None))
            .order_by(CrmShoot.synced_at.desc()).limit(1))).scalars().first()
        bad = (await session.execute(
            select(CrmShoot).where(CrmShoot.sync_error.isnot(None))
            .order_by(CrmShoot.updated_at.desc()).limit(1))).scalars().first()
    return {
        "configured": bool(sa and cal_id),
        "calendar_id": cal_id,
        "service_account_email": (sa or {}).get("client_email", ""),
        "last_sync_at": _ts(last.synced_at) if last else None,
        "last_error": (bad.sync_error if bad else "") or err or "",
    }


@router.put("/calendar/config")
async def calendar_config(req: CalendarConfigPayload, request: Request):
    check_admin(request)
    from config import load_settings, save_settings
    s = load_settings()
    g = dict(s.get("google_calendar") or {})
    g["calendar_id"] = (req.calendar_id or "").strip()
    s["google_calendar"] = g
    save_settings(s)
    gc.invalidate_config()          # 快取的舊設定作廢，下面的狀態才是剛存的那份
    return await _calendar_status()


@router.post("/calendar/test")
async def calendar_test(request: Request):
    check_admin(request)
    sa, cal_id, err = await gc.load_config()
    if sa is None:
        return {"ok": False, "message": err}
    ok, msg, summary = await asyncio.to_thread(gc.test_connection, sa, cal_id)
    return {"ok": ok, "message": msg, "calendar_summary": summary}


@router.get("/{sid}")
async def get_shoot(sid: str, request: Request):
    factory = _require_factory()
    async with factory() as session:
        s = await _shoot_or_404(session, sid)
        return {"shoot": await _row(session, s)}


@router.post("")
async def create_shoot(req: ShootCreate, request: Request):
    user = _check_write(request)
    pid = (req.project_id or "").strip()
    if not pid:
        raise HTTPException(status_code=422, detail="project_id 必填")
    factory = _require_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        await _project_or_404_not_mine(session, pid)
        s = CrmShoot(id=uuid.uuid4().hex, project_id=pid, status=SCHEDULED,
                     created_by=(user or {}).get("username") or "", created_at=now, updated_at=now)
        _apply_fields(s, req, partial=False)
        session.add(s)
        await session.flush()
        await _reserve_equipment(session, s, req.equipment_ids or [])
        await _recompute_project_shoot_date(session, pid)
        await session.commit()
        sid = s.id
    return await _finish(sid)


@router.put("/{sid}")
async def update_shoot(sid: str, req: ShootUpdate, request: Request):
    _check_write(request)
    factory = _require_factory()
    async with factory() as session:
        s = await _shoot_or_404(session, sid)
        old_pid = s.project_id
        if "project_id" in req.model_fields_set and (req.project_id or "").strip():
            s.project_id = await _project_or_404_not_mine(session, req.project_id.strip())
        _apply_fields(s, req, partial=True)
        s.updated_at = datetime.now(timezone.utc)
        if "equipment_ids" in req.model_fields_set and req.equipment_ids is not None:
            await _reserve_equipment(session, s, req.equipment_ids)
        await _refresh_open_reservations(session, s)      # 日期／專案改了，留著的預約列跟著走（只寫一次）
        await _recompute_project_shoot_date(session, s.project_id)
        if old_pid and old_pid != s.project_id:
            await _recompute_project_shoot_date(session, old_pid)
        await session.commit()
    return await _finish(sid)


@router.post("/{sid}/status")
async def set_shoot_status(sid: str, req: ShootStatusPayload, request: Request):
    _check_write(request)
    status = (req.status or "").strip()
    if status not in SHOOT_STATUSES:
        raise HTTPException(status_code=422, detail="status 必須是 " + "／".join(SHOOT_STATUSES))
    factory = _require_factory()
    async with factory() as session:
        s = await _shoot_or_404(session, sid)
        s.status = status
        s.updated_at = datetime.now(timezone.utc)
        if status == CANCELLED:
            # 取消：還沒領的預約列直接放掉；已領的留著等歸還
            for c in (await session.execute(
                    select(EquipmentCheckout).where(EquipmentCheckout.shoot_id == s.id,
                                                    EquipmentCheckout.out_at.is_(None),
                                                    EquipmentCheckout.returned_at.is_(None)))).scalars().all():
                await session.delete(c)
        await _recompute_project_shoot_date(session, s.project_id)
        await session.commit()
    return await _finish(sid)


async def _pickup_or_return(sid: str, req: ShootEquipmentPayload, request: Request, pickup: bool) -> dict:
    _check_write(request)
    from routers.api_equipment import mark_checked_out, mark_returned
    only = {str(x) for x in (req.equipment_ids or []) if str(x)}
    factory = _require_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        s = await _shoot_or_404(session, sid)
        rows = (await session.execute(
            select(EquipmentCheckout, Equipment).join(Equipment, Equipment.id == EquipmentCheckout.equipment_id)
            .where(EquipmentCheckout.shoot_id == s.id, EquipmentCheckout.returned_at.is_(None)))).all()
        n = 0
        for c, e in rows:
            if only and c.equipment_id not in only:
                continue
            if pickup and c.out_at is None:
                mark_checked_out(e, c, now, person=(s.created_by or ""))
                n += 1
            elif not pickup and c.out_at is not None:
                mark_returned(e, c, now)
                n += 1
        if n == 0:
            raise HTTPException(status_code=409, detail="沒有可以" + ("領取" if pickup else "歸還") + "的器材")
        s.updated_at = now
        await session.commit()
        return {"shoot": await _row(session, s)}      # s 還在 session 裡（expire_on_commit=False），不必再查一次


@router.post("/{sid}/equipment/pickup")
async def pickup_equipment(sid: str, req: ShootEquipmentPayload, request: Request):
    return await _pickup_or_return(sid, req, request, pickup=True)


@router.post("/{sid}/equipment/return")
async def return_equipment(sid: str, req: ShootEquipmentPayload, request: Request):
    return await _pickup_or_return(sid, req, request, pickup=False)


@router.post("/{sid}/resync")
async def resync_shoot(sid: str, request: Request):
    _check_write(request)
    return await _finish(sid)          # _finish → _sync_calendar 自己會 _shoot_or_404
