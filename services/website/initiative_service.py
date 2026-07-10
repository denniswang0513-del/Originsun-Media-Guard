"""services/website/initiative_service.py
---
公益合作 / 創作計畫 案例 CRUD + 公開列表（解析作品連動）。

連動作品：entry.project_id 自 1:N 起語意 = work id（crm_project_showcase.id；
既有資料 == 舊 project id 自動相容）。渲染時：
- 封面/標題/年份：entry 自填值優先，空則 fallback 到作品欄位
- 連結：連動作品 → /works/{slug}（slug = sc.slug 或 sc.number）；
        獨立案例 → entry.link_url
- 連動的作品若已刪除或未公開 → 公開列表跳過該案例（admin 列表仍顯示並標示）
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CrmProject, CrmProjectShowcase
from db.models_website import WebsiteInitiative
from . import _crud_base as _crud

logger = logging.getLogger(__name__)


def _to_dict(o: WebsiteInitiative) -> dict[str, Any]:
    return {
        "id": o.id,
        "line": o.line,
        "project_id": o.project_id,
        "title": o.title,
        "summary": o.summary,
        "cover_url": o.cover_url,
        "link_url": o.link_url,
        "year": o.year,
        "sort_order": o.sort_order,
        "visible": o.visible,
    }


def _work_slug(w) -> Optional[str]:
    return (w.slug or (str(w.number) if w.number else None))


def _work_cover(w) -> Optional[str]:
    """作品封面：自訂封面優先；沒有就用 YouTube 預設縮圖（hqdefault — 永遠存在，
    不像 maxresdefault 偶爾 404 會讓 Astro build 失敗）。"""
    if w.cover_url:
        return w.cover_url
    if w.youtube_id:
        return f"https://img.youtube.com/vi/{w.youtube_id}/hqdefault.jpg"
    return None


async def _load_works(session: AsyncSession, work_ids: list[str]) -> dict[str, Any]:
    """批次撈連動作品（去重、去空）— 回 work id → sc（掛 `_project_name` transient）。

    id 查無 showcase 時當舊 project id 用虛擬主作品兜（連動到「未進過 showcase
    流程的專案」時 admin 列表仍能顯示標題，行為與 1:1 時代一致）。"""
    from .project_service import _virtual_work_from_project
    ids = [p for p in {pid for pid in work_ids if pid}]
    if not ids:
        return {}
    out: dict[str, Any] = {}
    rows = await session.execute(
        select(CrmProjectShowcase, CrmProject.name)
        .outerjoin(CrmProject, CrmProject.id == CrmProjectShowcase.project_id)
        .where(CrmProjectShowcase.id.in_(ids))
    )
    for sc, pname in rows:
        sc._project_name = pname or ""
        out[sc.id] = sc
    missing = [i for i in ids if i not in out]
    if missing:
        projs = (await session.execute(
            select(CrmProject).where(CrmProject.id.in_(missing))
        )).scalars()
        for p in projs:
            vw = _virtual_work_from_project(p)
            vw._project_name = p.name or ""
            out[p.id] = vw
    return out


# ── CRUD（admin）──

async def create_initiative(session, data):
    return await _crud.create_item(session, WebsiteInitiative, data, _to_dict)


async def update_initiative(session, item_id, data):
    return await _crud.update_item(session, WebsiteInitiative, item_id, data, _to_dict)


async def delete_initiative(session, item_id):
    return await _crud.delete_item(session, WebsiteInitiative, item_id)


async def list_admin(session: AsyncSession) -> list[dict]:
    """admin 列表 — 全部案例 + 連動作品的標題/狀態（顯示用）。"""
    items = await _crud.list_items(session, WebsiteInitiative, _to_dict)
    works = await _load_works(session, [it.get("project_id") for it in items])
    for it in items:
        w = works.get(it.get("project_id")) if it.get("project_id") else None
        it["work_title"] = (w.title or w._project_name) if w else None
        it["work_cover"] = _work_cover(w) if w else None
        it["work_public"] = bool(w and w.published)
        it["work_missing"] = bool(it.get("project_id") and not w)
    return items


# ── 公開列表（解析作品連動 → 可直接渲染的卡片）──

async def list_public(session: AsyncSession, line: str) -> list[dict]:
    items = await _crud.list_items(session, WebsiteInitiative, _to_dict, visible_only=True)
    items = [it for it in items if it.get("line") == line]
    works = await _load_works(session, [it.get("project_id") for it in items])

    out: list[dict] = []
    for it in items:
        pid = it.get("project_id")
        w = works.get(pid) if pid else None
        if pid:
            # 連動作品但作品不存在 / 未公開 → 公開站不顯示（避免死連結）
            if not w or not w.published:
                continue
            cover = it.get("cover_url") or _work_cover(w)
            title = it.get("title") or w.title or w._project_name
            year = it.get("year") or w.year
            slug = _work_slug(w)
            link = f"/works/{slug}" if slug else (it.get("link_url") or None)
        else:
            cover = it.get("cover_url")
            title = it.get("title")
            year = it.get("year")
            link = it.get("link_url") or None
        if not title:
            continue   # 沒標題的案例不渲染
        out.append({
            "id": it["id"],
            "title": title,
            "summary": it.get("summary"),
            "cover_url": cover,
            "link_url": link,
            "year": year,
            "is_work": bool(w),
        })
    return out
