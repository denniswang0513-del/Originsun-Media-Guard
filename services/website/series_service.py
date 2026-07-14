"""services/website/series_service.py — 作品系列（跨專案策展集合）

系列實體 CRUD（register_crud 泛型簽名；比照 category_service 手寫 —— 要注入
work_count / async slug 檢查 / 成員解除，超出 _crud_base 泛型範圍）+ 成員全量替換
+ 公開端（系列清單 / 系列頁 / 轉址表）。

成員關係在 crm_project_showcase.series_id / series_order（soft FK → website_series.id）；
一支作品最多屬一個系列。刪系列 = 解除成員歸屬（作品本身不動）。
slug 唯一性此層先擋（友善 409），DB UNIQUE 是後盾；slug 是 URL 永久承諾 ——
改名時自動記 old_slugs → 併入 /api/website/redirects（與作品/文章同一套 301 機制）。
作品 dict 序列化 / 成員排序 / 封面 fallback 沿用 project_service 單一真相源。
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CrmProjectShowcase
from db.models_website import WebsiteSeries


def _to_dict(s: WebsiteSeries, work_count: int = 0) -> dict:
    """admin 形狀（含 visible/sort_order/cover_image 原值）。公開端用 _to_public_item。"""
    return {
        "id": s.id, "slug": s.slug,
        "title_zh": s.title_zh, "title_en": s.title_en,
        "description_zh": s.description_zh, "description_en": s.description_en,
        "cover_image": s.cover_image,
        "sort_order": s.sort_order or 0, "visible": bool(s.visible),
        "work_count": work_count,
    }


def _to_public_item(s: WebsiteSeries, first_sc, work_count: int) -> dict:
    """公開端形狀：只給前端要用的欄位（cover_url 已含 fallback 鏈）。"""
    from services.website.project_service import work_cover
    return {
        "id": s.id, "slug": s.slug,
        "title_zh": s.title_zh, "title_en": s.title_en,
        "description_zh": s.description_zh, "description_en": s.description_en,
        "cover_url": s.cover_image or (first_sc is not None and work_cover(first_sc)) or None,
        "work_count": work_count,
    }


async def _work_count(session: AsyncSession, series_id: int) -> int:
    return (await session.execute(
        select(func.count()).select_from(CrmProjectShowcase)
        .where(CrmProjectShowcase.series_id == series_id))).scalar() or 0


async def _assert_slug_free(session: AsyncSession, slug: str,
                            exclude_id: int | None = None) -> None:
    q = select(WebsiteSeries.id).where(WebsiteSeries.slug == slug)
    if exclude_id is not None:
        q = q.where(WebsiteSeries.id != exclude_id)
    if (await session.execute(q)).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"slug「{slug}」已被其他系列使用")


# ── admin CRUD（register_crud 泛型簽名）─────────────────────

async def list_series(session: AsyncSession) -> list[dict]:
    """admin 列表：全部（含 hidden）+ 成員數（批次 group by）。"""
    rows = (await session.execute(
        select(WebsiteSeries)
        .order_by(WebsiteSeries.sort_order, WebsiteSeries.id))).scalars().all()
    counts = dict((await session.execute(
        select(CrmProjectShowcase.series_id, func.count(CrmProjectShowcase.id))
        .where(CrmProjectShowcase.series_id.isnot(None))
        .group_by(CrmProjectShowcase.series_id))).all())
    return [_to_dict(s, counts.get(s.id, 0)) for s in rows]


async def create_series(session: AsyncSession, data: dict) -> dict:
    await _assert_slug_free(session, data["slug"])
    s = WebsiteSeries(**data)
    session.add(s)
    await session.commit()
    await session.refresh(s)
    return _to_dict(s)


async def update_series(session: AsyncSession, item_id: int, data: dict):
    s = await session.get(WebsiteSeries, item_id)
    if not s:
        return None
    if data.get("slug") and data["slug"] != s.slug:
        await _assert_slug_free(session, data["slug"], exclude_id=item_id)
        # slug 改名 → 記舊值進 old_slugs（與作品同一套 301 機制，見 list_series_redirects）
        from services.website.project_service import _append_old_slug_core
        appended = _append_old_slug_core(s.slug, data["slug"], s.old_slugs)
        if appended is not None:
            s.old_slugs = appended
    for k, v in data.items():
        setattr(s, k, v)
    await session.commit()
    return _to_dict(s, await _work_count(session, item_id))


async def delete_series(session: AsyncSession, item_id: int) -> bool:
    s = await session.get(WebsiteSeries, item_id)
    if not s:
        return False
    await session.execute(  # 解除成員歸屬 — 作品本身不動
        update(CrmProjectShowcase)
        .where(CrmProjectShowcase.series_id == item_id)
        .values(series_id=None, series_order=0))
    await session.delete(s)
    await session.commit()
    return True


async def set_series_members(session: AsyncSession, series_id: int,
                             work_ids: list[str]) -> dict:
    """成員全量替換：work_ids 依序寫 series_order；原成員不在清單 → 解除歸屬。
    admin 卡的排序/加入/移除都走這一支（一個端點一種語意）。"""
    if not await session.get(WebsiteSeries, series_id):
        raise HTTPException(status_code=404, detail="系列不存在")
    clear_q = (update(CrmProjectShowcase)
               .where(CrmProjectShowcase.series_id == series_id))
    if work_ids:
        clear_q = clear_q.where(CrmProjectShowcase.id.notin_(work_ids))
    await session.execute(clear_q.values(series_id=None, series_order=0))
    for i, wid in enumerate(work_ids):
        await session.execute(
            update(CrmProjectShowcase)
            .where(CrmProjectShowcase.id == wid)
            .values(series_id=series_id, series_order=i))
    await session.commit()
    return {"ok": True, "work_count": await _work_count(session, series_id)}


# ── 公開端 ──────────────────────────────────────────────────

async def list_public_series(session: AsyncSession) -> list[dict]:
    """公開系列清單：visible 且至少 1 支已發布成員（0 成員對外不出現）。
    給作品牆摺疊 + getStaticPaths。"""
    from services.website.project_service import (_published_works_base,
                                                  series_member_order)
    rows = (await session.execute(
        select(WebsiteSeries).where(WebsiteSeries.visible.is_(True))
        .order_by(WebsiteSeries.sort_order, WebsiteSeries.id))).scalars().all()
    if not rows:
        return []
    members = (await session.execute(
        _published_works_base()
        .where(CrmProjectShowcase.series_id.in_([s.id for s in rows]))
        .order_by(*series_member_order()))).all()
    by_series: dict[int, list] = {}
    for sc, _p in members:
        by_series.setdefault(sc.series_id, []).append(sc)
    return [_to_public_item(s, mem[0], len(mem))
            for s in rows if (mem := by_series.get(s.id))]


async def get_public_series_by_slug(session: AsyncSession, slug: str):
    """系列頁資料：系列欄位 + works（list-shape，依 series_member_order）。
    不存在/隱藏 → None。"""
    from services.website import project_service as ps
    s = (await session.execute(
        select(WebsiteSeries).where(WebsiteSeries.slug == slug,
                                    WebsiteSeries.visible.is_(True)))).scalar_one_or_none()
    if not s:
        return None
    rows = (await session.execute(
        ps._published_works_base()
        .where(CrmProjectShowcase.series_id == s.id)
        .order_by(*ps.series_member_order()))).all()
    cat_map = await ps._categories_for_projects(session, [sc.id for sc, _ in rows])
    client_map = await ps._clients_for_projects(session, [p.id for _, p in rows])
    works = [ps._to_public_dict(sc, p, cat_map.get(sc.id), client_map.get(p.id))
             for sc, p in rows]
    d = _to_public_item(s, rows[0][0] if rows else None, len(works))
    d["works"] = works
    return d


async def list_series_redirects(session: AsyncSession) -> dict[str, str]:
    """系列 slug 改名轉址表：/works/series/{old} → /works/series/{new}。
    只算 visible 系列（隱藏 = 頁面不存在，redirect 到 404 沒意義）。"""
    rows = (await session.execute(
        select(WebsiteSeries).where(
            WebsiteSeries.visible.is_(True),
            func.jsonb_array_length(WebsiteSeries.old_slugs) > 0))).scalars().all()
    out: dict[str, str] = {}
    for s in rows:
        for old in (s.old_slugs or []):
            if isinstance(old, str) and old and old != s.slug:
                out[f"/works/series/{old}"] = f"/works/series/{s.slug}"
    return out
