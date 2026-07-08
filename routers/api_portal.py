"""
api_portal.py — 看片審批客戶門戶 API（B1，docs/BIZ_PLAN.md B1 段）

修改往返從「LINE 來回猜」變成「時間軸精準標記」：內部建送審連結（專案+版本+
影片路徑）→ 產 token → 客戶用 /review.html?token= 免登入看片、點時間軸留言、
一鍵核准。內部端點守衛 = 管理員 OR portal / crm_projects 模組；公開端點只認
token（未過期），影片串流走 FileResponse（支援 Range），video_path 絕不外洩。
DB 兩表：portal_review_links / portal_comments（create_all 自建）。
"""

import os
import secrets
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request  # type: ignore
from fastapi.responses import FileResponse  # type: ignore

import core.state as state
from core.auth import check_admin_or_module
from core.schemas import PortalApprovePayload, PortalCommentPayload, PortalLinkPayload

router = APIRouter(prefix="/api/v1/portal", tags=["portal"])

_LINK_STATUSES = ("待審", "修改中", "已核准")
# 副檔名白名單 + 串流 media_type 對映（原生 <video> 可播的容器）
_VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".webm": "video/webm",
}
_COMMENT_MAX_LEN = 2000

# 簡單防灌水：同 token 每分鐘最多 N 則留言（記憶體計數，重啟歸零可接受）
_RATE_LIMIT_PER_MIN = 20
_rate_counter: dict = {}   # token -> [minute_bucket, count]


def _check_auth(request: Request) -> dict:
    return check_admin_or_module(request, "portal", "crm_projects")


def _require_factory():
    """DB 守門：離線一律 503（模式同 api_locations）。"""
    if not state.db_online:
        raise HTTPException(status_code=503, detail="資料庫離線")
    from db.session import get_session_factory
    factory = get_session_factory()
    if not factory:
        raise HTTPException(status_code=503, detail="資料庫離線")
    return factory


def _parse_expires(raw):
    """'YYYY-MM-DD' → 當日 23:59:59 UTC（當天仍可看）；空值回 None，格式錯 422。"""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        d = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=422, detail="expires_at 格式須為 YYYY-MM-DD")
    return d.replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)


def _is_expired(link) -> bool:
    exp = link.expires_at
    if exp is None:
        return False
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return exp < datetime.now(timezone.utc)


def _comment_dict(c) -> dict:
    return {
        "id": c.id,
        "timecode_sec": float(c.timecode_sec or 0),
        "body": c.body or "",
        "author_name": c.author_name or "",
        "resolved": int(c.resolved or 0),
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _link_dict(link, project_name: str = "", comments: list = None,
               total: int = 0, unresolved: int = 0) -> dict:
    """內部視圖用（含 token/video_path）——公開端點不用這個 serializer。"""
    return {
        "id": link.id,
        "project_id": link.project_id,
        "project_name": project_name or "",
        "version_label": link.version_label or "",
        "video_path": link.video_path or "",
        "token": link.token,
        "status": link.status or "待審",
        "approved_by": link.approved_by or "",
        "approved_at": link.approved_at.isoformat() if link.approved_at else None,
        "expires_at": link.expires_at.strftime("%Y-%m-%d") if link.expires_at else None,
        "created_by": link.created_by or "",
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "comment_total": total,
        "comment_unresolved": unresolved,
        "comments": comments or [],
    }


async def _get_link_by_token(session, token: str):
    """公開端點共用：token 查連結 — 不存在 404、已過期 410。"""
    from sqlalchemy import select
    from db.models import PortalReviewLink
    link = (await session.execute(
        select(PortalReviewLink).where(PortalReviewLink.token == token)
    )).scalars().first()
    if not link:
        raise HTTPException(status_code=404, detail="連結不存在或已被移除")
    if _is_expired(link):
        raise HTTPException(status_code=410, detail="連結已過期")
    return link


async def _load_comments(session, link_id: str) -> list:
    from sqlalchemy import select
    from db.models import PortalComment
    rows = (await session.execute(
        select(PortalComment).where(PortalComment.link_id == link_id)
        .order_by(PortalComment.timecode_sec, PortalComment.created_at)
    )).scalars().all()
    return [_comment_dict(c) for c in rows]


# ── 內部端點（審批門戶 Tab） ─────────────────────────────


@router.get("/links")
async def list_links(request: Request):
    """全部送審連結（join crm_projects.name）+ 各連結意見統計與意見列表。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import select, func as safunc
    from db.models import CrmProject, PortalComment, PortalReviewLink

    async with factory() as session:
        rows = (await session.execute(
            select(PortalReviewLink, CrmProject.name)
            .outerjoin(CrmProject, CrmProject.id == PortalReviewLink.project_id)
            .order_by(PortalReviewLink.created_at.desc())
        )).all()

        totals = dict((await session.execute(
            select(PortalComment.link_id, safunc.count(PortalComment.id))
            .group_by(PortalComment.link_id))).all())
        unresolved = dict((await session.execute(
            select(PortalComment.link_id, safunc.count(PortalComment.id))
            .where(PortalComment.resolved == 0)
            .group_by(PortalComment.link_id))).all())

        comments_by_link: dict = {}
        all_comments = (await session.execute(
            select(PortalComment)
            .order_by(PortalComment.timecode_sec, PortalComment.created_at)
        )).scalars().all()
        for c in all_comments:
            comments_by_link.setdefault(c.link_id, []).append(_comment_dict(c))

    return {"links": [
        _link_dict(link, pname or "", comments_by_link.get(link.id, []),
                   totals.get(link.id, 0), unresolved.get(link.id, 0))
        for link, pname in rows
    ]}


@router.post("/links")
async def create_link(req: PortalLinkPayload, request: Request):
    """建立送審連結：驗 video_path 檔案存在 + 副檔名白名單 → 產 token。"""
    payload = _check_auth(request)
    project_id = (req.project_id or "").strip()
    if not project_id:
        raise HTTPException(status_code=422, detail="project_id 必填")
    video_path = (req.video_path or "").strip().strip('"')
    if not video_path:
        raise HTTPException(status_code=422, detail="video_path 必填")
    ext = os.path.splitext(video_path)[1].lower()
    if ext not in _VIDEO_MEDIA_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"不支援的影片格式：{ext or '(無副檔名)'}（僅 .mp4 / .mov / .m4v / .webm）")
    if not os.path.isfile(video_path):
        raise HTTPException(status_code=422, detail="影片檔案不存在：" + video_path)
    expires_at = _parse_expires(req.expires_at)
    factory = _require_factory()

    from db.models import PortalReviewLink

    link = PortalReviewLink(
        id=uuid.uuid4().hex,
        project_id=project_id,
        version_label=(req.version_label or "").strip() or None,
        video_path=video_path,
        token=secrets.token_urlsafe(32),
        status="待審",
        expires_at=expires_at,
        created_by=payload.get("username") or "",
    )
    async with factory() as session:
        session.add(link)
        await session.commit()
        await session.refresh(link)
    return {"status": "ok", "link": _link_dict(link)}


@router.put("/links/{lid}")
async def update_link(lid: str, req: PortalLinkPayload, request: Request):
    """更新送審連結：status / version_label / expires_at（部分更新）。"""
    _check_auth(request)
    factory = _require_factory()

    data = req.model_dump(exclude_unset=True)
    if "status" in data and data["status"] not in _LINK_STATUSES:
        raise HTTPException(status_code=422,
                            detail="status 須為：" + " / ".join(_LINK_STATUSES))

    from db.models import PortalReviewLink

    async with factory() as session:
        link = await session.get(PortalReviewLink, lid)
        if not link:
            raise HTTPException(status_code=404, detail="找不到此送審連結")
        if "status" in data:
            link.status = data["status"]
            if data["status"] != "已核准":   # 退回待審/修改中時清掉核准紀錄
                link.approved_by = None
                link.approved_at = None
        if "version_label" in data:
            link.version_label = (data["version_label"] or "").strip() or None
        if "expires_at" in data:
            link.expires_at = _parse_expires(data["expires_at"])
        await session.commit()
        await session.refresh(link)
    return {"status": "ok", "link": _link_dict(link)}


@router.delete("/links/{lid}")
async def delete_link(lid: str, request: Request):
    """刪送審連結：連帶刪 comments。"""
    _check_auth(request)
    factory = _require_factory()

    from sqlalchemy import delete as sa_delete
    from db.models import PortalComment, PortalReviewLink

    async with factory() as session:
        link = await session.get(PortalReviewLink, lid)
        if not link:
            raise HTTPException(status_code=404, detail="找不到此送審連結")
        await session.execute(sa_delete(PortalComment)
                              .where(PortalComment.link_id == lid))
        await session.delete(link)
        await session.commit()
    return {"status": "ok"}


@router.put("/comments/{cid}/resolve")
async def toggle_comment_resolved(cid: str, request: Request):
    """toggle 意見的 resolved 狀態。"""
    _check_auth(request)
    factory = _require_factory()

    from db.models import PortalComment

    async with factory() as session:
        c = await session.get(PortalComment, cid)
        if not c:
            raise HTTPException(status_code=404, detail="找不到此意見")
        c.resolved = 0 if int(c.resolved or 0) else 1
        await session.commit()
        resolved = int(c.resolved)
    return {"status": "ok", "resolved": resolved}


# ── 公開端點（客戶頁 /review.html，只認 token） ──────────


@router.get("/public/{token}")
async def public_get_link(token: str):
    """連結資訊 + 意見列表（按時間碼排序）— 絕不回 video_path。"""
    factory = _require_factory()

    from sqlalchemy import select
    from db.models import CrmProject

    async with factory() as session:
        link = await _get_link_by_token(session, token)
        project_name = (await session.execute(
            select(CrmProject.name).where(CrmProject.id == link.project_id)
        )).scalar() or ""
        comments = await _load_comments(session, link.id)

    return {
        "project_name": project_name,
        "version_label": link.version_label or "",
        "status": link.status or "待審",
        "approved_by": link.approved_by or "",
        "approved_at": link.approved_at.isoformat() if link.approved_at else None,
        "comments": comments,
    }


@router.get("/public/{token}/stream")
async def public_stream_video(token: str):
    """影片串流 — starlette FileResponse（本版支援 Range，seek 可用）。"""
    factory = _require_factory()
    async with factory() as session:
        link = await _get_link_by_token(session, token)
        video_path = link.video_path or ""

    if not video_path or not os.path.isfile(video_path):
        raise HTTPException(status_code=404, detail="影片檔案不存在（可能已被移動）")
    media_type = _VIDEO_MEDIA_TYPES.get(
        os.path.splitext(video_path)[1].lower(), "application/octet-stream")
    return FileResponse(video_path, media_type=media_type)


@router.post("/public/{token}/comments")
async def public_add_comment(token: str, req: PortalCommentPayload):
    """客戶留言：body 必填（≤2000 字）；同 token 每分鐘最多 20 則防灌水。"""
    body = (req.body or "").strip()
    if not body:
        raise HTTPException(status_code=422, detail="留言內容必填")
    if len(body) > _COMMENT_MAX_LEN:
        raise HTTPException(status_code=422,
                            detail=f"留言長度上限 {_COMMENT_MAX_LEN} 字")

    minute = int(time.time() // 60)
    bucket = _rate_counter.get(token)
    if not bucket or bucket[0] != minute:
        bucket = [minute, 0]
        _rate_counter[token] = bucket
    if bucket[1] >= _RATE_LIMIT_PER_MIN:
        raise HTTPException(status_code=429, detail="留言太頻繁，請稍後再試")
    bucket[1] += 1

    factory = _require_factory()

    from db.models import PortalComment

    async with factory() as session:
        link = await _get_link_by_token(session, token)
        c = PortalComment(
            id=uuid.uuid4().hex,
            link_id=link.id,
            timecode_sec=max(float(req.timecode_sec or 0), 0.0),
            body=body,
            author_name=(req.author_name or "").strip()[:64] or None,
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
    return {"status": "ok", "comment": _comment_dict(c)}


@router.post("/public/{token}/approve")
async def public_approve(token: str, req: PortalApprovePayload):
    """一鍵核准此版本：留名必填；已核准再按回 409。"""
    author = (req.author_name or "").strip()[:64]
    if not author:
        raise HTTPException(status_code=422, detail="請留下您的大名")
    factory = _require_factory()

    async with factory() as session:
        link = await _get_link_by_token(session, token)
        if link.status == "已核准":
            raise HTTPException(status_code=409, detail="此版本已核准過")
        link.status = "已核准"
        link.approved_by = author
        link.approved_at = datetime.now(timezone.utc)
        await session.commit()
        approved_at = link.approved_at.isoformat()
    return {"status": "ok", "approved_by": author, "approved_at": approved_at}
