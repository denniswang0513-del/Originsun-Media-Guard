"""routers/api_references.py
---
參考影片庫 v2（docs/REFERENCE_LIBRARY.md）—— 每支片一個小頁面。

為什麼另開一個 router 而不是塞進 api_proposals.py：後者已近千行且職責是「提案」，
參考片庫在 v2 之後是**獨立領域**（研究四欄 / 截圖標示 / 跨專案引用）。
既有的 `/api/v1/proposals/references*` 舊端點原地保留（SPA 與獨立頁都在用），
本檔一律掛 `/api/v1/references`，URL 不打架。

權限：沿用提案庫的閘門（admin 或 preprod_proposals / preprod_plan 模組）—— 刻意不新增
模組 key，免掉 RBAC 三處同步（docs/REFERENCE_LIBRARY.md §9 決策 5 預設值）。

公開路徑（`/shared/{token}/…`）：借用提案的 plan_share token —— 拿到提案共編連結的人
可以讀該提案掛的參考片、填研究、改標題備註；**不可**改分類/建檔旗標、不可刪片。
放行範圍集中在 `_PUBLIC_ALLOW` 這一張表（kind × 欄位），前端只是隱藏。
寫入前另過 `assert_public_ref_writable()`：被別的提案共用的片不給免登入連結改
（與 api_proposals 的公開改片規則同一份，見該函式 docstring）。
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request  # type: ignore

from core.auth import check_admin_or_module
from core.db_guard import db_factory_or_503 as _require_factory
from core.schemas import ReferencePatch

router = APIRouter(prefix="/api/v1/references", tags=["references"])

# ── 分類族（Notion「類別/品牌/製作單位/典範/技巧/情感取向/關鍵字/內部專案」對映）──
FACET_KEYS = ("category", "brand", "studio", "paragon", "technique",
              "emotion", "keyword", "study")
_SINGLE_FACETS = ("studio",)          # 單選（Notion 是 select 不是 multi_select）
_FACET_MAX_ITEMS = 30
_FACET_ITEM_MAX = 64

# ── 研究四欄（Notion 模板的四欄表格，固定欄位；換欄＝模板版本化）──
RESEARCH_COLS = ("purpose", "idea", "material", "moment")
RESEARCH_TEMPLATE_VERSION = "1.0.0"
_RESEARCH_MAX_ROWS = 20
_CELL_MAX_BYTES = 8 * 1024
_TEXT_MAX_BYTES = 16 * 1024
_RESEARCH_MAX_BYTES = 200 * 1024

_EDITABLE_FIELDS = ("title", "url", "note", "description", "curated", "thumb_url")
# 公開連結（免登入）的放行表：kind → True 全放 / tuple 白名單 / False 全禁。
# 這是「共編連結能改什麼」的唯一正本 —— 別在端點裡另外寫 if。
_PUBLIC_ALLOW = {"field": ("title", "note", "description"), "research": True, "facet": False}


def _check_auth(request: Request) -> dict:
    return check_admin_or_module(request, "preprod_proposals", "preprod_plan")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 影片描述子 + 封面 ────────────────────────────────────────


def _parse_video(url: str) -> dict:
    """→ {provider, video_id, thumb}（thumb 只有 YouTube 能零成本組出來）。"""
    try:
        from services.website.video_utils import parse_video_url
        d = parse_video_url(url or "") or {}
    except Exception:      # website 套件缺失不該讓片庫掛掉
        d = {}
    provider = d.get("provider") or ("link" if (url or "").startswith("http") else "")
    vid = d.get("video_id") or ""
    thumb = f"https://img.youtube.com/vi/{vid}/hqdefault.jpg" if provider == "youtube" and vid else ""
    return {"provider": provider, "video_id": vid, "thumb": thumb}


def _sync_video_meta(ref) -> None:
    """url 變動時同步 provider/video_id，缺封面時補 YouTube 縮圖。"""
    meta = _parse_video(ref.url or "")
    ref.provider = meta["provider"] or None
    ref.video_id = meta["video_id"] or None
    if meta["thumb"] and not (ref.thumb_url or "").strip():
        ref.thumb_url = meta["thumb"]


# ── 正規化 / 序列化 ─────────────────────────────────────────


def _leaf(v) -> dict:
    if isinstance(v, dict):
        return {"answer": str(v.get("answer") or ""),
                "updated_at": v.get("updated_at"), "updated_by": v.get("updated_by") or ""}
    return {"answer": str(v or ""), "updated_at": None, "updated_by": ""}


def _norm_facets(raw) -> dict:
    src = raw if isinstance(raw, dict) else {}
    out = {}
    for k in FACET_KEYS:
        v = src.get(k)
        if k in _SINGLE_FACETS:
            out[k] = str(v or "")[:_FACET_ITEM_MAX]
        else:
            out[k] = [str(x)[:_FACET_ITEM_MAX] for x in (v if isinstance(v, list) else [])]
    return out


_FIRST_ROW_ID = "r1"


def _blank_row(row_id: str = "") -> dict:
    return {"id": row_id or uuid.uuid4().hex[:8],
            "cells": {c: _leaf("") for c in RESEARCH_COLS}}


def _norm_research(raw) -> dict:
    """正規化研究表；空表（新片 / 還沒寫過）補一列空白列。

    補的那列 id 固定 `r1`（不是隨機）：空表這份是「讀取時生成、不落地」的，
    前端拿到 r1 後 PATCH 才第一次把 research 寫進 DB。若 id 隨機，
    讀到的 id 與寫入時新生成的 id 會不同 → 永遠 404（實測踩過）。
    """
    src = raw if isinstance(raw, dict) else {}
    rows = []
    for r in (src.get("rows") if isinstance(src.get("rows"), list) else []):
        if not isinstance(r, dict):
            continue
        cells = r.get("cells") if isinstance(r.get("cells"), dict) else {}
        rows.append({"id": str(r.get("id") or uuid.uuid4().hex[:8]),
                     "cells": {c: _leaf(cells.get(c)) for c in RESEARCH_COLS}})
    return {"template_version": str(src.get("template_version") or RESEARCH_TEMPLATE_VERSION),
            "rows": rows or [_blank_row(_FIRST_ROW_ID)]}


def ref_dict(r, *, research: bool = True) -> dict:
    """參考片序列化（詳情頁契約 — 改欄位名要同步 reference-page.js）。

    research=False：清單用（省掉整份 JSONB 正規化；欄位本身也已 defer 不出庫）。
    provider/video_id 缺值時「讀時推導」—— 片子可能是舊端點或 v2 之前建的，
    這樣所有既有列都能直接播，不需要資料遷移或手動 refresh。
    """
    provider, vid = r.provider or "", r.video_id or ""
    thumb = r.thumb_url or ""
    if not provider:
        meta = _parse_video(r.url or "")
        provider, vid = meta["provider"], meta["video_id"]
        thumb = thumb or meta["thumb"]
    d = {
        "id": r.id,
        "url": r.url or "",
        "title": r.title or "",
        "note": r.note or "",
        "description": r.description or "",
        "thumb_url": thumb,
        "curated": bool(r.curated),
        "provider": provider,
        "video_id": vid,
        "facets": _norm_facets(r.facets),
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }
    if research:
        d["research"] = _norm_research(r.research)
    return d


# ── 取片 ────────────────────────────────────────────────────


async def _get_ref_or_404(session, rid: str, for_update: bool = False):
    from db.models import PreprodReference
    if for_update:
        from sqlalchemy import select
        ref = (await session.execute(
            select(PreprodReference).where(PreprodReference.id == rid).with_for_update()
        )).scalar_one_or_none()
    else:
        ref = await session.get(PreprodReference, rid)
    if not ref:
        raise HTTPException(status_code=404, detail="找不到此參考影片")
    return ref


async def _linked_targets(session, rid: str) -> list:
    """這支片被誰引用（反向連結）。階段 1 只有提案（階段 3 換成 links 表）。"""
    from sqlalchemy import select
    from db.models import PreprodProposal, PreprodProposalRef
    rows = (await session.execute(
        select(PreprodProposal.id, PreprodProposal.title, PreprodProposal.status)
        .join(PreprodProposalRef, PreprodProposalRef.proposal_id == PreprodProposal.id)
        .where(PreprodProposalRef.reference_id == rid)
    )).all()
    return [{"target_type": "proposal", "target_id": i, "title": t or "", "status": s or ""}
            for i, t, s in rows]


# ── 寫入核心（authed 與公開 token 兩路共用）────────────────────


def _check_text_size(value: str, limit: int, what: str):
    if len((value or "").encode("utf-8")) > limit:
        raise HTTPException(status_code=413, detail=f"{what}超過 {limit // 1024}KB 上限")


def _raise_conflict(answer: str, updated_at, updated_by: str):
    raise HTTPException(status_code=409, detail={
        "reason": "conflict", "server_answer": answer,
        "updated_at": updated_at, "updated_by": updated_by})


def apply_ref_patch(ref, req: ReferencePatch, user: str, *, public: bool = False) -> dict:
    """逐欄/逐格寫入。回 {updated_at} 供前端更新樂觀鎖基準。

    kind=field  一般欄位（title/url/note/description/curated/thumb_url）
    kind=facet  分類族（list 或 studio 單字串）
    kind=research 研究格（row_id + col，帶 base_updated_at 做樂觀鎖）
    """
    now = _now_iso()
    if public:                                  # 放行表是唯一正本（見 _PUBLIC_ALLOW）
        allow = _PUBLIC_ALLOW.get(req.kind, False)
        if allow is False:
            raise HTTPException(status_code=403, detail="此項目不可由共編連結修改")
        if isinstance(allow, tuple) and (req.key or "") not in allow:
            raise HTTPException(status_code=403, detail="此欄位不可由共編連結修改")
    if req.kind == "field":
        key = req.key or ""
        if key not in _EDITABLE_FIELDS:
            raise HTTPException(status_code=422, detail="不可修改的欄位")
        if key == "curated":
            ref.curated = bool(req.value)
        elif key == "url":
            url = str(req.value or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                raise HTTPException(status_code=422, detail="網址須以 http:// 或 https:// 開頭")
            if len(url) > 512:
                raise HTTPException(status_code=422, detail="網址過長")
            ref.url = url
            _sync_video_meta(ref)
        elif key == "title":
            ref.title = str(req.value or "").strip()[:255]
        elif key == "thumb_url":
            thumb = str(req.value or "").strip()
            if thumb and not thumb.lower().startswith(("http://", "https://", "/")):
                raise HTTPException(status_code=422, detail="封面網址不合法")
            ref.thumb_url = thumb[:512]
        else:                                   # note / description
            text = str(req.value or "")
            _check_text_size(text, _TEXT_MAX_BYTES, "內容")
            setattr(ref, key, text)
    elif req.kind == "facet":
        key = req.key or ""
        if key not in FACET_KEYS:
            raise HTTPException(status_code=422, detail="未知的分類族")
        facets = _norm_facets(ref.facets)
        if key in _SINGLE_FACETS:
            facets[key] = str(req.value or "").strip()[:_FACET_ITEM_MAX]
        else:
            if req.value is None:
                items = []
            elif isinstance(req.value, list):
                items = req.value
            else:
                raise HTTPException(status_code=422, detail="此分類須為字串陣列")
            seen, clean = set(), []
            for x in items:                      # 去重 + 去空白，保持輸入順序
                s = str(x).strip()[:_FACET_ITEM_MAX]
                if s and s not in seen:
                    seen.add(s)
                    clean.append(s)
            facets[key] = clean[:_FACET_MAX_ITEMS]
        ref.facets = facets
    elif req.kind == "research":
        col = req.col or ""
        if col not in RESEARCH_COLS:
            raise HTTPException(status_code=422, detail="未知的研究欄位")
        answer = str(req.value or "")
        _check_text_size(answer, _CELL_MAX_BYTES, "單格內容")
        research = _norm_research(ref.research)
        row = next((r for r in research["rows"] if r["id"] == (req.row_id or "")), None)
        if row is None:
            raise HTTPException(status_code=404, detail="找不到這一列研究（可能已被刪除）")
        cur = row["cells"].get(col) or _leaf("")
        if (req.base_updated_at is not None and cur["updated_at"]
                and cur["updated_at"] > req.base_updated_at):
            _raise_conflict(cur["answer"], cur["updated_at"], cur["updated_by"])
        row["cells"][col] = {"answer": answer, "updated_at": now, "updated_by": user}
        if len(json.dumps(research, ensure_ascii=False).encode("utf-8")) > _RESEARCH_MAX_BYTES:
            raise HTTPException(status_code=413, detail="研究內容超過 200KB 上限")
        ref.research = research
    else:
        raise HTTPException(status_code=422, detail="kind 須為 field/facet/research")
    ref.updated_at = datetime.now(timezone.utc)
    return {"updated_at": now}


def _append_research_row(ref) -> dict:
    """研究表加一列（登入與公開兩路同用；上限守在這裡，不在端點各寫一次）。"""
    research = _norm_research(ref.research)
    if len(research["rows"]) >= _RESEARCH_MAX_ROWS:
        raise HTTPException(status_code=409, detail=f"研究列已達 {_RESEARCH_MAX_ROWS} 列上限")
    row = _blank_row()
    research["rows"].append(row)
    ref.research = research
    ref.updated_at = datetime.now(timezone.utc)
    return row


def _guest_identity(name: Optional[str]) -> str:
    """公開路徑署名：一律加「(外部)」後綴 —— 不讓匿名者冒充內部帳號。"""
    n = (name or "").strip()[:32]
    return f"{n}(外部)" if n else "訪客"


# ── 清單（v2：facets 篩選 + 關鍵字 + 建檔狀態）──────────────────


@router.get("")
async def list_references_v2(request: Request, q: str = "", curated: str = "",
                             facet: str = "", value: str = "", limit: int = 200):
    """片庫清單。facet+value 成對使用（如 facet=technique&value=平行剪接）。
    回列表刻意不帶 research 全文（清單頁不需要）—— 只帶計數與封面。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import func as safunc, or_, select
    from sqlalchemy.orm import defer
    from db.models import PreprodProposalRef, PreprodReference

    take = max(1, min(limit, 500))
    facet_on = bool(facet and facet in FACET_KEYS and value)

    async with factory() as session:
        # defer(research)：清單不需要研究全文，別把每列的 JSONB 拖出庫
        query = select(PreprodReference).options(defer(PreprodReference.research))
        if q:
            ql = f"%{q}%"
            query = query.where(or_(PreprodReference.title.ilike(ql),
                                    PreprodReference.url.ilike(ql),
                                    PreprodReference.note.ilike(ql),
                                    PreprodReference.description.ilike(ql)))
        if curated in ("1", "0"):
            query = (query.where(PreprodReference.curated.is_(True)) if curated == "1"
                     else query.where(or_(PreprodReference.curated.is_(False),
                                          PreprodReference.curated.is_(None))))
        query = query.order_by(PreprodReference.created_at.desc())
        # facets 篩選在 Python 端做（JSONB 跨欄族的 SQL 很醜，片庫是幾百列量級）——
        # 但那就不能先在 SQL 截斷，否則「技巧=X」只會在最新 N 筆裡找。
        rows = (await session.execute(query if facet_on else query.limit(take))).scalars().all()

        out = []
        for r in rows:
            d = ref_dict(r, research=False)
            if facet_on:
                got = d["facets"][facet]
                if value not in (got if isinstance(got, list) else [got]):
                    continue
            out.append(d)
        total = len(out)
        out = out[:take]
        if out:
            counts = dict((await session.execute(
                select(PreprodProposalRef.reference_id, safunc.count(PreprodProposalRef.id))
                .where(PreprodProposalRef.reference_id.in_([d["id"] for d in out]))
                .group_by(PreprodProposalRef.reference_id))).all())
            for d in out:
                d["links_count"] = counts.get(d["id"], 0)
    return {"references": out, "total": total}


@router.get("/facet_options")
async def facet_options(request: Request):
    """各分類族的既有值（前端 datalist 建議 — 使用者仍可自由新增）。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import PreprodReference

    async with factory() as session:
        rows = (await session.execute(select(PreprodReference.facets))).all()
    acc = {k: set() for k in FACET_KEYS}
    for (raw,) in rows:
        f = _norm_facets(raw)
        for k in FACET_KEYS:
            vals = f[k] if isinstance(f[k], list) else ([f[k]] if f[k] else [])
            acc[k].update(vals)
    return {"options": {k: sorted(v) for k, v in acc.items()},
            "research_cols": list(RESEARCH_COLS)}


# ── 詳情 / 寫入（登入路徑）────────────────────────────────────


@router.get("/{rid}")
async def get_reference(rid: str, request: Request):
    _check_auth(request)
    factory = _require_factory()
    async with factory() as session:
        ref = await _get_ref_or_404(session, rid)
        d = ref_dict(ref)
        d["links"] = await _linked_targets(session, rid)   # 反向連結：這支片被誰引用
        return {"reference": d}


@router.patch("/{rid}")
async def patch_reference(rid: str, req: ReferencePatch, request: Request):
    payload = _check_auth(request)
    factory = _require_factory()
    user = payload.get("username") or ""
    async with factory() as session:
        ref = await _get_ref_or_404(session, rid, for_update=True)
        out = apply_ref_patch(ref, req, user)
        await session.commit()
    return {"status": "ok", **out}


@router.post("/{rid}/research/rows")
async def add_research_row(rid: str, request: Request):
    """研究表加一列（回新列 id — 前端直接渲染，不用重抓整份）。"""
    _check_auth(request)
    factory = _require_factory()
    async with factory() as session:
        ref = await _get_ref_or_404(session, rid, for_update=True)
        row = _append_research_row(ref)
        await session.commit()
    return {"status": "ok", "row": row}


@router.delete("/{rid}/research/rows/{row_id}")
async def delete_research_row(rid: str, row_id: str, request: Request):
    """刪一列研究。最後一列不給刪（永遠留一列可寫，避免空表無入口）。"""
    _check_auth(request)
    factory = _require_factory()
    async with factory() as session:
        ref = await _get_ref_or_404(session, rid, for_update=True)
        research = _norm_research(ref.research)
        if len(research["rows"]) <= 1:
            raise HTTPException(status_code=409, detail="至少要保留一列研究")
        before = len(research["rows"])
        research["rows"] = [r for r in research["rows"] if r["id"] != row_id]
        if len(research["rows"]) == before:
            raise HTTPException(status_code=404, detail="找不到這一列研究")
        ref.research = research
        ref.updated_at = datetime.now(timezone.utc)
        await session.commit()
    return {"status": "ok"}


# ── 公開路徑（借提案的 plan_share token）──────────────────────


async def assert_public_ref_writable(session, rid: str):
    """免登入連結能不能寫這支片 —— 片庫是跨提案共用資產，被別的提案也掛著時
    改動會影響其他案子看到的內容 → 409 請走後台。

    這是「公開改參考片」的唯一正本：api_references 的 shared PATCH 與
    api_proposals 的 `PATCH /shared/{token}/refs/{rid}` 都呼叫這裡，
    免得同一條組織規則在兩個 router 有兩個答案。
    """
    from sqlalchemy import func as safunc, select
    from db.models import PreprodProposalRef
    n = (await session.execute(
        select(safunc.count(PreprodProposalRef.id))
        .where(PreprodProposalRef.reference_id == rid))).scalar() or 0
    if n > 1:
        raise HTTPException(status_code=409, detail="這支參考影片被其他提案共用，請由後台編輯")


async def _public_ref(session, token: str, rid: str, for_update: bool = False):
    """token 驗簽 + 該片必須掛在該提案上（否則等於用一條連結讀整個片庫）。"""
    from sqlalchemy import select
    from db.models import PreprodProposalRef
    from routers.api_proposals import _get_prop_by_plan_token

    prop = await _get_prop_by_plan_token(session, token)
    link = (await session.execute(
        select(PreprodProposalRef).where(PreprodProposalRef.proposal_id == prop.id,
                                         PreprodProposalRef.reference_id == rid)
    )).scalars().first()
    if not link:
        raise HTTPException(status_code=404, detail="這支參考影片沒掛在本提案")
    return prop, await _get_ref_or_404(session, rid, for_update=for_update)


@router.get("/shared/{token}/{rid}")
async def get_shared_reference(token: str, rid: str):
    """公開讀取（免登入）：不回 links（別的案子引用了什麼是內部資訊）。"""
    factory = _require_factory()
    async with factory() as session:
        _, ref = await _public_ref(session, token, rid)
        return {"reference": ref_dict(ref)}   # 公開路徑不帶 links（別案引用是內部資訊）


@router.patch("/shared/{token}/{rid}")
async def patch_shared_reference(token: str, rid: str, req: ReferencePatch):
    """公開寫入（免登入）：只放行 title/note/description + 研究格。"""
    factory = _require_factory()
    async with factory() as session:
        _, ref = await _public_ref(session, token, rid, for_update=True)
        await assert_public_ref_writable(session, rid)
        out = apply_ref_patch(ref, req, _guest_identity(req.guest_name), public=True)
        await session.commit()
    return {"status": "ok", **out}


@router.post("/shared/{token}/{rid}/research/rows")
async def add_shared_research_row(token: str, rid: str):
    factory = _require_factory()
    async with factory() as session:
        _, ref = await _public_ref(session, token, rid, for_update=True)
        await assert_public_ref_writable(session, rid)
        row = _append_research_row(ref)
        await session.commit()
    return {"status": "ok", "row": row}
