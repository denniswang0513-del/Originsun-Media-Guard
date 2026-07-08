"""
api_proposals.py — 提案資料庫 API（P-b，docs/PREPROD_PLAN.md B 段）

提案智財資產化 + win/loss 學習迴圈：提案 CRUD + deck 上傳（uploads/proposals/{id}/）
+ 共用參考片庫（跨提案掛連結）+ 一鍵成案（自動建 CRM 專案、project_id 回填）
+ 轉換率統計（by 類型 / by 年度）。守衛 = 管理員 OR preprod_proposals /
preprod_plan 模組。DB 三表：preprod_proposals / _references / _proposal_refs
（create_all 自建）。狀態轉「成案 / 未成案」強制 outcome_reason（組織學習欄）。
"""

import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile  # type: ignore

import core.state as state
from core.auth import check_admin_or_module
from core.schemas import ProposalPayload, ReferencePayload

router = APIRouter(prefix="/api/v1/proposals", tags=["proposals"])

# deck 檔案落地：<repo>/uploads/proposals/{proposal_id}/（main.py 已 mount /uploads）
_UPLOAD_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
_ALLOWED_DECK_EXT = {".pdf", ".ppt", ".pptx", ".key", ".zip"}
_DECK_MAX_BYTES = 50 * 1024 * 1024        # 50MB，超過回 413
_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# 狀態機：轉入這兩個狀態時 outcome_reason 必填（win/loss 學習迴圈的核心約束）
_OUTCOME_STATUSES = {"成案", "未成案"}
# 轉換率分母：只算真的提出去過的（草稿/擱置不進分母）
_FUNNEL_STATUSES = ("已提案", "入圍", "成案", "未成案")

# ptype → CRM project_type 對映（CRM 預設類型：紀實影片/活動紀實/形象影片/廣告/MV；
# 對不上的（政府標案/社群/其他…）原字帶入 — project_type 是自由字串）
_PTYPE_TO_PROJECT_TYPE = {
    "形象": "形象影片",
    "紀錄片": "紀實影片",
}

# ProposalPayload 中可直接 setattr 到 model 的欄位（部分更新白名單；
# pitch_date 是日期字串要先 parse、project_id 由 /convert 回填，都不走這裡）
_PROP_FIELDS = (
    "title", "client_id", "quotation_id", "ptype", "status",
    "budget_range", "deck_url", "outcome_reason", "tags",
)
_REF_FIELDS = ("url", "title", "note", "tags", "thumb_url")


def _check_auth(request: Request) -> dict:
    return check_admin_or_module(request, "preprod_proposals", "preprod_plan")


def _require_factory():
    """DB 守門：離線一律 503（模式同 api_locations）。"""
    if not state.db_online:
        raise HTTPException(status_code=503, detail="資料庫離線")
    from db.session import get_session_factory
    factory = get_session_factory()
    if not factory:
        raise HTTPException(status_code=503, detail="資料庫離線")
    return factory


def _parse_date(raw):
    """'YYYY-MM-DD' → datetime；空值回 None，格式錯 422。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="pitch_date 格式須為 YYYY-MM-DD")


def _check_outcome_reason(new_status: str, reason: str):
    """轉「成案/未成案」必附原因 — create/update 共用的守門。"""
    if new_status in _OUTCOME_STATUSES and not (reason or "").strip():
        raise HTTPException(status_code=422, detail=f"狀態轉「{new_status}」時 outcome_reason 必填（組織學習欄）")


def _prop_dict(p, client_name: str = "", refs_count: int = 0) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "client_id": p.client_id or "",
        "client_name": client_name or "",
        "project_id": p.project_id or "",
        "quotation_id": p.quotation_id or "",
        "ptype": p.ptype or "",
        "status": p.status or "草稿",
        "pitch_date": p.pitch_date.strftime("%Y-%m-%d") if p.pitch_date else None,
        "budget_range": p.budget_range or "",
        "deck_url": p.deck_url or "",
        "outcome_reason": p.outcome_reason or "",
        "tags": p.tags or [],
        "created_by": p.created_by or "",
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "refs_count": refs_count,
    }


def _ref_dict(r) -> dict:
    return {
        "id": r.id,
        "url": r.url or "",
        "title": r.title or "",
        "note": r.note or "",
        "tags": r.tags or [],
        "thumb_url": r.thumb_url or "",
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


async def _get_proposal_or_404(session, pid: str):
    from db.models import PreprodProposal
    prop = await session.get(PreprodProposal, pid)
    if not prop:
        raise HTTPException(status_code=404, detail="找不到此提案")
    return prop


# ── 轉換率統計（固定路徑，須排在 /{pid} 之前註冊） ─────────


@router.get("/stats")
async def proposal_stats(request: Request):
    """轉換率卡：整體 + by 類型 + by 年（pitch_date）各給 {total, won, rate}。
    只算 status in (已提案/入圍/成案/未成案)；total_all 是含草稿/擱置的全量。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import select, func as safunc
    from db.models import PreprodProposal

    async with factory() as session:
        total_all = (await session.execute(
            select(safunc.count(PreprodProposal.id)))).scalar() or 0
        rows = (await session.execute(
            select(PreprodProposal.ptype, PreprodProposal.status, PreprodProposal.pitch_date)
            .where(PreprodProposal.status.in_(_FUNNEL_STATUSES)))).all()

    def _bucket(items):
        out = {}
        for key, won in items:
            b = out.setdefault(key, {"total": 0, "won": 0})
            b["total"] += 1
            b["won"] += 1 if won else 0
        return [
            {"key": k, "total": v["total"], "won": v["won"],
             "rate": round(v["won"] * 100 / v["total"], 1) if v["total"] else 0.0}
            for k, v in sorted(out.items())
        ]

    won_total = sum(1 for _, s, _d in rows if s == "成案")
    overall = {
        "total": len(rows), "won": won_total,
        "rate": round(won_total * 100 / len(rows), 1) if rows else 0.0,
    }
    by_type = _bucket([(pt or "未分類", s == "成案") for pt, s, _d in rows])
    by_year = _bucket([(str(d.year) if d else "未填", s == "成案") for _pt, s, d in rows])

    return {"total_all": total_all, "overall": overall, "by_type": by_type, "by_year": by_year}


# ── 參考片庫（共用資產，固定路徑，須排在 /{pid} 之前註冊） ──


@router.get("/references")
async def list_references(request: Request, q: str = ""):
    """參考片庫列表（q=標題/網址 ilike）。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import or_, select
    from db.models import PreprodReference

    async with factory() as session:
        query = select(PreprodReference).order_by(PreprodReference.created_at.desc())
        if q:
            ql = f"%{q}%"
            query = query.where(or_(PreprodReference.title.ilike(ql),
                                    PreprodReference.url.ilike(ql)))
        rows = (await session.execute(query)).scalars().all()
    return {"references": [_ref_dict(r) for r in rows]}


@router.post("/references")
async def create_reference(req: ReferencePayload, request: Request):
    """新增參考片（url 必填）。"""
    _check_auth(request)
    url = (req.url or "").strip()
    if not url:
        raise HTTPException(status_code=422, detail="url 必填")
    factory = _require_factory()

    from db.models import PreprodReference

    data = req.model_dump(exclude_unset=True)
    data["url"] = url
    ref = PreprodReference(
        id=uuid.uuid4().hex,
        **{k: v for k, v in data.items() if k in _REF_FIELDS},
    )
    async with factory() as session:
        session.add(ref)
        await session.commit()
        await session.refresh(ref)
    return {"status": "ok", "reference": _ref_dict(ref)}


@router.put("/references/{rid}")
async def update_reference(rid: str, req: ReferencePayload, request: Request):
    """部分更新參考片。"""
    _check_auth(request)
    factory = _require_factory()

    data = req.model_dump(exclude_unset=True)
    if "url" in data and not (data["url"] or "").strip():
        raise HTTPException(status_code=422, detail="url 不可為空")

    from db.models import PreprodReference

    async with factory() as session:
        ref = await session.get(PreprodReference, rid)
        if not ref:
            raise HTTPException(status_code=404, detail="找不到此參考片")
        for k, v in data.items():
            if k in _REF_FIELDS:
                setattr(ref, k, v)
        await session.commit()
        await session.refresh(ref)
    return {"status": "ok", "reference": _ref_dict(ref)}


@router.delete("/references/{rid}")
async def delete_reference(rid: str, request: Request):
    """刪參考片：連帶清掉所有提案的掛載關聯列。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import delete as sa_delete
    from db.models import PreprodProposalRef, PreprodReference

    async with factory() as session:
        ref = await session.get(PreprodReference, rid)
        if not ref:
            raise HTTPException(status_code=404, detail="找不到此參考片")
        await session.execute(sa_delete(PreprodProposalRef)
                              .where(PreprodProposalRef.reference_id == rid))
        await session.delete(ref)
        await session.commit()
    return {"status": "ok"}


# ── 提案 CRUD ─────────────────────────────────────────────


@router.get("")
async def list_proposals(request: Request, q: str = "", status: str = "",
                         ptype: str = "", client_id: str = "", year: str = ""):
    """提案列表 + 篩選（q=標題、狀態、類型、客戶、年度=pitch_date 年），
    join clients 取 client_name，附 refs_count。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import extract, select, func as safunc
    from db.models import Client, PreprodProposal, PreprodProposalRef

    async with factory() as session:
        query = (select(PreprodProposal, Client.short_name)
                 .outerjoin(Client, Client.id == PreprodProposal.client_id)
                 .order_by(PreprodProposal.updated_at.desc()))
        if status:
            query = query.where(PreprodProposal.status == status)
        if ptype:
            query = query.where(PreprodProposal.ptype == ptype)
        if client_id:
            query = query.where(PreprodProposal.client_id == client_id)
        if q:
            query = query.where(PreprodProposal.title.ilike(f"%{q}%"))
        if year:
            try:
                yr = int(year)
            except ValueError:
                raise HTTPException(status_code=422, detail="year 須為數字年份")
            query = query.where(extract("year", PreprodProposal.pitch_date) == yr)
        rows = (await session.execute(query)).all()

        ref_counts = dict((await session.execute(
            select(PreprodProposalRef.proposal_id, safunc.count(PreprodProposalRef.id))
            .group_by(PreprodProposalRef.proposal_id))).all())

    return {"proposals": [
        _prop_dict(p, cname or "", ref_counts.get(p.id, 0)) for p, cname in rows
    ]}


@router.post("")
async def create_proposal(req: ProposalPayload, request: Request):
    """新增提案（title 必填；status 若直接開在成案/未成案，outcome_reason 必填）。"""
    payload = _check_auth(request)
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(status_code=422, detail="title 必填")
    _check_outcome_reason(req.status or "", req.outcome_reason or "")
    pitch_date = _parse_date(req.pitch_date)
    factory = _require_factory()

    from db.models import PreprodProposal

    data = req.model_dump(exclude_unset=True)
    data["title"] = title
    prop = PreprodProposal(
        id=uuid.uuid4().hex,
        pitch_date=pitch_date,
        created_by=payload.get("username") or "",
        **{k: v for k, v in data.items() if k in _PROP_FIELDS},
    )
    async with factory() as session:
        session.add(prop)
        await session.commit()
        await session.refresh(prop)
    return {"status": "ok", "proposal": _prop_dict(prop)}


@router.get("/{pid}")
async def get_proposal(pid: str, request: Request):
    """提案詳情：欄位 + client_name/project_name + references[]（join 共用片庫）。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import Client, CrmProject, PreprodProposalRef, PreprodReference

    async with factory() as session:
        prop = await _get_proposal_or_404(session, pid)
        client_name = ""
        if prop.client_id:
            client_name = (await session.execute(
                select(Client.short_name).where(Client.id == prop.client_id)
            )).scalar() or ""
        project_name = ""
        if prop.project_id:
            project_name = (await session.execute(
                select(CrmProject.name).where(CrmProject.id == prop.project_id)
            )).scalar() or ""
        refs = (await session.execute(
            select(PreprodReference)
            .join(PreprodProposalRef, PreprodProposalRef.reference_id == PreprodReference.id)
            .where(PreprodProposalRef.proposal_id == pid)
            .order_by(PreprodReference.created_at.desc())
        )).scalars().all()

    d = _prop_dict(prop, client_name, refs_count=len(refs))
    d["project_name"] = project_name
    d["references"] = [_ref_dict(r) for r in refs]
    return {"proposal": d}


@router.put("/{pid}")
async def update_proposal(pid: str, req: ProposalPayload, request: Request):
    """部分更新：只動 payload 有帶的欄位；轉成案/未成案時 outcome_reason 必填。"""
    _check_auth(request)
    factory = _require_factory()

    data = req.model_dump(exclude_unset=True)
    if "title" in data and not (data["title"] or "").strip():
        raise HTTPException(status_code=422, detail="title 不可為空")
    has_pitch_date = "pitch_date" in data
    pitch_date = _parse_date(data.pop("pitch_date")) if has_pitch_date else None

    async with factory() as session:
        prop = await _get_proposal_or_404(session, pid)
        if "status" in data:
            # 有效原因 = 本次帶的（含刻意清空）或原本已存的
            effective_reason = data.get("outcome_reason", prop.outcome_reason)
            _check_outcome_reason(data["status"] or "", effective_reason or "")
        for k, v in data.items():
            if k in _PROP_FIELDS:
                setattr(prop, k, v)
        if has_pitch_date:
            prop.pitch_date = pitch_date
        prop.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(prop)
    return {"status": "ok", "proposal": _prop_dict(prop)}


@router.delete("/{pid}")
async def delete_proposal(pid: str, request: Request):
    """刪提案：連帶刪 proposal_refs 關聯列（reference 是共用片庫保留）
    + best-effort 清掉 uploads deck 目錄。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import delete as sa_delete
    from db.models import PreprodProposalRef

    async with factory() as session:
        prop = await _get_proposal_or_404(session, pid)
        await session.execute(sa_delete(PreprodProposalRef)
                              .where(PreprodProposalRef.proposal_id == pid))
        await session.delete(prop)
        await session.commit()

    if _ID_RE.match(pid):  # id 是自產 uuid hex，格式外的一律不碰檔案系統
        shutil.rmtree(os.path.join(_UPLOAD_BASE, "proposals", pid), ignore_errors=True)
    return {"status": "ok"}


# ── Deck 上傳 ─────────────────────────────────────────────


@router.post("/{pid}/deck")
async def upload_deck(pid: str, request: Request, file: UploadFile = File(...)):
    """上傳提案簡報（.pdf/.ppt/.pptx/.key/.zip，上限 50MB→413）。
    存 uploads/proposals/{pid}/{uuid}{ext}，寫 deck_url（舊檔 best-effort 刪除）。"""
    _check_auth(request)
    if not _ID_RE.match(pid):
        raise HTTPException(status_code=422, detail="無效的提案 ID")
    ext = (os.path.splitext(file.filename or "")[1] or "").lower()
    if ext not in _ALLOWED_DECK_EXT:
        raise HTTPException(status_code=422, detail=f"不支援的簡報格式：{ext or '(無副檔名)'}（限 pdf/ppt/pptx/key/zip）")
    content = await file.read()
    if len(content) > _DECK_MAX_BYTES:
        raise HTTPException(status_code=413, detail="簡報檔超過 50MB 上限")
    factory = _require_factory()

    async with factory() as session:
        prop = await _get_proposal_or_404(session, pid)
        old_url = prop.deck_url or ""

        dest_dir = os.path.join(_UPLOAD_BASE, "proposals", pid)
        os.makedirs(dest_dir, exist_ok=True)
        fname = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(dest_dir, fname), "wb") as fp:
            fp.write(content)

        prop.deck_url = f"/uploads/proposals/{pid}/{fname}"
        prop.updated_at = datetime.now(timezone.utc)
        await session.commit()
        deck_url = prop.deck_url

    # 舊 deck 由本模組自產（/uploads/proposals/...），照相對路徑 best-effort 刪檔
    if old_url.startswith("/uploads/proposals/"):
        try:
            os.remove(os.path.join(_UPLOAD_BASE, *old_url.split("/")[2:]))
        except OSError:
            pass
    return {"status": "ok", "deck_url": deck_url}


# ── 一鍵成案 ─────────────────────────────────────────────


@router.post("/{pid}/convert")
async def convert_proposal(pid: str, request: Request,
                           req: Optional[ProposalPayload] = None):
    """一鍵成案：自動建 CRM 專案（帶客戶 + 類型對映）→ 回填 proposal.project_id
    + status=成案。body 帶 outcome_reason 一併存。提案沒選客戶回 422
    （CrmProject.client_id 是 nullable=False）。"""
    _check_auth(request)
    factory = _require_factory()

    from db.models import Client, CrmProject

    now = datetime.now(timezone.utc)
    async with factory() as session:
        prop = await _get_proposal_or_404(session, pid)
        if prop.project_id or prop.status == "成案":
            raise HTTPException(status_code=409, detail="此提案已成案（project_id 已回填）")
        if not (prop.client_id or "").strip():
            raise HTTPException(status_code=422, detail="提案尚未關聯客戶 — 請先選擇客戶再成案")
        client = await session.get(Client, prop.client_id)
        if not client:
            raise HTTPException(status_code=422, detail="提案關聯的客戶不存在 — 請先選擇有效客戶")

        project = CrmProject(
            id=uuid.uuid4().hex,
            name=prop.title,
            client_id=prop.client_id,
            status="進行中",
            project_type=_PTYPE_TO_PROJECT_TYPE.get(prop.ptype or "", prop.ptype or ""),
            created_at=now,
            updated_at=now,
        )
        session.add(project)

        prop.project_id = project.id
        prop.status = "成案"
        if req and (req.outcome_reason or "").strip():
            prop.outcome_reason = req.outcome_reason.strip()
        prop.updated_at = now
        await session.commit()
        project_id = project.id

    return {"status": "ok", "project_id": project_id}


# ── 提案 ↔ 參考片 掛載 ───────────────────────────────────


@router.post("/{pid}/refs")
async def link_reference(pid: str, request: Request, body: dict = Body(...)):
    """掛參考片到提案：body {reference_id}（重複掛載冪等回 ok）。"""
    _check_auth(request)
    reference_id = (body.get("reference_id") or "").strip()
    if not reference_id:
        raise HTTPException(status_code=422, detail="reference_id 必填")
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import PreprodProposalRef, PreprodReference

    async with factory() as session:
        await _get_proposal_or_404(session, pid)
        ref = await session.get(PreprodReference, reference_id)
        if not ref:
            raise HTTPException(status_code=404, detail="找不到此參考片")
        existing = (await session.execute(
            select(PreprodProposalRef)
            .where(PreprodProposalRef.proposal_id == pid,
                   PreprodProposalRef.reference_id == reference_id)
        )).scalars().first()
        if not existing:
            session.add(PreprodProposalRef(
                id=uuid.uuid4().hex, proposal_id=pid, reference_id=reference_id))
            await session.commit()
    return {"status": "ok", "reference": _ref_dict(ref)}


@router.delete("/{pid}/refs/{rid}")
async def unlink_reference(pid: str, rid: str, request: Request):
    """解除提案上的參考片掛載（rid=reference_id；片庫本體保留）。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import delete as sa_delete
    from db.models import PreprodProposalRef

    async with factory() as session:
        await _get_proposal_or_404(session, pid)
        await session.execute(sa_delete(PreprodProposalRef)
                              .where(PreprodProposalRef.proposal_id == pid,
                                     PreprodProposalRef.reference_id == rid))
        await session.commit()
    return {"status": "ok"}
