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
import asyncio
import io
import json
import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile  # type: ignore

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

# ── 截圖（v2 階段 2）──
# 圖落地在 uploads/references/{rid}/（照 deck 上傳的既有慣例，main.py 已 mount /uploads）：
# 這頁只由 master serve（NAS 官網容器沒有 CRM 後端），放 NAS 圖床換不到可用性，
# 反而多一個離線失敗點。規劃 §4.1 原寫「走 paste 圖床」，這裡刻意改成與 deck 同路。
_SHOT_MAX_BYTES = 12 * 1024 * 1024     # 單張上限（截圖/手機照綽綽有餘）
_SHOT_MAX_SIDE = 1800                  # 長邊縮到 1800 後轉 WebP
_SHOTS_MAX = 40                        # 每支片截圖上限
_SHOT_CAPTION_MAX = 255
_SHOT_TIMECODE_MAX = 16
_SHOT_SHAPES_MAX = 200                 # 單張標示圖形數上限
_SHOT_ANNO_MAX_BYTES = 64 * 1024       # 單張標示總量（手繪點數會長，要有天花板）
_UPLOAD_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
_SHOT_EDITABLE = ("caption", "timecode", "annotations")
# 公開連結（免登入）的放行表：kind → True 全放 / tuple 白名單 / False 全禁。
# 這是「共編連結能改什麼」的唯一正本 —— 別在端點裡另外寫 if。
_PUBLIC_ALLOW = {"field": ("title", "note", "description"), "research": True, "facet": False}


def _check_auth(request: Request) -> dict:
    """片庫本身的閘門：片庫 tab（references）或提案庫兩系模組任一即可。"""
    return check_admin_or_module(request, "references", "preprod_proposals", "preprod_plan")


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
    # embed_url 由 parse_video_url 產（Vimeo 未公開影片的 ?h= 私密雜湊在裡面）——
    # 前端一律用它嵌入，不要自己拿 video_id 拼
    return {"provider": provider, "video_id": vid, "thumb": thumb,
            "embed_url": d.get("embed_url") or ""}


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


def _clean_facet_values(key: str, items):
    """分類值清洗（去空白 → 截長 → 去重 → 上限）—— PATCH / CSV 匯入 / AI 建議共用。
    單選族回字串，多選族回 list。原本三處各寫一份，且已經分岔（只有 PATCH 會去重）。"""
    if key in _SINGLE_FACETS:
        first = items if isinstance(items, str) else (items[0] if items else "")
        return str(first or "").strip()[:_FACET_ITEM_MAX]
    seen, out = set(), []
    for x in (items if isinstance(items, (list, tuple)) else []):
        v = str(x).strip()[:_FACET_ITEM_MAX]
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out[:_FACET_MAX_ITEMS]


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
    meta = _parse_video(r.url or "")          # embed_url 一律即時推導（欄位沒存它）
    provider, vid = r.provider or "", r.video_id or ""
    thumb = r.thumb_url or ""
    if not provider:
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
        "embed_url": meta["embed_url"],
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
    """這支片被誰引用（反向連結）—— 提案與 CRM 專案都算，標題一次 JOIN 帶回。"""
    from sqlalchemy import select
    from db.models import CrmProject, PreprodProposal, PreprodReferenceLink

    links = (await session.execute(
        select(PreprodReferenceLink)
        .where(PreprodReferenceLink.reference_id == rid)
        .order_by(PreprodReferenceLink.created_at))).scalars().all()
    if not links:
        return []
    p_ids = [x.target_id for x in links if x.target_type == "proposal"]
    j_ids = [x.target_id for x in links if x.target_type == "crm_project"]
    titles = {}
    if p_ids:
        for i, t, st in (await session.execute(
                select(PreprodProposal.id, PreprodProposal.title, PreprodProposal.status)
                .where(PreprodProposal.id.in_(p_ids)))).all():
            titles[("proposal", i)] = (t or "", st or "")
    if j_ids:
        for i, t, st in (await session.execute(
                select(CrmProject.id, CrmProject.name, CrmProject.status)
                .where(CrmProject.id.in_(j_ids)))).all():
            titles[("crm_project", i)] = (t or "", st or "")
    out = []
    for x in links:
        title, status = titles.get((x.target_type, x.target_id), ("", ""))
        out.append({"link_id": x.id, "target_type": x.target_type, "target_id": x.target_id,
                    "title": title, "status": status, "note": x.note or ""})
    return out


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
        if key not in _SINGLE_FACETS and req.value is not None and not isinstance(req.value, list):
            raise HTTPException(status_code=422, detail="此分類須為字串陣列")
        facets = _norm_facets(ref.facets)
        facets[key] = _clean_facet_values(key, req.value if req.value is not None else [])
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


def shot_dict(s) -> dict:
    """截圖序列化（改欄位名要同步 reference-page.js / annotate.js）。"""
    return {"id": s.id, "image_url": s.image_url or "", "timecode": s.timecode or "",
            "caption": s.caption or "", "annotations": _norm_annotations(s.annotations),
            "sort_order": s.sort_order or 0, "created_by": s.created_by or "",
            "created_at": s.created_at.isoformat() if s.created_at else None}


def _clamp(v, default: float = 0.0) -> float:
    """座標夾擠到 -1~2（容許少量出界的拖曳，但不給離譜值）；不可解析 → default。"""
    try:
        return max(-1.0, min(2.0, float(v)))
    except (TypeError, ValueError):
        return default


def _norm_annotations(raw) -> dict:
    """標示圖形正規化：座標一律 0~1 相對值（圖片縮放不跑位），未知型別丟掉。

    形狀契約（前後端共同，改這裡要同步 annotate.js）：
      {v:1, shapes:[{type:'rect'|'arrow'|'text'|'pen', x,y,w,h, x2,y2,
                     points:[[x,y]…], text, color, width}]}
    """
    src = raw if isinstance(raw, dict) else {}
    shapes = src.get("shapes") if isinstance(src.get("shapes"), list) else []
    if len(shapes) > _SHOT_SHAPES_MAX:            # 靜默截斷會讓人以為存好了 → 明講
        raise HTTPException(status_code=413, detail=f"單張標示超過 {_SHOT_SHAPES_MAX} 個上限")
    out = []
    for sh in shapes:
        if not isinstance(sh, dict) or sh.get("type") not in ("rect", "arrow", "text", "pen"):
            continue
        try:
            width = max(1, min(12, int(sh.get("width") or 3)))
        except (TypeError, ValueError):
            width = 3
        item = {"type": sh["type"], "x": _clamp(sh.get("x")), "y": _clamp(sh.get("y")),
                "color": str(sh.get("color") or "#e05252")[:24], "width": width}
        if sh["type"] == "rect":
            item.update(w=_clamp(sh.get("w")), h=_clamp(sh.get("h")))
        elif sh["type"] == "arrow":
            item.update(x2=_clamp(sh.get("x2")), y2=_clamp(sh.get("y2")))
        elif sh["type"] == "text":
            item["text"] = str(sh.get("text") or "")[:200]
        else:                                     # pen
            pts = sh.get("points") if isinstance(sh.get("points"), list) else []
            item["points"] = [[_clamp(p[0]), _clamp(p[1])] for p in pts[:400]
                              if isinstance(p, (list, tuple)) and len(p) >= 2]
        out.append(item)
    result = {"v": 1, "shapes": out}
    _check_text_size(json.dumps(result, ensure_ascii=False), _SHOT_ANNO_MAX_BYTES, "標示")
    return result


async def _shots_of(session, rid: str) -> list:
    from sqlalchemy import select
    from db.models import PreprodReferenceShot
    rows = (await session.execute(
        select(PreprodReferenceShot)
        .where(PreprodReferenceShot.reference_id == rid)
        .order_by(PreprodReferenceShot.sort_order, PreprodReferenceShot.created_at)
    )).scalars().all()
    return [shot_dict(s) for s in rows]


async def _add_shots(session, rid: str, files, user: str, key: str = "") -> list:
    """存多張圖（轉 WebP）+ 建列（呼叫端 commit）。

    收 list[UploadFile] 而非單檔：貼一整批截圖是常態，逐張一個 request 會讓
    上限檢查與 sort_order 都只能逐張近似，前端還得自己寫進度/中斷樣板。
    """
    from sqlalchemy import func as safunc, select
    from db.models import PreprodReferenceShot
    from core.image_utils import save_webp_or_none

    n = (await session.execute(
        select(safunc.count(PreprodReferenceShot.id))
        .where(PreprodReferenceShot.reference_id == rid))).scalar() or 0
    if n + len(files) > _SHOTS_MAX:
        raise HTTPException(status_code=409,
                            detail=f"截圖上限 {_SHOTS_MAX} 張（目前 {n} 張、這批 {len(files)} 張）")

    dest_dir = os.path.join(_UPLOAD_BASE, "references", rid)
    out = []
    for f in files:
        content = await f.read()
        if not content:
            raise HTTPException(status_code=400, detail="空檔案")
        if len(content) > _SHOT_MAX_BYTES:
            raise HTTPException(status_code=413, detail="圖片超過 12MB 上限")
        sid = uuid.uuid4().hex
        saved = await asyncio.to_thread(save_webp_or_none, content, dest_dir, sid, _SHOT_MAX_SIDE)
        if saved is None:
            raise HTTPException(status_code=400, detail="無法辨識的圖片格式")
        n += 1
        shot = PreprodReferenceShot(
            id=sid, reference_id=rid,
            image_url=f"/uploads/references/{rid}/{os.path.basename(saved)}",
            sort_order=n, created_by=user, created_key=key or None)
        session.add(shot)
        out.append(shot)
    return out


async def _get_shot_or_404(session, rid: str, sid: str):
    from db.models import PreprodReferenceShot
    shot = await session.get(PreprodReferenceShot, sid)
    if not shot or shot.reference_id != rid:
        raise HTTPException(status_code=404, detail="找不到這張截圖")
    return shot


def _apply_shot_patch(shot, body: dict):
    """截圖欄位寫入（caption/timecode/annotations）—— 登入與公開兩路共用。"""
    touched = False
    for key in _SHOT_EDITABLE:
        if key not in body:
            continue
        touched = True
        if key == "annotations":
            shot.annotations = _norm_annotations(body.get(key))
        elif key == "timecode":
            shot.timecode = str(body.get(key) or "").strip()[:_SHOT_TIMECODE_MAX]
        else:
            shot.caption = str(body.get(key) or "").strip()[:_SHOT_CAPTION_MAX]
    if not touched:
        raise HTTPException(status_code=422, detail="沒有可寫入的欄位（caption/timecode/annotations）")


def _delete_shot_file(image_url: str):
    """best-effort 刪檔（只刪本模組自產的 /uploads/references/… 路徑）。"""
    if not image_url.startswith("/uploads/references/"):
        return
    root = os.path.realpath(os.path.join(_UPLOAD_BASE, "references"))
    target = os.path.realpath(os.path.join(_UPLOAD_BASE, *image_url.split("/")[2:]))
    if os.path.commonpath([root, target]) != root:      # 前綴過關但含 ../ 的字串
        return
    try:
        os.remove(target)
    except OSError:
        pass


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


@router.post("")
async def create_reference_v2(request: Request, body: dict = Body(...)):
    """建一支參考片（v2 入口）。body {url, title?, note?, link?: {target_type, target_id, note?}}。

    - **以 url 去重**：同一條網址已在片庫 → 不重建，回既有那支（idempotent —— 片庫是
      共用資產，同一支片兩個實體會讓研究/截圖散在兩處）。
    - `link` 一併帶時，順手掛到該提案/專案（權限依對象把關，同 add_link）——
      CRM 分頁「貼網址→建檔→引用」是一個動作，不用打兩趟。
    """
    url = str(body.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="網址須以 http:// 或 https:// 開頭")
    if len(url) > 512:
        raise HTTPException(status_code=422, detail="網址過長")
    link = body.get("link") if isinstance(body.get("link"), dict) else None
    if link:
        target_type = str(link.get("target_type") or "")
        payload = _check_target_auth(request, target_type)   # 有 link → 依對象把關
    else:
        payload = _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import PreprodReference, PreprodReferenceLink

    async with factory() as session:
        ref = (await session.execute(
            select(PreprodReference).where(PreprodReference.url == url))).scalars().first()
        created = ref is None
        if created:
            ref = PreprodReference(
                id=uuid.uuid4().hex, url=url,
                title=str(body.get("title") or "").strip()[:255],
                note=str(body.get("note") or "")[:2000])
            _sync_video_meta(ref)
            session.add(ref)
        if link:
            target_id = str(link.get("target_id") or "").strip()
            if await _target_title(session, target_type, target_id) is None:
                raise HTTPException(status_code=404, detail="找不到要引用的提案／專案")
            exists = (await session.execute(
                select(PreprodReferenceLink)
                .where(PreprodReferenceLink.reference_id == ref.id,
                       PreprodReferenceLink.target_type == target_type,
                       PreprodReferenceLink.target_id == target_id))).scalars().first()
            if not exists:
                session.add(PreprodReferenceLink(
                    id=uuid.uuid4().hex, reference_id=ref.id,
                    target_type=target_type, target_id=target_id,
                    note=str(link.get("note") or "")[:255],
                    created_by=payload.get("username") or ""))
        await session.commit()
        await session.refresh(ref)
        return {"status": "ok", "created": created, "reference": ref_dict(ref, research=False)}


@router.get("")
async def list_references_v2(request: Request, q: str = "", curated: str = "",
                             facet: str = "", value: str = "", unused: str = "",
                             limit: int = 200):
    # 讀片庫：提案庫或 CRM 專案的人都要能挑片（寫入才依對象分別把關，見 _check_target_auth）
    """片庫清單。facet+value 成對使用（如 facet=technique&value=平行剪接）。
    回列表刻意不帶 research 全文（清單頁不需要）—— 只帶計數與封面。"""
    check_admin_or_module(request, "references", "preprod_proposals", "preprod_plan", "crm_projects")
    factory = _require_factory()

    from sqlalchemy import func as safunc, or_, select
    from sqlalchemy.orm import defer
    from db.models import PreprodReference, PreprodReferenceLink

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
        if unused == "1":     # 只看沒被任何案子引用的（清理用）—— 要在 SQL 就濾掉，
            query = query.where(   # 否則「最新 200 筆之外的孤兒」永遠看不到
                ~select(PreprodReferenceLink.id)
                .where(PreprodReferenceLink.reference_id == PreprodReference.id)
                .exists())
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
                select(PreprodReferenceLink.reference_id, safunc.count(PreprodReferenceLink.id))
                .where(PreprodReferenceLink.reference_id.in_([d["id"] for d in out]))
                .group_by(PreprodReferenceLink.reference_id))).all())
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
        d["shots"] = await _shots_of(session, rid)
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


# ── 引用（跨提案／專案，docs/REFERENCE_LIBRARY.md §6）──────────
# 片庫是共用資產：同一支片可以同時被多個提案與 CRM 專案引用，note 記「本案為什麼引用它」。
# 解除只斷連結，片庫本體保留。

# 引用對象 → (model, 標題欄, 需要的模組)。加新型別（如 'work'）只改這一張表。
def _target_models():
    from db.models import CrmProject, PreprodProposal
    return {
        "proposal": (PreprodProposal, PreprodProposal.title,
                     ("preprod_proposals", "preprod_plan")),   # 提案的引用歸提案庫管
        "crm_project": (CrmProject, CrmProject.name, ("crm_projects",)),
    }


_TARGET_TYPES = ("proposal", "crm_project")


def _check_target_auth(request: Request, target_type: str) -> dict:
    """依**引用對象**把關，不是給一張通行證：CRM 專案分頁的使用者可能只有
    crm_projects 模組，他該能在自己的專案上掛片 —— 但不該因此能動提案的引用。"""
    spec = _target_models().get(target_type)
    if not spec:
        raise HTTPException(status_code=422, detail="未知的引用對象型別")
    return check_admin_or_module(request, *spec[2])


async def _target_title(session, target_type: str, target_id: str) -> str:
    """回標題；找不到該對象回 None（空標題的提案不該被當成不存在）。"""
    from sqlalchemy import select
    model, title_col, _ = _target_models()[target_type]
    row = (await session.execute(
        select(model.id, title_col).where(model.id == target_id))).first()
    return None if row is None else (row[1] or "")


@router.get("/for/{target_type}/{target_id}")
async def list_refs_for_target(target_type: str, target_id: str, request: Request):
    """某個提案／專案引用了哪些片（CRM 專案「參考影片」分頁用）。"""
    _check_target_auth(request, target_type)
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import PreprodReference, PreprodReferenceLink

    async with factory() as session:
        rows = (await session.execute(
            select(PreprodReferenceLink, PreprodReference)
            .join(PreprodReference, PreprodReference.id == PreprodReferenceLink.reference_id)
            .where(PreprodReferenceLink.target_type == target_type,
                   PreprodReferenceLink.target_id == target_id)
            .order_by(PreprodReferenceLink.created_at))).all()
        out = []
        for link, ref in rows:
            d = ref_dict(ref, research=False)
            d.update(link_id=link.id, link_note=link.note or "")
            out.append(d)
    return {"references": out}


@router.post("/{rid}/links")
async def add_link(rid: str, request: Request, body: dict = Body(...)):
    """把片掛到提案／專案（重複掛載冪等）。body {target_type, target_id, note?}"""
    target_type = str(body.get("target_type") or "")
    target_id = str(body.get("target_id") or "").strip()
    if target_type not in _TARGET_TYPES or not target_id:
        raise HTTPException(status_code=422, detail="target_type / target_id 不合法")
    payload = _check_target_auth(request, target_type)
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import PreprodReferenceLink

    async with factory() as session:
        await _get_ref_or_404(session, rid)
        if await _target_title(session, target_type, target_id) is None:
            raise HTTPException(status_code=404, detail="找不到要引用的提案／專案")
        link = (await session.execute(
            select(PreprodReferenceLink)
            .where(PreprodReferenceLink.reference_id == rid,
                   PreprodReferenceLink.target_type == target_type,
                   PreprodReferenceLink.target_id == target_id))).scalars().first()
        if not link:
            link = PreprodReferenceLink(
                id=uuid.uuid4().hex, reference_id=rid, target_type=target_type,
                target_id=target_id, note=str(body.get("note") or "")[:255],
                created_by=payload.get("username") or "")
            session.add(link)
        elif "note" in body:
            link.note = str(body.get("note") or "")[:255]
        await session.commit()
        return {"status": "ok", "link_id": link.id}


@router.delete("/links/{link_id}")
async def delete_link(link_id: str, request: Request):
    """解除引用（只斷連結，片庫本體與其他引用不動）。

    先讀出這筆掛在什麼對象上，再依**那個對象**把關 —— 否則只有 CRM 權限的人
    能拿一個 link id 解掉提案的引用。"""
    factory = _require_factory()

    from db.models import PreprodReferenceLink

    async with factory() as session:
        link = await session.get(PreprodReferenceLink, link_id)
        if not link:
            raise HTTPException(status_code=404, detail="找不到這筆引用")
        _check_target_auth(request, link.target_type)
        await session.delete(link)
        await session.commit()
    return {"status": "ok"}


# ── 截圖（登入路徑）────────────────────────────────────────


@router.post("/{rid}/shots")
async def add_shots(rid: str, request: Request, files: List[UploadFile] = File(...)):
    """上傳/貼上截圖（可多張，自動轉 WebP、長邊 1800）。"""
    payload = _check_auth(request)
    factory = _require_factory()
    async with factory() as session:
        await _get_ref_or_404(session, rid)
        shots = await _add_shots(session, rid, files, payload.get("username") or "")
        await session.commit()
        return {"status": "ok", "shots": [shot_dict(x) for x in shots]}


@router.patch("/{rid}/shots/{sid}")
async def patch_shot(rid: str, sid: str, request: Request, body: dict = Body(...)):
    """改截圖的說明 / 時間碼 / 標示圖形。"""
    _check_auth(request)
    factory = _require_factory()
    async with factory() as session:
        shot = await _get_shot_or_404(session, rid, sid)
        _apply_shot_patch(shot, body)
        await session.commit()
        return {"status": "ok", "shot": shot_dict(shot)}


@router.delete("/{rid}/shots/{sid}")
async def delete_shot(rid: str, sid: str, request: Request):
    _check_auth(request)
    factory = _require_factory()
    async with factory() as session:
        shot = await _get_shot_or_404(session, rid, sid)
        image_url = shot.image_url or ""
        await session.delete(shot)
        await session.commit()
    _delete_shot_file(image_url)
    return {"status": "ok"}


@router.post("/{rid}/shots/reorder")
async def reorder_shots(rid: str, request: Request, body: dict = Body(...)):
    """重排截圖：body {ids: [...]}（沒帶到的維持原順序排在後面）。"""
    _check_auth(request)
    ids = body.get("ids")
    if not isinstance(ids, list):
        raise HTTPException(status_code=422, detail="ids 須為陣列")
    factory = _require_factory()
    from sqlalchemy import select
    from db.models import PreprodReferenceShot
    async with factory() as session:
        rows = (await session.execute(
            select(PreprodReferenceShot)
            .where(PreprodReferenceShot.reference_id == rid))).scalars().all()
        order = {str(i): n for n, i in enumerate(ids, start=1)}
        for s_ in rows:
            want = order.get(s_.id)
            if want is not None and s_.sort_order != want:   # 沒列到的維持原順序，別壓平
                s_.sort_order = want
        await session.commit()
        return {"status": "ok", "order": order}


# ── 階段 4：匯入 / 封面 / AI 建議 ─────────────────────────────

# Notion「參考影片資料庫」匯出 CSV 的欄名 → 本系統欄位。Notion 匯出的標題列
# 可能帶型別後綴（如「連結」或「連結 (URL)」），所以比對用「開頭相符」。
_CSV_MAP = {   # 內部欄位 → 可接受的 CSV 欄名（要吃新匯出格式就往 tuple 加）
    "title": ("項目", "名稱", "Name"), "url": ("連結", "連結 (URL)", "網址", "URL"),
    "description": ("說明",), "note": ("備註",), "curated": ("建檔完成",),
    "category": ("類別",), "brand": ("品牌",), "studio": ("製作單位",),
    "paragon": ("典範",), "technique": ("技巧",), "emotion": ("情感取向",),
    "keyword": ("關鍵字",), "study": ("內部專案",),
}
_CSV_TRUE = ("yes", "true", "1", "是", "v", "x")
_CSV_MAX_BYTES = 4 * 1024 * 1024
_CSV_MAX_ROWS = 2000


def _csv_field(header: str) -> str:
    """CSV 欄名 → 內部欄位（別名精確比對；找不到回空字串）。

    刻意不用「開頭相符」：Notion 若出現「項目說明」這種欄名會誤中「項目」。
    """
    h = (header or "").strip().lstrip("\ufeff")
    for field, aliases in _CSV_MAP.items():
        if h in aliases:
            return field
    return ""


def _row_to_ref_fields(header: list, line: list):
    """CSV 一列 → (一般欄位 dict, facets dict)。純函式 —— Notion 匯出格式的解析規則
    （逗號分隔的 multi_select、建檔完成的真值集合）集中在這裡，可單獨驗。"""
    rec, facets = {}, {k: _clean_facet_values(k, []) for k in FACET_KEYS}
    for i, cell in enumerate(line):
        key = header[i] if i < len(header) else ""
        val = (cell or "").strip()
        if not key or not val:
            continue
        if key in FACET_KEYS:
            facets[key] = _clean_facet_values(
                key, val if key in _SINGLE_FACETS else val.split(","))
        elif key == "curated":
            rec["curated"] = val.lower() in _CSV_TRUE
        else:
            rec[key] = val
    return rec, facets


@router.post("/import_csv")
async def import_csv(request: Request, file: UploadFile = File(...)):
    """匯入 Notion 匯出的參考影片 CSV（以 url 去重，已存在的跳過不覆蓋）。

    只做「新增」不做更新：匯入是一次性搬家，之後正本在這裡；
    若允許覆蓋，第二次匯入會把大家在系統裡寫的研究/分類洗回 Notion 的舊值。
    """
    payload = _check_auth(request)
    raw = await file.read()
    if len(raw) > _CSV_MAX_BYTES:
        raise HTTPException(status_code=413, detail="CSV 超過 4MB 上限")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("big5")          # 有人會用 Excel 另存
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="無法辨識的檔案編碼（請存成 UTF-8 CSV）")

    import csv as _csv
    rows = list(_csv.reader(io.StringIO(text)))
    if not rows:
        raise HTTPException(status_code=400, detail="空的 CSV")
    header = [_csv_field(h) for h in rows[0]]
    if "url" not in header:
        raise HTTPException(status_code=422,
                            detail="找不到「連結」欄 —— 這看起來不是參考影片資料庫的匯出檔")
    if len(rows) - 1 > _CSV_MAX_ROWS:
        raise HTTPException(status_code=413, detail=f"超過 {_CSV_MAX_ROWS} 列上限")

    factory = _require_factory()
    from sqlalchemy import select
    from db.models import PreprodReference

    user = payload.get("username") or ""
    created, skipped, bad = 0, 0, 0
    async with factory() as session:
        known = set((await session.execute(select(PreprodReference.url))).scalars().all())
        for line in rows[1:]:
            rec, facets = _row_to_ref_fields(header, line)
            url = (rec.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                bad += 1
                continue
            if url in known:
                skipped += 1
                continue
            ref = PreprodReference(
                id=uuid.uuid4().hex, url=url[:512],
                title=(rec.get("title") or "")[:255],
                note=rec.get("note") or "", description=rec.get("description") or "",
                curated=bool(rec.get("curated")), facets=facets)
            _sync_video_meta(ref)
            session.add(ref)
            known.add(url)
            created += 1
        await session.commit()
    return {"status": "ok", "imported": created, "skipped": skipped, "invalid": bad, "by": user}


def _fetch_oembed_thumb(url: str) -> str:
    """Vimeo / Facebook 的封面（oEmbed，3 秒逾時，失敗回空字串）。

    只在使用者按「抓封面」時才打 —— 不放進寫入路徑：那會讓每次改連結都做一次
    對外網路 I/O，NAS/對外斷線時整個存檔跟著卡住。
    """
    import json as _json
    import urllib.parse
    import urllib.request
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "vimeo.com" or host.endswith(".vimeo.com"):
        api = "https://vimeo.com/api/oembed.json?url=" + urllib.parse.quote(url, safe="")
    elif host.endswith("facebook.com") or host.endswith("fb.watch"):
        api = ("https://graph.facebook.com/v12.0/oembed_video?url="
               + urllib.parse.quote(url, safe=""))
    else:
        return ""
    try:
        with urllib.request.urlopen(api, timeout=3) as resp:
            data = _json.loads(resp.read(1_000_000).decode("utf-8", errors="replace"))
        thumb = str(data.get("thumbnail_url") or "")
        return thumb if thumb.startswith("https://") else ""
    except Exception:
        return ""                      # 對外抓不到封面不是錯誤，使用者可自己上傳


@router.post("/{rid}/cover_fetch")
async def cover_fetch(rid: str, request: Request):
    """抓外站封面（Vimeo/FB oEmbed）—— YouTube 不需要（網址可直接組）。"""
    _check_auth(request)
    factory = _require_factory()
    async with factory() as session:        # 先短查拿 url —— 別把列鎖與連線抱著出去打網路
        ref = await _get_ref_or_404(session, rid)
        url = ref.url or ""
    thumb = await asyncio.to_thread(_fetch_oembed_thumb, url)
    if not thumb:
        raise HTTPException(status_code=404, detail="這個連結抓不到封面（可改用截圖當封面）")
    async with factory() as session:
        ref = await _get_ref_or_404(session, rid, for_update=True)
        ref.thumb_url = thumb[:512]
        ref.updated_at = datetime.now(timezone.utc)
        await session.commit()
        return {"status": "ok", "thumb_url": ref.thumb_url}


_AI_MAX_PER_FACET = 4


def _build_facet_prompt(ref, research: str) -> str:
    """組 AI 建議分類的 prompt（f-string 直組，比 .replace 串接安全：
    使用者資料裡若含 `{url}` 這種字樣不會被當成佔位符再展開一次）。"""
    return f"""你是影像製作公司的資料整理助理。根據下面這支參考影片的資訊，建議分類標籤。
只回 JSON，不要任何其他文字。

格式：{{"category": [], "technique": [], "emotion": [], "keyword": [], "brand": [], "paragon": []}}

規則：
- category 從這些挑（可多選，沒把握就留空）：商業廣告 影視服務 活動紀錄 動態設計 企業形象 劇情短片 劇情長片 紀實短片 紀錄長片 宣傳影片 音樂MV 短影音 節目
- technique 是剪輯/敘事手法（如：平行剪接、分割畫面、匹配剪輯、反轉、多事件剪輯）
- emotion 是情感取向（如：溫暖、幽默、衝擊、療癒）
- keyword 是題材關鍵字（如：孩童、公益、汽車、旅遊）
- brand 是出現的品牌或委託單位；paragon 是導演或製作單位
- 每族最多 {_AI_MAX_PER_FACET} 個；不確定的一律留空陣列，不要猜

影片資訊：
標題：{ref.title or ""}
連結：{ref.url or ""}
心得：{(ref.note or "")[:500]}
說明：{(ref.description or "")[:800]}
研究筆記：{research}
"""


@router.post("/{rid}/ai_facets")
async def ai_facets(rid: str, request: Request):
    """用 claude CLI 讀這支片已填的資訊，建議分類（**只回建議不寫入**，由人按下才套用）。

    只有 master 有 claude CLI（見 reference_ai_seo_runner 的機隊陷阱），
    其他機器直接回 503 講清楚，而不是讓使用者對著沒反應的按鈕猜。
    """
    _check_auth(request)
    # 閘門問的是「這台有沒有 claude CLI」—— 不是 is_master_machine()（那是排程用的
    # 「機隊只跑一次」閘，會把裝了 CLI 的 dev 機也擋掉）。其他互動式 AI 端點同此慣例。
    from services.website.seo_runner import _call_claude, _resolve_claude_exe
    if not _resolve_claude_exe():
        raise HTTPException(status_code=503, detail="這台機器沒有 claude CLI（AI 建議只在主控端可用）")
    factory = _require_factory()
    async with factory() as session:
        ref = await _get_ref_or_404(session, rid)
        research = " / ".join(
            leaf["answer"] for row in _norm_research(ref.research)["rows"]
            for leaf in row["cells"].values() if leaf["answer"])[:1500]
        prompt = _build_facet_prompt(ref, research)
    text, err = await _call_claude(prompt)
    if not text:
        raise HTTPException(status_code=502, detail=f"AI 建議失敗：{err}")
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        data = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=502, detail="AI 回應不是預期的 JSON")
    out = {}
    for k in FACET_KEYS:
        v = data.get(k)
        if isinstance(v, list):
            out[k] = _clean_facet_values(k, v)[:_AI_MAX_PER_FACET]
    return {"status": "ok", "suggestions": out}


# ── 公開路徑（借提案的 plan_share token）──────────────────────


async def assert_public_ref_writable(session, rid: str):
    """免登入連結能不能寫這支片 —— 片庫是跨提案共用資產，被別的提案也掛著時
    改動會影響其他案子看到的內容 → 409 請走後台。

    這是「公開改參考片」的唯一正本：api_references 的 shared PATCH 與
    api_proposals 的 `PATCH /shared/{token}/refs/{rid}` 都呼叫這裡，
    免得同一條組織規則在兩個 router 有兩個答案。
    """
    from sqlalchemy import func as safunc, select
    from db.models import PreprodReferenceLink
    n = (await session.execute(
        select(safunc.count(PreprodReferenceLink.id))
        .where(PreprodReferenceLink.reference_id == rid))).scalar() or 0
    if n > 1:
        raise HTTPException(status_code=409, detail="這支參考影片被其他提案共用，請由後台編輯")


async def _public_ref(session, token: str, rid: str, for_update: bool = False,
                      write: bool = False):
    """token 驗簽 + 該片必須掛在該提案上（否則等於用一條連結讀整個片庫）。
    write=True 一併過共用資產護欄 —— 權限配對綁在這裡，不靠每個端點記得抄兩行。"""
    from sqlalchemy import select
    from db.models import PreprodReferenceLink
    from routers.api_proposals import _get_prop_by_plan_token

    prop = await _get_prop_by_plan_token(session, token)
    link = (await session.execute(
        select(PreprodReferenceLink)
        .where(PreprodReferenceLink.target_type == "proposal",
               PreprodReferenceLink.target_id == prop.id,
               PreprodReferenceLink.reference_id == rid))).scalars().first()
    if not link:
        raise HTTPException(status_code=404, detail="這支參考影片沒掛在本提案")
    if write:
        await assert_public_ref_writable(session, rid)
    return prop, await _get_ref_or_404(session, rid, for_update=for_update)


@router.get("/shared/{token}/{rid}")
async def get_shared_reference(token: str, rid: str):
    """公開讀取（免登入）：不回 links（別的案子引用了什麼是內部資訊）。"""
    factory = _require_factory()
    async with factory() as session:
        _, ref = await _public_ref(session, token, rid)
        d = ref_dict(ref)                    # 公開路徑不帶 links（別案引用是內部資訊）
        d["shots"] = await _shots_of(session, rid)
        return {"reference": d}


@router.patch("/shared/{token}/{rid}")
async def patch_shared_reference(token: str, rid: str, req: ReferencePatch):
    """公開寫入（免登入）：只放行 title/note/description + 研究格。"""
    factory = _require_factory()
    async with factory() as session:
        _, ref = await _public_ref(session, token, rid, for_update=True, write=True)
        out = apply_ref_patch(ref, req, _guest_identity(req.guest_name), public=True)
        await session.commit()
    return {"status": "ok", **out}


@router.post("/shared/{token}/{rid}/research/rows")
async def add_shared_research_row(token: str, rid: str):
    factory = _require_factory()
    async with factory() as session:
        _, ref = await _public_ref(session, token, rid, for_update=True, write=True)
        row = _append_research_row(ref)
        await session.commit()
    return {"status": "ok", "row": row}


@router.post("/shared/{token}/{rid}/shots")
async def add_shared_shots(token: str, rid: str, guest_name: str = "", guest_key: str = "",
                           files: List[UploadFile] = File(...)):
    """公開貼截圖（免登入，可多張）—— 署名加「(外部)」，另存 guest_key 供「只刪自己的」。"""
    factory = _require_factory()
    async with factory() as session:
        await _public_ref(session, token, rid, write=True)
        shots = await _add_shots(session, rid, files, _guest_identity(guest_name),
                                 key=(guest_key or "")[:64])
        await session.commit()
        return {"status": "ok", "shots": [shot_dict(x) for x in shots]}


@router.patch("/shared/{token}/{rid}/shots/{sid}")
async def patch_shared_shot(token: str, rid: str, sid: str, body: dict = Body(...)):
    """公開改截圖說明/時間碼/標示（任何一張都可以標 —— 標示是共同看片的產物）。"""
    factory = _require_factory()
    async with factory() as session:
        await _public_ref(session, token, rid, write=True)
        shot = await _get_shot_or_404(session, rid, sid)
        _apply_shot_patch(shot, body)
        await session.commit()
        return {"status": "ok", "shot": shot_dict(shot)}


@router.delete("/shared/{token}/{rid}/shots/{sid}")
async def delete_shared_shot(token: str, rid: str, sid: str, guest_name: str = "",
                             guest_key: str = ""):
    """公開刪截圖：**只能刪自己貼的**。

    憑證是瀏覽器自己產的不可見 guest_key（localStorage），不是 created_by 署名 ——
    署名會直接印在截圖卡上（就在刪除鈕旁邊），拿它當憑證等於把鑰匙貼在門上。
    舊列沒有 key → 退回比對署名（至少擋得住訪客刪內部帳號貼的圖）。"""
    factory = _require_factory()
    async with factory() as session:
        await _public_ref(session, token, rid, write=True)
        shot = await _get_shot_or_404(session, rid, sid)
        mine = ((shot.created_key and guest_key and shot.created_key == guest_key)
                or (not shot.created_key and guest_name.strip()
                    and (shot.created_by or "") == _guest_identity(guest_name)))
        if not mine:
            raise HTTPException(status_code=403, detail="只能移除自己貼的截圖")
        image_url = shot.image_url or ""
        await session.delete(shot)
        await session.commit()
    _delete_shot_file(image_url)
    return {"status": "ok"}
