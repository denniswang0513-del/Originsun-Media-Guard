"""
api_intel.py — 產業情報 runner API（P-c，docs/PREPROD_PLAN.md C 段）

第四個 AI runner 的工作台後端（⚠ 內部工具、不是官網功能 — 所以在 routers/ 根層
而非 routers/website/）：來源白名單 CRUD + 情報項目看板（篩選/收藏/封存）
+ 一鍵轉提案（建 PreprodProposal 草稿、item.proposal_id 回填 — 情報→提案閉環）
+ runner 設定（enabled/cron）與立即執行（背景跑，模式同 admin_social 的 run）。
守衛 = 管理員 OR intel / preprod_plan 模組。DB 兩表：intel_sources / intel_items
（create_all 自建）。pipeline 本體在 services/intel_runner.py。
"""

import uuid

from fastapi import APIRouter, HTTPException, Request  # type: ignore

from core.auth import check_admin_or_module
from core.schemas import IntelSourcePayload

router = APIRouter(prefix="/api/v1/intel", tags=["intel"])

_ITEM_STATUSES = ("new", "starred", "archived", "converted")
# IntelSourcePayload 中可直接 setattr 到 model 的欄位（部分更新白名單；enabled 是
# bool→int 要先轉、url 要驗開頭，都另外處理）
_SRC_FIELDS = ("name", "type", "url", "keywords", "note")


def _check_auth(request: Request) -> dict:
    return check_admin_or_module(request, "intel", "preprod_plan")


from core.db_guard import db_factory_or_503 as _require_factory


def _check_url(url: str) -> str:
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="url 必須以 http:// 或 https:// 開頭")
    return url


def _src_dict(s) -> dict:
    return {
        "id": s.id,
        "name": s.name or "",
        "type": s.type or "rss",
        "url": s.url or "",
        "keywords": s.keywords or [],
        "enabled": bool(s.enabled),
        "last_fetched_at": s.last_fetched_at.isoformat() if s.last_fetched_at else None,
        "note": s.note or "",
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _item_dict(it, source_name: str = "") -> dict:
    return {
        "id": it.id,
        "source_id": it.source_id or "",
        "source_name": source_name or "",
        "url": it.url or "",
        "title": it.title or "",
        "summary": it.summary or "",
        "category": it.category or "未分類",
        "score": it.score or 0,
        "deadline": it.deadline.strftime("%Y-%m-%d") if it.deadline else None,
        "status": it.status or "new",
        "proposal_id": it.proposal_id or "",
        "fetched_at": it.fetched_at.isoformat() if it.fetched_at else None,
        "created_at": it.created_at.isoformat() if it.created_at else None,
    }


async def _get_item_or_404(session, iid: str):
    from db.models import IntelItem
    item = await session.get(IntelItem, iid)
    if not item:
        raise HTTPException(status_code=404, detail="找不到此情報項目")
    return item


# ── 來源白名單 CRUD ───────────────────────────────────────


@router.get("/sources")
async def list_sources(request: Request):
    """來源白名單列表（建立順序）。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import IntelSource

    async with factory() as session:
        rows = (await session.execute(
            select(IntelSource).order_by(IntelSource.created_at)
        )).scalars().all()
    return {"sources": [_src_dict(s) for s in rows]}


@router.post("/sources")
async def create_source(req: IntelSourcePayload, request: Request):
    """新增來源（url 必填且須 http 開頭；name 空則以 url 代）。"""
    _check_auth(request)
    url = _check_url(req.url or "")
    factory = _require_factory()

    from db.models import IntelSource

    data = req.model_dump(exclude_unset=True)
    src = IntelSource(
        id=uuid.uuid4().hex,
        url=url,
        enabled=1 if data.get("enabled", True) else 0,
        **{k: v for k, v in data.items() if k in _SRC_FIELDS and k != "url"},
    )
    if not (src.name or "").strip():
        src.name = url[:128]
    async with factory() as session:
        session.add(src)
        await session.commit()
        await session.refresh(src)
    return {"status": "ok", "source": _src_dict(src)}


@router.put("/sources/{sid}")
async def update_source(sid: str, req: IntelSourcePayload, request: Request):
    """部分更新來源（url 有帶就驗 http 開頭；enabled 收 bool 存 0/1）。"""
    _check_auth(request)
    factory = _require_factory()

    from db.models import IntelSource

    data = req.model_dump(exclude_unset=True)
    if "url" in data:
        data["url"] = _check_url(data["url"] or "")
    async with factory() as session:
        src = await session.get(IntelSource, sid)
        if not src:
            raise HTTPException(status_code=404, detail="找不到此來源")
        for k, v in data.items():
            if k in _SRC_FIELDS:   # url 已在其中
                setattr(src, k, v)
        if "enabled" in data:
            src.enabled = 1 if data["enabled"] else 0
        await session.commit()
        await session.refresh(src)
    return {"status": "ok", "source": _src_dict(src)}


@router.delete("/sources/{sid}")
async def delete_source(sid: str, request: Request):
    """刪來源（既有 intel_items 保留 — 情報是已入庫資產，只斷來源）。"""
    _check_auth(request)
    factory = _require_factory()

    from db.models import IntelSource

    async with factory() as session:
        src = await session.get(IntelSource, sid)
        if not src:
            raise HTTPException(status_code=404, detail="找不到此來源")
        await session.delete(src)
        await session.commit()
    return {"status": "ok"}


# ── 情報項目看板 ─────────────────────────────────────────


@router.get("/items")
async def list_items(request: Request, status: str = "", category: str = "",
                     q: str = "", limit: int = 200):
    """情報列表 + 篩選（status/category/q=標題摘要 ilike），
    score desc → created_at desc，上限 200，附 source_name。"""
    _check_auth(request)
    if status and status not in _ITEM_STATUSES:
        raise HTTPException(status_code=422, detail=f"status 只接受 {list(_ITEM_STATUSES)}")
    factory = _require_factory()

    from sqlalchemy import or_, select
    from db.models import IntelItem, IntelSource

    limit = max(1, min(int(limit or 200), 200))
    async with factory() as session:
        query = (select(IntelItem, IntelSource.name)
                 .outerjoin(IntelSource, IntelSource.id == IntelItem.source_id)
                 .order_by(IntelItem.score.desc(), IntelItem.created_at.desc())
                 .limit(limit))
        if status:
            query = query.where(IntelItem.status == status)
        if category:
            query = query.where(IntelItem.category == category)
        if q:
            ql = f"%{q}%"
            query = query.where(or_(IntelItem.title.ilike(ql),
                                    IntelItem.summary.ilike(ql)))
        rows = (await session.execute(query)).all()
    return {"items": [_item_dict(it, sname or "") for it, sname in rows]}


async def _set_status(iid: str, request: Request, new_status) -> dict:
    """star/archive/unarchive 共用：converted 是終態不准改（409）。
    new_status 可為 callable(cur_status) → 新狀態（star 的 toggle 用）。"""
    _check_auth(request)
    factory = _require_factory()
    async with factory() as session:
        item = await _get_item_or_404(session, iid)
        if item.status == "converted":
            raise HTTPException(status_code=409, detail="已轉提案的項目不可再改狀態")
        item.status = new_status(item.status) if callable(new_status) else new_status
        await session.commit()
        await session.refresh(item)
        return {"status": "ok", "item": _item_dict(item)}


@router.post("/items/{iid}/star")
async def star_item(iid: str, request: Request):
    """收藏 toggle：new/archived → starred；已 starred → 退回 new。"""
    return await _set_status(iid, request, lambda cur: "new" if cur == "starred" else "starred")


@router.post("/items/{iid}/archive")
async def archive_item(iid: str, request: Request):
    """封存 → archived。"""
    return await _set_status(iid, request, "archived")


@router.post("/items/{iid}/unarchive")
async def unarchive_item(iid: str, request: Request):
    """解封存 → new。"""
    return await _set_status(iid, request, "new")


@router.post("/items/{iid}/convert")
async def convert_item(iid: str, request: Request):
    """轉提案：建 PreprodProposal 草稿（category=標案 → ptype=政府標案，其餘=其他；
    tags=[產業情報]）→ item.status=converted + proposal_id 回填。
    情報 → 提案 → 專案 → 結案 → 官網 → 社群 全鏈路的第一哩。"""
    payload = _check_auth(request)
    factory = _require_factory()

    from db.models import PreprodProposal

    async with factory() as session:
        item = await _get_item_or_404(session, iid)
        if item.status == "converted" or item.proposal_id:
            raise HTTPException(status_code=409, detail="此項目已轉過提案")
        prop = PreprodProposal(
            id=uuid.uuid4().hex,
            title=(item.title or "").strip()[:255] or "（無標題情報）",
            ptype="政府標案" if item.category == "標案" else "其他",
            status="草稿",
            tags=["產業情報"],
            created_by=payload.get("username") or "",
        )
        session.add(prop)
        item.status = "converted"
        item.proposal_id = prop.id
        await session.commit()
        proposal_id = prop.id
    return {"status": "ok", "proposal_id": proposal_id}


# ── Runner 設定 + 立即執行 ────────────────────────────────


@router.get("/settings")
async def get_settings(request: Request):
    """讀 intel.* 設定 + runner 狀態（enabled/cron/last_run_at/last_run_summary/running）。"""
    _check_auth(request)
    factory = _require_factory()
    from services import intel_runner
    async with factory() as session:
        return await intel_runner.get_intel_settings(session)


@router.put("/settings")
async def put_settings(payload: dict, request: Request):
    """更新設定（白名單鍵：enabled / cron；cron 走 validate_cron）。"""
    auth = _check_auth(request)
    factory = _require_factory()
    from services import intel_runner
    async with factory() as session:
        try:
            return await intel_runner.update_intel_settings(
                session, payload, by=auth.get("username") or "")
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))


@router.post("/run")
async def run_now(request: Request):
    """立即跑一輪 pipeline — 非同步背景跑（立即回 {started}/{busy}），
    前端輪詢 /settings 的 running/last_run_at（模式同 admin_social 的 run）。
    enabled 關著時 pipeline 會記 disabled、不燒 claude 額度。"""
    _check_auth(request)
    _require_factory()
    from services import intel_runner
    return await intel_runner.trigger_run()
