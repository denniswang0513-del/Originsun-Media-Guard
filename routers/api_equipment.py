"""
api_equipment.py — 器材管理 API（B4，docs/BIZ_PLAN.md B4 段）

成本真相的最後一塊：器材 CRUD + 封面照（uploads/equipment/{eid}/）+ 領用/歸還
+ 保養履歷 + 統計（年度使用天數/稼動率/月折舊直線攤提）。守衛 = 管理員 OR
equipment / preprod_plan 模組。DB 三表：equipment / equipment_checkouts /
equipment_maintenance（create_all 自建）。
"""

import os
import re
import shutil
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, File, HTTPException, Request, UploadFile  # type: ignore

from core.auth import check_admin_or_module, tab_modules
from core.schemas import (
    EquipmentCheckoutPayload,
    EquipmentMaintenancePayload,
    EquipmentPayload,
    EquipmentReturnPayload,
)
from routers.crm._shared import project_names_map

router = APIRouter(prefix="/api/v1/equipment", tags=["equipment"])

# 封面檔案落地：<repo>/uploads/equipment/{equipment_id}/（main.py 已 mount /uploads）
_UPLOAD_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# EquipmentPayload 中可直接 setattr 到 model 的欄位（部分更新白名單）
# purchase_date 是 'YYYY-MM-DD' 字串 → datetime，另行處理
_EQUIP_FIELDS = (
    "name", "category", "serial", "purchase_cost", "depreciation_months",
    "status", "note", "cover_url",
)


def _check_auth(request: Request) -> dict:
    return check_admin_or_module(request, *tab_modules("equipment"))


from core.db_guard import db_factory_or_503 as _require_factory


def _parse_date(raw, field: str):
    """'YYYY-MM-DD' → datetime；空值回 None，格式錯 422。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} 格式須為 YYYY-MM-DD")


def _aware(dt):
    """DB 讀回/strptime 可能為 naive — 統一補 UTC tzinfo 再比較。"""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _monthly_depreciation(e) -> int:
    """月折舊（直線攤提）＝ purchase_cost / depreciation_months；除役 = 0。"""
    if (e.status or "") == "除役":
        return 0
    months = e.depreciation_months or 0
    if not e.purchase_cost or months <= 0:
        return 0
    return round(e.purchase_cost / months)


def _usage_stats(checkouts, now):
    """年度使用天數/稼動率：近 365 天視窗內 sum(returned_at−out_at)，
    未歸還算到今天；跨視窗的紀錄只計視窗內重疊段。回 (days, pct)。"""
    win_start = now - timedelta(days=365)
    total_sec = 0.0
    for c in checkouts:
        start = _aware(c.out_at)
        if not start:
            continue
        end = _aware(c.returned_at) or now
        seg_start = max(start, win_start)
        seg_end = min(end, now)
        if seg_end > seg_start:
            total_sec += (seg_end - seg_start).total_seconds()
    days = total_sec / 86400
    return round(days, 1), round(days / 365 * 100, 1)


def _strip_mine_money(d: dict, request) -> dict:
    """§8 錢牆（器材側）：mine 器材對「無我的帳 scope」者抹掉錢欄。

    器材清單本身共用可見（owner 拍板「只有錢區隔」），但 purchase_cost／
    monthly_depreciation 是 owner 私人器材的錢 —— 這個 router 沒掛
    MoneyRedactRoute（core/money 記載的機制邊界），牆只能在這裡自己立。
    刪鍵不歸零（三態慣例，同 core/money.redact）。"""
    if d.get("entity") != "mine":
        return d
    from core.money import redact, viewer_has_mine_scope
    if viewer_has_mine_scope(request):
        return d
    # 🔴 遞迴走 core.money.redact，不要自己列鍵：手列那版只蓋了 purchase_cost
    # 與 monthly_depreciation，詳情裡的 maintenance_total 與每筆保養的 cost
    # 照樣外流（/simplify 2026-08-25）。跟著 MONEY_FIELDS 走，以後加欄自動涵蓋，
    # 也才受 test_money_visibility 的掃描守住。
    return redact(d)


def _assert_mine_writable(equip, request) -> None:
    """🔴 mine 器材只有「有我的帳 scope」的人能改／刪。

    讀的牆（_strip_mine_money）2026-08-25 之前只立在讀那一側，寫這側整個沒有：
    只有 `equipment` 模組的 Lv1 帳號可以直接 `PUT {"purchase_cost": …}` 改寫
    owner 私人器材的成本。牆要兩側都有才叫牆。
    """
    if (getattr(equip, "entity", None) or "parent") != "mine":
        return
    from core.money import viewer_has_mine_scope
    if not viewer_has_mine_scope(request):
        raise HTTPException(status_code=403, detail="這件器材不屬於你可存取的帳本")


def _equip_dict(e) -> dict:
    return {
        "id": e.id,
        "entity": e.entity or "parent",
        "name": e.name,
        "category": e.category or "",
        "serial": e.serial or "",
        "purchase_date": e.purchase_date.strftime("%Y-%m-%d") if e.purchase_date else None,
        "purchase_cost": e.purchase_cost,
        "depreciation_months": e.depreciation_months or 36,
        "status": e.status or "在庫",
        "note": e.note or "",
        "cover_url": e.cover_url or "",
        "monthly_depreciation": _monthly_depreciation(e),
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


def _checkout_dict(c, project_name: str, now) -> dict:
    out_at = _aware(c.out_at)
    due_at = _aware(c.due_at)
    returned_at = _aware(c.returned_at)
    overdue = bool(due_at and not returned_at and due_at < now)
    end = returned_at or now
    days = round((end - out_at).total_seconds() / 86400, 1) if out_at and end > out_at else 0
    return {
        "id": c.id,
        "equipment_id": c.equipment_id,
        "project_id": c.project_id or "",
        "project_name": project_name or "",
        "person": c.person or "",
        "out_at": out_at.isoformat() if out_at else None,
        "due_at": due_at.strftime("%Y-%m-%d") if due_at else None,
        "returned_at": returned_at.isoformat() if returned_at else None,
        "condition_note": c.condition_note or "",
        "days": days,
        "overdue": overdue,
        # 逾期天數以日曆日算；應還日當天 = 0（前端顯示「今日應還」）
        "overdue_days": (now.date() - due_at.date()).days if overdue else 0,
    }


def _maint_dict(m) -> dict:
    return {
        "id": m.id,
        "equipment_id": m.equipment_id,
        "date": m.date.strftime("%Y-%m-%d") if m.date else None,
        "cost": m.cost,
        "note": m.note or "",
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


async def _get_equipment_or_404(session, eid: str):
    from db.models import Equipment
    equip = await session.get(Equipment, eid)
    if not equip:
        raise HTTPException(status_code=404, detail="找不到此器材")
    return equip


# ── 器材 CRUD ─────────────────────────────────────────────


@router.get("")
async def list_equipment(request: Request, q: str = "", category: str = "",
                         status: str = "", entity: str = ""):
    """器材列表 + 篩選（q=名稱/序號、分類、狀態、帳本），附 current_checkout + overdue。

    entity：''＝兩本都看（維持既有行為，清單本身共用可見）、'parent'、'mine'。
    """
    _check_auth(request)
    from core.ledger import ENTITIES
    if entity and entity not in ENTITIES:
        raise HTTPException(status_code=422, detail=f"未知的帳本: {entity}")
    factory = _require_factory()

    from sqlalchemy import or_, select
    from db.models import Equipment, EquipmentCheckout

    async with factory() as session:
        query = select(Equipment).order_by(Equipment.updated_at.desc())
        if entity:
            query = query.where(Equipment.entity == entity)
        if category:
            query = query.where(Equipment.category == category)
        if status:
            query = query.where(Equipment.status == status)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(Equipment.name.ilike(ql), Equipment.serial.ilike(ql)))
        rows = (await session.execute(query)).scalars().all()

        open_rows = (await session.execute(
            select(EquipmentCheckout)
            .where(EquipmentCheckout.returned_at.is_(None))
            .order_by(EquipmentCheckout.out_at.desc())
        )).scalars().all()
        pnames = await project_names_map(session, open_rows)

    open_by_eq = {}
    for c in open_rows:  # 已按 out_at desc — 每件器材取最新未歸還那筆
        open_by_eq.setdefault(c.equipment_id, c)

    now = datetime.now(timezone.utc)
    items = []
    for r in rows:
        d = _strip_mine_money(_equip_dict(r), request)
        c = open_by_eq.get(r.id)
        d["current_checkout"] = _checkout_dict(c, pnames.get(c.project_id) or "", now) if c else None
        d["overdue"] = bool(d["current_checkout"] and d["current_checkout"]["overdue"])
        items.append(d)
    return {"equipment": items}


@router.post("")
async def create_equipment(req: EquipmentPayload, request: Request):
    """新增器材（name 必填）。"""
    _check_auth(request)
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name 必填")
    purchase_date = _parse_date(req.purchase_date, "purchase_date")
    factory = _require_factory()

    from db.models import Equipment

    data = req.model_dump(exclude_unset=True)
    data["name"] = name
    # 🔴 帳本要收得進來。原本 entity 只有讀那側用得到（折舊按它進哪本損益表、
    # 錢牆按它決定抹不抹），寫這側收不了 —— owner 從 UI 新增的私人器材一律
    # 落 parent，折舊靜靜進了母公司損益表，而且 UI 沒有任何辦法搬回去。
    from core.ledger import require_entity
    ent = (data.get("entity") or "parent").strip() or "parent"
    require_entity(request, ent, level="full")
    equip = Equipment(
        id=uuid.uuid4().hex,
        purchase_date=purchase_date,
        entity=ent,
        **{k: v for k, v in data.items() if k in _EQUIP_FIELDS},
    )
    async with factory() as session:
        session.add(equip)
        await session.commit()
        await session.refresh(equip)
    return {"status": "ok", "equipment": _equip_dict(equip)}


@router.get("/{eid}")
async def get_equipment(eid: str, request: Request):
    """器材詳情：欄位 + checkouts 歷史（join 專案名）+ maintenance 歷史 + 統計
    （年度使用天數/稼動率/月折舊/保養費累計）。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import CrmProject, EquipmentCheckout, EquipmentMaintenance

    now = datetime.now(timezone.utc)
    async with factory() as session:
        equip = await _get_equipment_or_404(session, eid)
        checkouts = (await session.execute(
            select(EquipmentCheckout, CrmProject.name)
            .outerjoin(CrmProject, CrmProject.id == EquipmentCheckout.project_id)
            .where(EquipmentCheckout.equipment_id == eid)
            .order_by(EquipmentCheckout.out_at.desc())
        )).all()
        maints = (await session.execute(
            select(EquipmentMaintenance)
            .where(EquipmentMaintenance.equipment_id == eid)
            .order_by(EquipmentMaintenance.date.desc())
        )).scalars().all()

    d = _equip_dict(equip)
    d["checkouts"] = [_checkout_dict(c, pname or "", now) for c, pname in checkouts]
    d["current_checkout"] = next((c for c in d["checkouts"] if not c["returned_at"]), None)
    d["overdue"] = bool(d["current_checkout"] and d["current_checkout"]["overdue"])
    d["maintenance"] = [_maint_dict(m) for m in maints]
    usage_days, utilization = _usage_stats([c for c, _ in checkouts], now)
    d["stats"] = {
        "usage_days_365": usage_days,
        "utilization_pct": utilization,
        "monthly_depreciation": _monthly_depreciation(equip),
        "maintenance_total": sum(m.cost or 0 for m in maints),
    }
    # redact 是遞迴的 —— stats 裡的 monthly_depreciation／maintenance_total
    # 與 maintenance[].cost 一起處理掉，不用再各補一刀
    return {"equipment": _strip_mine_money(d, request)}


@router.put("/{eid}")
async def update_equipment(eid: str, req: EquipmentPayload, request: Request):
    """部分更新：只動 payload 有帶的欄位（purchase_date 空字串 = 清除）。"""
    _check_auth(request)
    factory = _require_factory()

    data = req.model_dump(exclude_unset=True)
    if "name" in data and not (data["name"] or "").strip():
        raise HTTPException(status_code=422, detail="name 不可為空")
    # 換帳本＝把這件器材的折舊整個搬到另一本損益表，那不是編輯。比照兩本帳
    # 其他寫入端點：更新一律不得換帳本。
    if data.get("entity") is not None:
        raise HTTPException(status_code=422, detail="不能用更新換帳本")

    async with factory() as session:
        equip = await _get_equipment_or_404(session, eid)
        _assert_mine_writable(equip, request)
        for k, v in data.items():
            if k in _EQUIP_FIELDS:
                setattr(equip, k, v)
        if "purchase_date" in data:
            equip.purchase_date = _parse_date(data["purchase_date"], "purchase_date")
        equip.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(equip)
    # 🔴 回應要過讀的牆 —— 兩支 GET 都包了，這支原本是裸的，於是送一個空
    # body（exclude_unset 讓它什麼都不改）就讀得到 GET 藏起來的 purchase_cost。
    return {"status": "ok", "equipment": _strip_mine_money(_equip_dict(equip), request)}


@router.delete("/{eid}")
async def delete_equipment(eid: str, request: Request):
    """刪器材：連帶刪 checkouts/maintenance 列 + best-effort 清掉 uploads 目錄。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import delete as sa_delete
    from db.models import EquipmentCheckout, EquipmentMaintenance

    async with factory() as session:
        equip = await _get_equipment_or_404(session, eid)
        _assert_mine_writable(equip, request)
        await session.execute(sa_delete(EquipmentCheckout)
                              .where(EquipmentCheckout.equipment_id == eid))
        await session.execute(sa_delete(EquipmentMaintenance)
                              .where(EquipmentMaintenance.equipment_id == eid))
        await session.delete(equip)
        await session.commit()

    if _ID_RE.match(eid):  # id 是自產 uuid hex，格式外的一律不碰檔案系統
        shutil.rmtree(os.path.join(_UPLOAD_BASE, "equipment", eid), ignore_errors=True)
    return {"status": "ok"}


# ── 領用 / 歸還 ───────────────────────────────────────────


def mark_checked_out(equip, checkout, now, person: str = "") -> None:
    """領用：領用列填 out_at、器材轉出勤。器材庫直接領用與行事曆場次「已領」共用這一份規則。"""
    checkout.out_at = now
    if person and not checkout.person:
        checkout.person = person[:64]
    equip.status = "出勤"
    equip.updated_at = now


def mark_returned(equip, checkout, now, note: str = "") -> None:
    """歸還：領用列填 returned_at（＋狀況備註）、器材轉在庫。"""
    checkout.returned_at = now
    note = (note or "").strip()
    if note:
        checkout.condition_note = note[:255]
    equip.status = "在庫"
    equip.updated_at = now


@router.post("/{eid}/checkout")
async def checkout_equipment(eid: str, req: EquipmentCheckoutPayload, request: Request):
    """領用（person 必填；已有未歸還紀錄回 409）。成功後 status=出勤。"""
    _check_auth(request)
    person = (req.person or "").strip()
    if not person:
        raise HTTPException(status_code=422, detail="person 必填")
    due_at = _parse_date(req.due_at, "due_at")
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import CrmProject, EquipmentCheckout

    now = datetime.now(timezone.utc)
    async with factory() as session:
        equip = await _get_equipment_or_404(session, eid)
        open_row = (await session.execute(
            select(EquipmentCheckout)
            .where(EquipmentCheckout.equipment_id == eid,
                   EquipmentCheckout.returned_at.is_(None))
        )).scalars().first()
        if open_row:
            raise HTTPException(status_code=409, detail="此器材已有未歸還的領用紀錄")

        checkout = EquipmentCheckout(
            id=uuid.uuid4().hex,
            equipment_id=eid,
            project_id=(req.project_id or "").strip() or None,
            person=person[:64],
            due_at=due_at,
        )
        session.add(checkout)
        mark_checked_out(equip, checkout, now)
        await session.commit()
        await session.refresh(checkout)
        project_name = ""
        if checkout.project_id:
            project_name = (await session.execute(
                select(CrmProject.name).where(CrmProject.id == checkout.project_id)
            )).scalar() or ""

    return {"status": "ok", "checkout": _checkout_dict(checkout, project_name, now)}


@router.post("/{eid}/return")
async def return_equipment(eid: str, req: EquipmentReturnPayload, request: Request):
    """歸還：未歸還那筆設 returned_at=now（無未歸還 404）。成功後 status=在庫。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import EquipmentCheckout

    now = datetime.now(timezone.utc)
    async with factory() as session:
        equip = await _get_equipment_or_404(session, eid)
        open_row = (await session.execute(
            select(EquipmentCheckout)
            .where(EquipmentCheckout.equipment_id == eid,
                   EquipmentCheckout.returned_at.is_(None))
            .order_by(EquipmentCheckout.out_at.desc())
        )).scalars().first()
        if not open_row:
            raise HTTPException(status_code=404, detail="此器材沒有未歸還的領用紀錄")

        mark_returned(equip, open_row, now, req.condition_note or "")
        await session.commit()
        await session.refresh(open_row)

    return {"status": "ok", "checkout": _checkout_dict(open_row, "", now)}


# ── 保養履歷 ─────────────────────────────────────────────


@router.post("/{eid}/maintenance")
async def add_maintenance(eid: str, req: EquipmentMaintenancePayload, request: Request):
    """新增保養紀錄（date 必填 'YYYY-MM-DD'；cost/note 可空）。"""
    _check_auth(request)
    date = _parse_date(req.date, "date")
    if not date:
        raise HTTPException(status_code=422, detail="date 必填（YYYY-MM-DD）")
    factory = _require_factory()

    from db.models import EquipmentMaintenance

    async with factory() as session:
        await _get_equipment_or_404(session, eid)
        maint = EquipmentMaintenance(
            id=uuid.uuid4().hex,
            equipment_id=eid,
            date=date,
            cost=req.cost,
            note=(req.note or "").strip()[:255] or None,
        )
        session.add(maint)
        await session.commit()
        await session.refresh(maint)
    return {"status": "ok", "maintenance": _maint_dict(maint)}


@router.delete("/maintenance/{mid}")
async def delete_maintenance(mid: str, request: Request):
    """刪保養紀錄。"""
    _check_auth(request)
    factory = _require_factory()

    from db.models import EquipmentMaintenance

    async with factory() as session:
        maint = await session.get(EquipmentMaintenance, mid)
        if not maint:
            raise HTTPException(status_code=404, detail="找不到此保養紀錄")
        await session.delete(maint)
        await session.commit()
    return {"status": "ok"}


# ── 封面照 ───────────────────────────────────────────────


@router.post("/{eid}/photo")
async def upload_photo(eid: str, request: Request, file: UploadFile = File(...)):
    """上傳單張封面。存 uploads/equipment/{eid}/{uuid}.{ext}，寫 cover_url；
    覆蓋時 best-effort 刪舊檔。"""
    _check_auth(request)
    if not _ID_RE.match(eid):
        raise HTTPException(status_code=422, detail="無效的器材 ID")
    ext = (os.path.splitext(file.filename or "")[1] or "").lower()
    if ext not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=422, detail=f"不支援的圖片格式：{ext or '(無副檔名)'}")
    factory = _require_factory()

    async with factory() as session:
        equip = await _get_equipment_or_404(session, eid)
        old_url = equip.cover_url or ""

        dest_dir = os.path.join(_UPLOAD_BASE, "equipment", eid)
        os.makedirs(dest_dir, exist_ok=True)
        fname = f"{uuid.uuid4().hex}{ext}"
        content = await file.read()
        with open(os.path.join(dest_dir, fname), "wb") as fp:
            fp.write(content)

        equip.cover_url = f"/uploads/equipment/{eid}/{fname}"
        equip.updated_at = datetime.now(timezone.utc)
        await session.commit()
        cover_url = equip.cover_url

    # url 由本模組自產（/uploads/equipment/...），照相對路徑 best-effort 刪舊檔
    if old_url.startswith("/uploads/equipment/"):
        try:
            os.remove(os.path.join(_UPLOAD_BASE, *old_url.split("/")[2:]))
        except OSError:
            pass
    return {"status": "ok", "cover_url": cover_url}
