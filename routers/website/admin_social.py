"""routers/website/admin_social.py
---
管理端：社群編輯自動化（Phase N-soc 階段一）—— 文稿佇列審核 + 設定 + 立即執行。

對應前端「📣 社群工作台」子視圖：
- 佇列：GET /social/posts（status 篩選）→ 審核卡改稿（PUT）/ 核准 / 退回
- 發佈回寫：POST /social/posts/{id}/published（階段一先給「一鍵複製後手動貼」用；
  階段二 n8n 發佈器自動回呼）
- 設定：GET/PUT /social/settings（social.* 鍵包裝，白名單見 social_runner）
- 立即執行：POST /social/run（非同步背景跑，同 seo runner 模式）

社群文稿不影響 Astro build → 全部端點不觸發 rebuild_service.mark_dirty()。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ._common import admin_session, current_username
from core.schemas_website import SocialPostPublishedBody, SocialPostUpdate
from services.website import social_runner

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-social"])

_VALID_STATUSES = {"draft", "approved", "published", "rejected"}


# ══════════════════════════════════════════════════════════
# 文稿佇列
# ══════════════════════════════════════════════════════════

@router.get("/social/posts")
async def social_posts_list(
    request: Request, session: AsyncSession = Depends(admin_session),
):
    """文稿佇列（新→舊）。?status=draft|approved|published|rejected 篩選可選。"""
    status = (request.query_params.get("status") or "").strip() or None
    if status and status not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status 只接受 {sorted(_VALID_STATUSES)}")
    return {"items": await social_runner.list_queue(session, status=status)}


@router.put("/social/posts/{post_id}")
async def social_post_update(
    post_id: str, req: SocialPostUpdate,
    session: AsyncSession = Depends(admin_session),
):
    """編輯改稿：content / media_url / scheduled_at。"""
    item = await social_runner.update_queue_post(
        session, post_id, req.model_dump(exclude_unset=True),
    )
    if not item:
        raise HTTPException(status_code=404, detail="文稿不存在")
    return item


@router.post("/social/posts/{post_id}/approve")
async def social_post_approve(
    post_id: str, request: Request,
    session: AsyncSession = Depends(admin_session),
):
    """核准 → status=approved（階段二發佈器只吃 approved 的）。"""
    item = await social_runner.set_queue_status(
        session, post_id, "approved", reviewed_by=current_username(request),
    )
    if not item:
        raise HTTPException(status_code=404, detail="文稿不存在")
    return item


@router.post("/social/posts/{post_id}/reject")
async def social_post_reject(
    post_id: str, request: Request,
    session: AsyncSession = Depends(admin_session),
):
    """退回 → status=rejected（不佔頻率上限額度、不再進發佈排程）。"""
    item = await social_runner.set_queue_status(
        session, post_id, "rejected", reviewed_by=current_username(request),
    )
    if not item:
        raise HTTPException(status_code=404, detail="文稿不存在")
    return item


@router.post("/social/posts/{post_id}/published")
async def social_post_published(
    post_id: str, req: SocialPostPublishedBody,
    session: AsyncSession = Depends(admin_session),
):
    """發佈完成回寫實際貼文連結 → status=published。"""
    item = await social_runner.set_queue_status(
        session, post_id, "published", published_url=req.published_url,
    )
    if not item:
        raise HTTPException(status_code=404, detail="文稿不存在")
    return item


# ══════════════════════════════════════════════════════════
# 設定 + 立即執行
# ══════════════════════════════════════════════════════════

@router.get("/social/settings")
async def social_get_settings(session: AsyncSession = Depends(admin_session)):
    """讀 social.* 設定 + runner 狀態（last_run_at / last_run_summary / running）。"""
    return await social_runner.get_social_settings(session)


@router.put("/social/settings")
async def social_update_settings(
    payload: dict, request: Request,
    session: AsyncSession = Depends(admin_session),
):
    """更新設定（白名單鍵：enabled/platforms/slots/caps/brand_voice/tpl_*/cron 等）。"""
    try:
        return await social_runner.update_social_settings(
            session, payload, by=current_username(request),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/social/run")
async def social_run_now(session: AsyncSession = Depends(admin_session)):
    """立即跑一輪 pipeline — 非同步背景跑（立即回 {started}/{busy}），前端輪詢
    /social/settings 的 running/last_run_at。NAS 端 forward 給 master 跑 claude。"""
    return await social_runner.trigger_run(session)


# ── Internal forward endpoint（NAS website-api → master，跑 claude）──────
# claude.exe 只在 master Windows 上能跑；NAS 容器收到 admin 請求後 relay 到這裡。
# X-Internal-Key 認證 + internal DB session 共用 _common（同 admin_seo）。
from ._common import _check_internal_key


@router.post("/internal/social/run")
async def social_run_internal(request: Request):
    """NAS website-api 把「立即執行」轉給 master。master 背景跑、立即回。"""
    _check_internal_key(request)
    if not social_runner.start_run_bg():
        return {"status": "busy"}
    return {"status": "started"}
