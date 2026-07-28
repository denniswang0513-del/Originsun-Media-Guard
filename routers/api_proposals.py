"""
api_proposals.py — 提案資料庫 API（P-b，docs/PREPROD_PLAN.md B 段）

提案智財資產化 + win/loss 學習迴圈：提案 CRUD + deck 上傳（uploads/proposals/{id}/）
+ 共用參考片庫（跨提案掛連結）+ 一鍵成案（自動建 CRM 專案、project_id 回填）
+ 轉換率統計（by 類型 / by 年度）。守衛 = 管理員 OR preprod_proposals /
preprod_plan 模組。DB 三表：preprod_proposals / _references / _proposal_refs
（create_all 自建）。狀態轉「成案 / 未成案」強制 outcome_reason（組織學習欄）。
"""

import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile  # type: ignore

from core.auth import check_admin_or_module
from core.schemas import ProposalPayload, ProposalPlanCellPatch, ProposalPlanPayload, ReferencePayload

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


from core.db_guard import db_factory_or_503 as _require_factory


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


def _prop_dict(p, client_name: str = "", refs_count: int = 0, has_plan=None) -> dict:
    # has_plan 可由呼叫端傳入（清單走 SQL 布林 + defer(plan)，避免整包 JSONB 出庫）
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
        "has_plan": bool(p.plan) if has_plan is None else bool(has_plan),
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


async def _get_proposal_or_404(session, pid: str, for_update: bool = False):
    """取提案或 404。for_update=True 時 FOR UPDATE 持列鎖 —
    逐格 PATCH 是 read-modify-write 整包 JSONB，不持鎖時兩個併發
    PATCH 後 commit 的會把前者的格子蓋掉。"""
    from db.models import PreprodProposal
    if for_update:
        from sqlalchemy import select
        prop = (await session.execute(
            select(PreprodProposal).where(PreprodProposal.id == pid).with_for_update()
        )).scalar_one_or_none()
    else:
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
    from sqlalchemy.orm import defer
    from db.models import Client, PreprodProposal, PreprodProposalRef

    async with factory() as session:
        # has_plan 用 SQL 布林算、plan 欄 defer — 清單不把整包 JSONB 拖出庫
        query = (select(PreprodProposal, Client.short_name,
                        PreprodProposal.plan.isnot(None).label("has_plan"))
                 .options(defer(PreprodProposal.plan))
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
        _prop_dict(p, cname or "", ref_counts.get(p.id, 0), has_plan=hp)
        for p, cname, hp in rows
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
    d["plan"] = prop.plan   # 企劃矩陣全文（None = 尚未開始）
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
        # 不 refresh：expire_on_commit=False，屬性仍有效 — 省一次整列（含 plan JSONB）重讀
    return {"status": "ok", "proposal": _prop_dict(prop)}


# ── 企劃矩陣（docs/PROPOSAL_PLANNER.md §6.3 / §7.4） ──────────
# 共編正確性：日常輸入=逐格 PATCH（FOR UPDATE 持鎖 + 樂觀鎖 409）；
# PUT 整份只用於 開始/載入範例/清空；updated_at 一律伺服器蓋章。

_PLAN_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_PLAN_MAX_BYTES = 200 * 1024        # 整份上限（PUT）
_PLAN_CELL_MAX_BYTES = 20 * 1024    # 單格上限（PATCH）— 一格貼整篇腳本就該擋


def _plan_normalize_leaf(v) -> dict:
    """把 {answer,...} 或裸字串統一成完整葉節點格式。"""
    if isinstance(v, dict):
        return {"answer": str(v.get("answer") or ""),
                "updated_at": v.get("updated_at"), "updated_by": v.get("updated_by") or ""}
    return {"answer": str(v or ""), "updated_at": None, "updated_by": ""}


def _raise_plan_conflict(answer: str, updated_at, updated_by: str):
    """樂觀鎖撞牆 → 409 + 伺服器現值（前端並列兩版本讓人決定，不自動合併）。"""
    raise HTTPException(status_code=409, detail={
        "reason": "conflict", "server_answer": answer,
        "updated_at": updated_at, "updated_by": updated_by})


@router.put("/{pid}/plan")
async def put_plan(pid: str, req: ProposalPlanPayload, request: Request):
    """整份寫入 — 僅供「開始企劃 / 載入範例 / 清空(clear=true)」。
    只動 plan 欄，不碰提案其他欄位（避開整包 model_dump 洗欄地雷）。"""
    payload = _check_auth(request)
    factory = _require_factory()
    user = payload.get("username") or ""
    now_iso = datetime.now(timezone.utc).isoformat()

    def _stamp(v) -> dict:
        """收 {answer,...} 或裸字串，一律以伺服器時間戳重新蓋章。"""
        answer = v.get("answer") if isinstance(v, dict) else v
        return {"answer": str(answer or ""), "updated_at": now_iso, "updated_by": user}

    async with factory() as session:
        prop = await _get_proposal_or_404(session, pid, for_update=True)
        if req.clear:
            prop.plan = None
        else:
            if not (req.template_id and req.template_version):
                raise HTTPException(status_code=422, detail="template_id / template_version 必填")
            new_plan = {
                "template_id": req.template_id,
                "template_version": req.template_version,
                "theme": str(req.theme or ""),
                "theme_updated_at": now_iso, "theme_updated_by": user,
                "cells": {l: {h: _stamp(v) for h, v in (hows or {}).items()}
                          for l, hows in (req.cells or {}).items()},
                "directions": {h: _stamp(v) for h, v in (req.directions or {}).items()},
                "field_values": req.field_values or {},
            }
            # encode 量位元組（len(str) 量的是字元 — CJK 下實際 payload 會是 3 倍）
            if len(json.dumps(new_plan, ensure_ascii=False).encode("utf-8")) > _PLAN_MAX_BYTES:
                raise HTTPException(status_code=413, detail="企劃內容超過 200KB 上限")
            prop.plan = new_plan
        prop.updated_at = datetime.now(timezone.utc)
        await session.commit()
        plan = prop.plan
    return {"status": "ok", "plan": plan}


@router.patch("/{pid}/plan/cell")
async def patch_plan_cell(pid: str, req: ProposalPlanCellPatch, request: Request):
    """逐格寫入（共編的日常輸入單位）。
    kind: cell(lens+how) / direction(how) / theme / field(lens+field)。
    cell/direction/theme 帶 base_updated_at 做樂觀鎖 — 伺服器較新 → 409 + 現值，
    前端並列兩版本讓人決定，不自動合併。field（分鐘數/基調）last-write-wins。"""
    payload = _check_auth(request)
    factory = _require_factory()
    user = payload.get("username") or ""
    now_iso = datetime.now(timezone.utc).isoformat()

    if len((req.answer or "").encode("utf-8")) > _PLAN_CELL_MAX_BYTES:
        raise HTTPException(status_code=413, detail="單格內容超過 20KB 上限")

    async with factory() as session:
        prop = await _get_proposal_or_404(session, pid, for_update=True)
        if not prop.plan:
            raise HTTPException(status_code=409, detail="此提案尚未開始企劃（先 PUT /plan）")
        # 只淺拷路徑上的容器（O(路徑) 而非 O(全文深拷）；頂層換新物件即觸發
        # JSONB 變更偵測，未動的兄弟節點共享參照無妨 — 本函式不 mutate 它們
        plan = dict(prop.plan)

        def _conflict_or_write(container: dict, key: str):
            cur = _plan_normalize_leaf(container.get(key) or "")
            if (req.base_updated_at is not None and cur["updated_at"]
                    and cur["updated_at"] > req.base_updated_at):
                _raise_plan_conflict(cur["answer"], cur["updated_at"], cur["updated_by"])
            container[key] = {"answer": req.answer, "updated_at": now_iso, "updated_by": user}

        if req.kind == "cell":
            if not (req.lens and req.how and _PLAN_KEY_RE.match(req.lens) and _PLAN_KEY_RE.match(req.how)):
                raise HTTPException(status_code=422, detail="kind=cell 需合法 lens + how")
            cells = plan["cells"] = dict(plan.get("cells") or {})
            cells[req.lens] = dict(cells.get(req.lens) or {})
            _conflict_or_write(cells[req.lens], req.how)
        elif req.kind == "direction":
            if not (req.how and _PLAN_KEY_RE.match(req.how)):
                raise HTTPException(status_code=422, detail="kind=direction 需合法 how")
            dirs = plan["directions"] = dict(plan.get("directions") or {})
            _conflict_or_write(dirs, req.how)
        elif req.kind == "theme":
            cur_at = plan.get("theme_updated_at")
            if req.base_updated_at is not None and cur_at and cur_at > req.base_updated_at:
                _raise_plan_conflict(plan.get("theme") or "", cur_at, plan.get("theme_updated_by") or "")
            plan["theme"] = req.answer
            plan["theme_updated_at"], plan["theme_updated_by"] = now_iso, user
        elif req.kind == "field":
            if not (req.lens and req.field and _PLAN_KEY_RE.match(req.lens) and _PLAN_KEY_RE.match(req.field)):
                raise HTTPException(status_code=422, detail="kind=field 需合法 lens + field")
            fv = plan["field_values"] = dict(plan.get("field_values") or {})
            fv[req.lens] = dict(fv.get(req.lens) or {})
            fv[req.lens][req.field] = req.answer
        else:
            raise HTTPException(status_code=422, detail="kind 須為 cell/direction/theme/field")

        prop.plan = plan
        prop.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return {"status": "ok", "updated_at": now_iso}


@router.get("/{pid}/plan/meta")
async def get_plan_meta(pid: str, request: Request, since: str = ""):
    """輕量輪詢端點：只回各格 updated_at/by，不帶全文（§7.4 的 30s 輪詢打這支）。
    帶 ?since=<上次看到的提案 updated_at> 可短路 — 沒動過就只花一次純量查詢，
    不 detoast 整包 JSONB。"""
    _check_auth(request)
    factory = _require_factory()
    from sqlalchemy import select
    from db.models import PreprodProposal
    async with factory() as session:
        if since:
            cur = (await session.execute(
                select(PreprodProposal.updated_at).where(PreprodProposal.id == pid)
            )).scalar_one_or_none()
            if cur is None:
                raise HTTPException(status_code=404, detail="找不到此提案")
            if cur.isoformat() == since:
                return {"unchanged": True, "updated_at": since}
        prop = await _get_proposal_or_404(session, pid)
        prop_updated_at = prop.updated_at.isoformat() if prop.updated_at else None
        plan = prop.plan or {}

    def _meta(section):
        out = {}
        for k, v in (section or {}).items():
            if isinstance(v, dict) and "answer" not in v:
                out[k] = _meta(v)
            else:
                leaf = _plan_normalize_leaf(v)
                out[k] = {"updated_at": leaf["updated_at"], "updated_by": leaf["updated_by"]}
        return out

    return {"has_plan": bool(plan),
            "updated_at": prop_updated_at,   # 下次輪詢帶回 ?since= 用
            "theme_updated_at": plan.get("theme_updated_at"),
            "cells": _meta(plan.get("cells")),
            "directions": _meta(plan.get("directions"))}


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
            status="製作",
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
