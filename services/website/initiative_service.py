"""services/website/initiative_service.py
---
公益合作 / 創作計畫 案例 CRUD + 公開列表（解析作品連動）。

連動作品：entry.project_id 指向 crm_projects.id。渲染時：
- 封面/標題/年份：entry 自填值優先，空則 fallback 到作品的 public_* 欄位
- 連結：連動作品 → /works/{slug}（slug = public_slug 或 public_number）；
        獨立案例 → entry.link_url
- 連動的作品若已刪除或未公開 → 公開列表跳過該案例（admin 列表仍顯示並標示）
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CrmProject
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


def _work_slug(w: CrmProject) -> Optional[str]:
    return (w.public_slug or (str(w.public_number) if w.public_number else None))


def _work_cover(w: CrmProject) -> Optional[str]:
    """作品封面：自訂封面優先；沒有就用 YouTube 預設縮圖（hqdefault — 永遠存在，
    不像 maxresdefault 偶爾 404 會讓 Astro build 失敗）。"""
    if w.public_cover_url:
        return w.public_cover_url
    if w.public_youtube_id:
        return f"https://img.youtube.com/vi/{w.public_youtube_id}/hqdefault.jpg"
    return None


async def _load_works(session: AsyncSession, project_ids: list[str]) -> dict[str, CrmProject]:
    """批次撈連動作品（去重、去空）。"""
    ids = [p for p in {pid for pid in project_ids if pid}]
    if not ids:
        return {}
    rows = (await session.execute(
        select(CrmProject).where(CrmProject.id.in_(ids))
    )).scalars()
    return {w.id: w for w in rows}


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
        it["work_title"] = (w.public_title or w.name) if w else None
        it["work_cover"] = _work_cover(w) if w else None
        it["work_public"] = bool(w and w.public)
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
            if not w or not w.public:
                continue
            cover = it.get("cover_url") or _work_cover(w)
            title = it.get("title") or w.public_title or w.name
            year = it.get("year") or w.public_year
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
