"""services/website/project_service.py
---
作品查詢、排序、公開切換邏輯。

1:N 改造（2026-07）：「作品」= crm_project_showcase 一列（id=work id、project_id=所屬
專案；既有列 id == project_id 即「主作品」）。讀路徑以 showcase 為主體 join CrmProject
（client 顯示名 / name fallback）；寫路徑寫 sc.*，主作品同時 dual-write 回
crm_projects.public_*（過渡期回退保險，Phase 4 停寫）。

website_project_categories.project_id 自 1:N 起語意 = work id（既有資料兩者相等，
零遷移自動相容）。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from types import SimpleNamespace
from typing import Optional

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

# is_main_work / work_url_slug / WORK_WIRE_FIELD_MAP 正本在 core.crm_logic（純函式、
# OTA 安全，routers/crm 也用同一份）— 這裡 re-export 供既有 caller 沿用
from core.crm_logic import (  # noqa: F401
    SHOWCASE_EDIT_EXPIRES_DAYS, SHOWCASE_EDIT_SCOPE, WORK_WIRE_FIELD_MAP,
    is_main_work, showcase_edit_url, work_url_slug,
)
from db.models import Client, CrmProject, CrmProjectShowcase
from db.models_website import WebsiteCategory, WebsiteProjectCategory

logger = logging.getLogger(__name__)


def _youtube_thumbnail(video_id: Optional[str]) -> Optional[str]:
    return f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg" if video_id else None


async def _categories_for_projects(
    session: AsyncSession, project_ids: list[str]
) -> dict[str, dict[str, list[str]]]:
    """Batch fetch：一次 JOIN 取所有專案的分類/標籤 slug，依 kind 拆兩個 list。

    回傳 {project_id: {'categories': [slug...], 'tags': [slug...]}}。
    kind 為 NULL 或 'category' 視為分類；'tag' 視為標籤。
    """
    if not project_ids:
        return {}
    stmt = (
        select(WebsiteProjectCategory.project_id, WebsiteCategory.slug, WebsiteCategory.kind)
        .join(WebsiteCategory, WebsiteCategory.id == WebsiteProjectCategory.category_id)
        .where(WebsiteProjectCategory.project_id.in_(project_ids))
    )
    out: dict[str, dict[str, list[str]]] = defaultdict(lambda: {"categories": [], "tags": []})
    for pid, slug, kind in await session.execute(stmt):
        bucket = "tags" if kind == "tag" else "categories"
        out[pid][bucket].append(slug)
    return out


def _empty_cat_data() -> dict[str, list[str]]:
    return {"categories": [], "tags": []}


async def _clients_for_projects(
    session: AsyncSession, project_ids: list[str]
) -> dict[str, str]:
    """Batch fetch:project_id → client display name(short_name 優先、空值 fallback full_name)。

    給 _to_public_dict 用,讓 client 顯示 fallback chain 是
    `public_client || client.short_name || client.full_name || ""`,
    這樣 CRM 改 client_id 立刻反映到作品集 Tab 跟對外網站,不需 PM 重存 showcase。
    """
    if not project_ids:
        return {}
    stmt = (
        select(CrmProject.id, Client.short_name, Client.full_name)
        .join(Client, Client.id == CrmProject.client_id)
        .where(CrmProject.id.in_(project_ids))
    )
    out: dict[str, str] = {}
    for pid, short, full in await session.execute(stmt):
        out[pid] = (short or "").strip() or (full or "").strip()
    return out


def _gallery_first_url(gallery) -> Optional[str]:
    """gallery[0].url（成果展示第一張圖）— 首頁輪播精選圖的 fallback。"""
    if isinstance(gallery, list) and gallery and isinstance(gallery[0], dict):
        url = (gallery[0].get("url") or "").strip()
        if url:
            return url
    return None


def _virtual_work_from_project(p: CrmProject) -> SimpleNamespace:
    """從 crm_projects.public_* 合成一個「虛擬主作品」— 給還沒有 showcase row 的專案
    （admin 作品列表列出全部專案，未進過結案/showcase 流程者無 sc row）。

    欄位值 = 舊 1:1 時代的讀取來源，維持 admin 列表輸出與改造前逐位等價。
    寫入路徑（update_project_public）遇到無 row 會 get-or-create 真列，不用這個。
    """
    return SimpleNamespace(
        id=p.id, project_id=p.id,
        slug=p.public_slug, number=p.public_number,
        title=p.public_title, title_en=p.public_title_en,
        description=p.public_description, description_en=p.public_description_en,
        youtube_id=p.public_youtube_id, extra_videos=None,
        year=p.public_year, featured=bool(p.public_featured),
        featured_image=p.public_featured_image, noindex=bool(p.public_noindex),
        old_slugs=p.public_old_slugs or [], sort_order=p.public_sort_order or 0,
        published=bool(p.public), published_at=p.public_published_at,
        credits=p.public_credits, credits_mode=p.public_credits_mode,
        credits_text=p.public_credits_text,
        gallery=None, cover_url=p.public_cover_url, video_url=None,
        prod_stage=p.website_prod_stage, verified_at=p.website_verified_at,
    )


def _slug_or_fallback(sc) -> str:
    """work_url_slug 的 'work' 兜底版 — URL 不可為空的 caller（redirects/驗收）用。

    number 在第一次 publish 時 auto-assign，永久不變避免 republish 改編號破壞
    SEO 連結。fresh publish 還沒分配 number → 'work' 兜底（理論上不該發生，
    publish 路徑都會配號）。"""
    return work_url_slug(sc) or "work"


def _old_slug_to_url(s: str) -> str:
    """把 public_old_slugs 一筆轉成完整 URL path。

    語意：純字串（如 'miriam-cahn'）→ '/works/miriam-cahn'（向下相容）
         '/' 開頭（如 '/portfolio/abc'）→ 原樣保留（網站重建從外部 CMS 遷入用）

    跨網域 URL（含 http://）一律當外部、本系統不接，回空字串讓 caller 過濾。
    （理論上 normalize_old_slug_input 會在 save 時 strip 掉，但保留防呆。）
    """
    if not s:
        return ""
    s = s.strip()
    if not s:
        return ""
    if s.startswith("http://") or s.startswith("https://"):
        return ""  # cross-domain — 應該已被 normalize 移除網域；走到這代表 normalize 沒跑
    if s.startswith("/"):
        return s   # full path — 已是完整本網域路徑
    return f"/works/{s}"  # bare slug — 舊行為


def normalize_old_slug_input(raw: str) -> Optional[str]:
    """把 PM 從各種來源貼進來的舊網址正規化成內部儲存格式。

    支援輸入：
      - 完整 URL（含網域）：`https://www.originsun-studio.com/portfolio/abc/` → `/portfolio/abc`
      - 相對路徑：`/portfolio/abc/` → `/portfolio/abc`
      - 純字串 slug：`old-name` → `old-name`（讀取時 _old_slug_to_url 會展為 /works/）

    跟 post_service._normalize_old_url 行為一致（strip 網域 + trim trailing /）。
    控制字元 / 引號 / 空白 → return None 讓 caller 過濾。
    """
    s = (raw or "").strip()
    if not s:
        return None
    # 完整 URL → 取 path（不論網域是不是本站，PM 貼啥都 strip）
    if s.startswith("http://") or s.startswith("https://"):
        from urllib.parse import urlparse
        try:
            s = urlparse(s).path or ""
        except Exception:
            return None
        if not s:
            return None
    # 結尾 / 去掉（避免 /portfolio/abc 跟 /portfolio/abc/ 視為兩條）
    if len(s) > 1 and s.endswith("/"):
        s = s.rstrip("/")
    # 控制字元 / 引號 / 空白拒收
    import re
    if re.search(r"[\s\"'\x00-\x1f]", s):
        return None
    return s


def normalize_old_slugs_input(raw_list) -> list[str]:
    """批次正規化 + 去重（保序）。"""
    if not raw_list:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_list:
        if not isinstance(raw, str):
            continue
        n = normalize_old_slug_input(raw)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _credits_summary(credits: Optional[list]) -> str:
    """從 block 結構 credits 抽前 2 個 entry 組「主演 邱雲福 · 導演 王小明」摘要。

    限制 ~30 字（給 list card 一行顯示），超過截斷 + …。空或非 block 結構回 ""。
    """
    if not isinstance(credits, list) or not credits:
        return ""
    parts: list[str] = []
    for block in credits:
        if not isinstance(block, dict):
            continue
        for entry in (block.get("entries") or []):
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            label = block.get("name_zh") or ""
            if entry.get("duty"):
                label = f"{label} {entry['duty']}".strip() if label else entry["duty"]
            parts.append(f"{label} {entry['name']}".strip() if label else entry["name"])
            if len(parts) >= 2:
                break
        if len(parts) >= 2:
            break
    summary = " · ".join(parts)
    return summary if len(summary) <= 30 else summary[:29] + "…"


def _to_public_dict(
    sc,
    p: CrmProject,
    cat_data: Optional[dict[str, list[str]]] = None,
    client_name: Optional[str] = None,
    with_carousel_fallback: bool = False,
) -> dict:
    """投影作品欄位到 dict — caller 必須保證 sc 是 published（list/get caller 都在 SQL 層
    `WHERE published.is_(True)` 過濾過）。本函式不檢查 published flag，避免 admin path
    （_to_admin_dict）誤拒未公開作品。新增 caller 時請 SQL 層先過濾。

    sc: CrmProjectShowcase（作品，或 _virtual_work_from_project 同形物件）。
    p: 所屬 CrmProject（name fallback / public_client 覆寫 / client_en）。
    cat_data: {"categories": [...], "tags": [...]} — _categories_for_projects 的單筆（key=work id）。
    client_name: _clients_for_projects 解析的 client 顯示名(short_name fallback full_name)。
        Source of truth 是 CRM 的 client_id;public_client 純 SEO 覆寫,空值就 fallback。
        title 同理:sc.title 空就 fallback 到專案 name。
    with_carousel_fallback: carousel_image 是否啟用 gallery 第一張 fallback —
        只有 featured/hero 清單 caller 開（此欄本就是輪播專用），其餘 caller 維持
        「只看精選圖」的舊行為。
    """
    cd = cat_data or _empty_cat_data()
    return {
        "slug": _slug_or_fallback(sc),
        "title": (sc.title or "").strip() or p.name,
        "client": (p.public_client or "").strip() or (client_name or ""),
        "youtube_id": sc.youtube_id,
        "description": sc.description,
        "year": sc.year,
        "categories": cd.get("categories") or [],
        "tags": cd.get("tags") or [],
        "credits_mode": sc.credits_mode or "block",
        "credits_text": sc.credits_text,
        "thumbnail_url": _youtube_thumbnail(sc.youtube_id),
        # OG image fallback chain：work cover > YouTube thumb > BaseLayout meta.seo_og_image
        "cover_url": sc.cover_url or _youtube_thumbnail(sc.youtube_id),
        # 首頁輪播取圖：精選圖 → 成果展示第一張（→ 前端再接 YouTube 縮圖，見 HomeSlideshow）
        "carousel_image": sc.featured_image
                          or (_gallery_first_url(sc.gallery) if with_carousel_fallback else None),
        "featured": bool(sc.featured),
        "noindex": bool(sc.noindex),
        # SEO 301 來源舊 URL — Astro JSON-LD sameAs / markdown 「曾用 URL」用
        # 支援兩種寫法：純 slug（自動 prepend /works/）+ '/' 開頭完整路徑（外部 CMS 遷入）
        "old_urls": [u for u in (_old_slug_to_url(s) for s in (sc.old_slugs or [])) if u],
        # 列表卡片摘要 — 給 WorkCard 一行顯示用
        "credits_summary": _credits_summary(sc.credits),
        # 英文版（transcreation；空則前端 fallback 中文。client_en 為手動指定）
        "title_en": sc.title_en,
        "description_en": sc.description_en,
        "client_en": p.public_client_en,
        # sitemap <lastmod> 用。目前沒有 updated 欄位入列，退而用發布時間 —
        # 它是誠實的下界（永遠不會謊報比實際更新），Google 對不實 lastmod 會整個忽略。
        "date_modified": sc.published_at.isoformat() if sc.published_at else None,
    }


def _to_admin_dict(
    sc,
    p: CrmProject,
    cat_data: Optional[dict[str, list[str]]] = None,
    client_name: Optional[str] = None,
) -> dict:
    d = _to_public_dict(sc, p, cat_data, client_name)
    d.update({
        "id": sc.id,             # work id（主作品 == project id，向下相容）
        "project_id": sc.project_id or p.id,
        "name": p.name,
        "public": bool(sc.published),
        "public_sort_order": sc.sort_order or 0,
        "credits": sc.credits or [],
        "published_at": sc.published_at.isoformat() if sc.published_at else None,
        "redirect_count": len(sc.old_slugs or []),
    })
    return d


def _published_works_base():
    """published 作品 join 所屬專案的共用查詢基底 — rows 是 (sc, p) tuple。

    inner join：project 被刪的殘 showcase 列不出現（= 舊 1:1 時代行為，作品即專案）。
    """
    return (
        select(CrmProjectShowcase, CrmProject)
        .join(CrmProject, CrmProject.id == CrmProjectShowcase.project_id)
        .where(CrmProjectShowcase.published.is_(True))
    )


async def list_public_projects(
    session: AsyncSession,
    category_slug: Optional[str] = None,
    page: int = 1,
    limit: int = 12,
) -> tuple[list[dict], int]:
    """列出對外公開的作品（分頁 + 分類篩選）。"""
    base = _published_works_base()

    if category_slug:
        cat_subq = select(WebsiteCategory.id).where(WebsiteCategory.slug == category_slug).scalar_subquery()
        base = base.join(
            WebsiteProjectCategory, WebsiteProjectCategory.project_id == CrmProjectShowcase.id
        ).where(WebsiteProjectCategory.category_id == cat_subq)

    total = (await session.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0

    # 排序原則（依序）：1 精選作品先 → 2 年份新→舊 → 3 發布日新→舊。
    # sort_order 當最後平手 tiebreaker（保留手動微調、且排序穩定）。
    stmt = base.order_by(
        CrmProjectShowcase.featured.desc().nullslast(),
        CrmProjectShowcase.year.desc().nullslast(),
        CrmProjectShowcase.published_at.desc().nullslast(),
        CrmProjectShowcase.sort_order.desc().nullslast(),
    ).offset((page - 1) * limit).limit(limit)

    rows = list(await session.execute(stmt))
    wids = [sc.id for sc, _ in rows]
    pids = [p.id for _, p in rows]
    cat_map = await _categories_for_projects(session, wids)
    client_map = await _clients_for_projects(session, pids)
    return [
        _to_public_dict(sc, p, cat_map.get(sc.id), client_map.get(p.id))
        for sc, p in rows
    ], total


async def get_public_project_by_slug(session: AsyncSession, slug: str) -> Optional[dict]:
    """單一公開作品詳情。slug 解析順序：
       1. slug（admin 自訂語意 slug）
       2. number（純數字 slug，第一次 publish 時 auto-assign）
    """
    stmt = _published_works_base().where(CrmProjectShowcase.slug == slug)
    row = (await session.execute(stmt)).first()
    if not row and slug.isdigit():
        stmt = _published_works_base().where(CrmProjectShowcase.number == int(slug))
        row = (await session.execute(stmt)).first()
    if not row:
        return None
    sc, project = row

    cat_map = await _categories_for_projects(session, [sc.id])
    client_map = await _clients_for_projects(session, [project.id])
    data = _to_public_dict(sc, project, cat_map.get(sc.id), client_map.get(project.id))
    data["credits"] = sc.credits or []
    data["published_at"] = sc.published_at.isoformat() if sc.published_at else None
    data["gallery"] = sc.gallery or []
    data["process_items"] = sc.process_items or []

    # 附加影片（一頁多影片，Phase 3）— 逐項 parse youtube_id 給 Astro 直接 render
    from services.website.notion_service import _extract_youtube_id
    data["extra_videos"] = [
        {"url": v.get("url") or "", "caption": v.get("caption") or "",
         "youtube_id": _extract_youtube_id(v.get("url") or "") or None}
        for v in (sc.extra_videos or [])
        if isinstance(v, dict) and (v.get("url") or "").strip()
    ]

    # 系列互連（Phase 3）— 同專案其他 published 作品（read-time 推導，不落地）
    series_rows = await session.execute(
        _published_works_base()
        .where(CrmProjectShowcase.project_id == sc.project_id,
               CrmProjectShowcase.id != sc.id)
        .order_by(CrmProjectShowcase.sort_order.desc().nullslast(),
                  CrmProjectShowcase.published_at.desc().nullslast())
    )
    data["series"] = [
        {"slug": _slug_or_fallback(s2),
         "title": (s2.title or "").strip() or p2.name,
         "cover_url": s2.cover_url or _youtube_thumbnail(s2.youtube_id)}
        for s2, p2 in series_rows
    ]

    # Detail-only：作品級 SEO（faqs/narrative...）— website_project_seo key 自 1:N 起
    # 語意 = work id（既有資料 == project id，自動相容）
    from . import seo_service
    seo = await seo_service.get_project_seo(session, sc.id)
    data["seo_title"] = seo["seo_title"]
    data["seo_description"] = seo["seo_description"]
    data["seo_keywords"] = seo["keywords"]
    data["canonical_url"] = seo["canonical_url"]
    data["narrative_long"] = seo["narrative_long"]
    data["key_facts"] = seo["key_facts"]
    data["faqs"] = seo["faqs"]
    return data


async def list_featured_projects(session: AsyncSession, limit: int = 6) -> list[dict]:
    """首頁精選：featured 優先，不足時補最新 public。"""
    stmt = _published_works_base().where(
        CrmProjectShowcase.featured.is_(True)
    ).order_by(
        CrmProjectShowcase.sort_order.desc().nullslast(),
        CrmProjectShowcase.published_at.desc().nullslast(),
    ).limit(limit)
    rows = list(await session.execute(stmt))

    if len(rows) < limit:
        remaining = limit - len(rows)
        existing_ids = [sc.id for sc, _ in rows]
        fb_stmt = _published_works_base()
        if existing_ids:
            fb_stmt = fb_stmt.where(CrmProjectShowcase.id.notin_(existing_ids))
        fb_stmt = fb_stmt.order_by(
            CrmProjectShowcase.published_at.desc().nullslast()
        ).limit(remaining)
        rows.extend(await session.execute(fb_stmt))

    return await _build_public_dicts(session, rows)


async def _build_public_dicts(session: AsyncSession, rows: list) -> list[dict]:
    """一批 (sc, project) rows → 對外 dict（帶 categories / client / carousel_image）。
    list_featured / list_hero 共用；carousel_image 帶 gallery 第一張 fallback（輪播專用欄）。"""
    wids = [sc.id for sc, _ in rows]
    pids = [p.id for _, p in rows]
    cat_map = await _categories_for_projects(session, wids)
    client_map = await _clients_for_projects(session, pids)
    return [
        _to_public_dict(sc, p, cat_map.get(sc.id), client_map.get(p.id),
                        with_carousel_fallback=True)
        for sc, p in rows
    ]


async def list_hero_projects(session: AsyncSession, limit: int = 8) -> list[dict]:
    """首頁最上方 Hero 輪播：admin 在「首頁設定」挑選 + 排序的作品（home.hero_work_ids）。
    id 自 1:N 起語意 = work id（既有存的 project id == 主作品 work id，自動相容）。
    未設定 / 全部失效（作品被刪或下架）→ fallback list_featured_projects（精選前 N），首頁不空。"""
    from services.website.settings_service import get_all_settings
    settings = await get_all_settings(session)
    raw = settings.get("home.hero_work_ids")
    ids = [str(x) for x in raw if x] if isinstance(raw, list) else []
    rows = (await session.execute(
        _published_works_base().where(CrmProjectShowcase.id.in_(ids))
    )).all() if ids else []
    found = {sc.id: (sc, p) for sc, p in rows}
    ordered = [found[i] for i in ids if i in found]  # 保留 admin 拖曳排序、丟掉失效/未公開
    if not ordered:  # 未挑選 / 全失效 → fallback 精選作品，首頁不空
        return await list_featured_projects(session, limit=limit)
    return await _build_public_dicts(session, ordered[:limit])


async def list_all_featured_projects(session: AsyncSession) -> list[dict]:
    """首頁「精選作品」網格：所有 ⭐首頁精選 作品（不限數量、不補非精選作品）。"""
    stmt = _published_works_base().where(
        CrmProjectShowcase.featured.is_(True)
    ).order_by(
        CrmProjectShowcase.sort_order.desc().nullslast(),
        CrmProjectShowcase.published_at.desc().nullslast(),
    )
    rows = list(await session.execute(stmt))
    return await _build_public_dicts(session, rows)


def work_completeness_dict(sc) -> dict:
    """單一作品完成度 — 規則收斂在 core.crm_logic.work_completeness（純函式 + 測試）。

    sc: CrmProjectShowcase（或 _virtual_work_from_project 同形物件）。"""
    from core.crm_logic import work_completeness
    return work_completeness(
        video_url=sc.video_url, youtube_id=sc.youtube_id, extra_videos=sc.extra_videos,
        gallery=sc.gallery, cover_url=sc.cover_url, featured_image=sc.featured_image,
        description=sc.description, credits=sc.credits, credits_text=sc.credits_text,
    )


async def list_admin_projects(
    session: AsyncSession, include_non_public: bool = True
) -> list[dict]:
    """管理 Tab 列出所有作品（預設含未公開）。

    以專案為骨架 outer join 作品：沒有 showcase row 的專案以虛擬主作品呈現
    （admin 列表向來列出全部專案）；1:N 後同專案的子作品各自成列（主作品先）。
    """
    if include_non_public:
        stmt = select(CrmProject, CrmProjectShowcase).outerjoin(
            CrmProjectShowcase, CrmProjectShowcase.project_id == CrmProject.id
        )
    else:
        stmt = select(CrmProject, CrmProjectShowcase).join(
            CrmProjectShowcase, CrmProjectShowcase.project_id == CrmProject.id
        ).where(CrmProjectShowcase.published.is_(True))
    stmt = stmt.order_by(
        CrmProject.updated_at.desc().nullslast(),
        # 同專案內：主作品（id == project_id）先、再依 sort_order
        (CrmProjectShowcase.id == CrmProjectShowcase.project_id).desc().nullslast(),
        CrmProjectShowcase.sort_order.desc().nullslast(),
    )

    rows = [
        (sc if sc is not None else _virtual_work_from_project(p), p)
        for p, sc in await session.execute(stmt)
    ]
    wids = [sc.id for sc, _ in rows]
    pids = [p.id for _, p in rows]
    cat_map = await _categories_for_projects(session, wids)
    client_map = await _clients_for_projects(session, pids)
    result = []
    for sc, p in rows:
        d = _to_admin_dict(sc, p, cat_map.get(sc.id), client_map.get(p.id))
        d["completeness"] = work_completeness_dict(sc)
        result.append(d)
    return result


# wire key → 作品欄位對照（正本 = core.crm_logic.WORK_WIRE_FIELD_MAP）。
# 不含 public_credits / public_cover_url — credits / cover 只能從 showcase-edit
# 編輯（直接寫 sc.credits / sc.cover_url）。public_client 不在此 — 客戶是專案層
# 資料，留在 crm_projects（見 update_project_public）。
_WORK_FIELD_MAP = WORK_WIRE_FIELD_MAP


def mirror_main_work_to_project(sc, project) -> None:
    """主作品 dual-write：把最終 sc 值全量鏡射回 crm_projects.public_*（Phase 4 停寫）。

    全量而非只鏡射 touched 欄位 — backfill 後兩邊本就相等，全量寫可自癒歷史分岔。
    caller 自行判定 is_main_work 後呼叫（update_project_public / token PUT 共用）。
    """
    for wire_key, field in _WORK_FIELD_MAP.items():
        setattr(project, wire_key, getattr(sc, field))
    if sc.number is not None and project.public_number is None:
        project.public_number = sc.number


def _append_old_slug_core(old: str, new: str, existing_list) -> Optional[list]:
    """slug 變動 → old_slugs append 的共用核心（去重 + 自循環防護）。

    回傳新的 old_slugs list；不需變動時回 None。
    純數字 slug（=number 路由）不加，避免污染 redirect map（number 路由仍 work）。
    """
    old = (old or "").strip()
    new = (new or "").strip()
    if not old or old == new or old.isdigit():
        return None
    existing = list(existing_list or [])
    if old in existing:
        return None
    if new and new in existing:
        # 新 slug 曾是舊 slug — 從 old_slugs 移除（避免 redirect 自循環）
        existing = [s for s in existing if s != new]
    existing.append(old)
    return existing


def append_old_slug_if_changed(project: CrmProject, new_slug):
    """偵測 public_slug 變動 → 把舊 slug 加進 public_old_slugs（去重）。

    呼叫前 setattr 還沒跑，project.public_slug 仍是舊值。回傳 True 表示有 append。
    （crm_projects 鏡射層版本 — dual-write 期間 _sync_showcase_to_public 仍呼叫。）
    """
    updated = _append_old_slug_core(project.public_slug, new_slug, project.public_old_slugs)
    if updated is None:
        return False
    project.public_old_slugs = updated
    return True


def append_old_slug_if_changed_work(sc, new_slug):
    """作品版：偵測 sc.slug 變動 → append 進 sc.old_slugs（1:N 後的單一真相）。"""
    updated = _append_old_slug_core(sc.slug, new_slug, sc.old_slugs)
    if updated is None:
        return False
    sc.old_slugs = updated
    return True


async def mirror_public_to_crm(session: AsyncSession, project: CrmProject, updates: dict) -> None:
    """Option B mirror — 寫 public_* 時回寫到 CRM source-of-truth 欄位。

    `name` / `client_id` 是 CRM 專案管理 Tab 的顯示欄位,`public_title` / `public_client` 是
    SEO 覆寫層。從作品集 Tab 新增的作品 `name` 卡 sentinel `「（未命名作品）」`、`client_id`
    空白,PM 在 showcase-edit / 作品集 Tab 編輯填的標題/客戶若不回寫,CRM Tab 永遠顯示
    sentinel。寫入時鏡射兩條:

    - `public_title`(strip 後非空) → `name`
    - `public_client`(strip 後非空)若精確匹配既有 `Client.short_name` → 設 `client_id`
      (autocomplete dropdown 點選時 input 直接帶 short_name,精確比對最安全;
      模糊比對誤判風險高,留給人工)

    呼叫者:`update_project_public`(作品集 admin Tab edit)+ token PUT(showcase-edit)。
    """
    new_title = (updates.get("public_title") or "").strip()
    if new_title:
        project.name = new_title

    new_client_text = (updates.get("public_client") or "").strip()
    if new_client_text:
        c = (await session.execute(
            select(Client).where(Client.short_name == new_client_text)
        )).scalar_one_or_none()
        if c:
            project.client_id = c.id


async def create_child_work(session: AsyncSession, project: CrmProject,
                            title: Optional[str] = None):
    """在既有專案下新增子作品（1:N Phase 2）— 建 sc 列 + mint edit token。

    id 用 uuid4().hex（≠ 任何 project id → 永遠不是主作品）；prod_stage 預設
    '待製作'（直接進結案看板待辦）。不 commit（caller 統一 commit）。
    回傳 (sc, token)。
    """
    import uuid as _uuid
    from core.auth import create_token
    count = (await session.execute(
        select(func.count()).select_from(CrmProjectShowcase)
        .where(CrmProjectShowcase.project_id == project.id)
    )).scalar() or 0
    wid = _uuid.uuid4().hex
    token = create_token({"sub": wid, "scope": SHOWCASE_EDIT_SCOPE},
                         expires_days=SHOWCASE_EDIT_EXPIRES_DAYS)
    sc = CrmProjectShowcase(
        id=wid, project_id=project.id,
        title=(title or "").strip() or f"{project.name}（{count + 1}）",
        year=project.public_year,
        prod_stage="待製作",
        edit_token=token, editable=True,
    )
    session.add(sc)
    return sc, token


async def get_or_create_work(session: AsyncSession, work_id: str):
    """以 work id 取作品 + 所屬專案；找不到 sc 時視為舊 1:1 專案 id → get-or-create 主作品列。

    回傳 (sc, project)；兩者任一不存在回 (None, None)。不 commit（caller 統一 commit）。
    """
    sc = await session.get(CrmProjectShowcase, work_id)
    if sc is None:
        project = await session.get(CrmProject, work_id)
        if not project:
            return None, None
        # 用投影補水（不建空列）— 草稿專案可能 public_* 有值但沒 sc row（孤兒
        # backfill 只涵蓋 public=TRUE），空列 + 主作品全量鏡射會把那些值洗成 NULL
        sc = CrmProjectShowcase(**vars(_virtual_work_from_project(project)))
        session.add(sc)
        return sc, project
    project = await session.get(CrmProject, sc.project_id or sc.id)
    if not project:
        return None, None
    return sc, project


async def update_project_public(
    session: AsyncSession,
    work_id: str,
    updates: dict,
    category_ids: Optional[list[int]] = None,
) -> bool:
    """更新作品欄位 + 重置分類關聯（id 自 1:N 起語意 = work id；主作品 == 舊 project id）。

    wire key 沿用歷史 public_* 命名（前端不用改），實際寫 sc.*；主作品同時把
    對映欄位全量鏡射回 crm_projects.public_*（dual-write，Phase 4 停寫）。
    若 updates 把 published 從 False 切 True 且還沒分配 number，自動 assign max+1。
    Unpublish 不釋放號碼避免 republish 編號跳動。
    """
    sc, project = await get_or_create_work(session, work_id)
    if sc is None:
        return False

    # SEO 301：偵測 slug 變動 — 先讀舊值再 setattr
    if "public_slug" in updates:
        append_old_slug_if_changed_work(sc, updates["public_slug"])

    # public_old_slugs 寫入前 normalize（strip 網域、trim 結尾 /、去重）
    if "public_old_slugs" in updates and isinstance(updates["public_old_slugs"], list):
        updates = {**updates, "public_old_slugs": normalize_old_slugs_input(updates["public_old_slugs"])}

    for wire_key, v in updates.items():
        field = _WORK_FIELD_MAP.get(wire_key)
        if field:
            setattr(sc, field, v)

    # 客戶是專案層資料 — 不進 _WORK_FIELD_MAP，直接寫 project（所有作品共用顯示）
    if "public_client" in updates:
        project.public_client = updates["public_client"]

    await ensure_work_number(session, sc)

    if is_main_work(sc):
        mirror_main_work_to_project(sc, project)
        await mirror_public_to_crm(session, project, updates)

    if category_ids is not None:
        await session.execute(
            delete(WebsiteProjectCategory).where(WebsiteProjectCategory.project_id == sc.id)
        )
        session.add_all([
            WebsiteProjectCategory(project_id=sc.id, category_id=cid)
            for cid in category_ids
        ])

    await session.commit()
    return True


async def ensure_work_number(session: AsyncSession, sc) -> None:
    """首發配號：published 且還沒有 number 才配 max+1（unpublish 不收回，
    避免 republish 改號破壞 SEO 連結）。所有 publish 路徑統一走這裡。"""
    if sc.published and sc.number is None:
        sc.number = await _next_public_number(session)


async def _next_public_number(session: AsyncSession) -> int:
    """取下一個作品編號 — 從 1 開始，避免跳號。

    同時看 works(number) 與 crm_projects(public_number) 取 max：backfill 後主作品
    兩邊同號，理論上恆等；GREATEST 是防「有 public_number 但沒被 backfill」殘缺資料
    的便宜保險（兩邊 DB、多次部署）。
    """
    w = (await session.execute(
        select(func.coalesce(func.max(CrmProjectShowcase.number), 0))
    )).scalar_one()
    p = (await session.execute(
        select(func.coalesce(func.max(CrmProject.public_number), 0))
    )).scalar_one()
    return int(max(w or 0, p or 0)) + 1


async def reorder_projects(session: AsyncSession, ordered_ids: list[str]) -> None:
    """批次更新作品 sort_order（靠前的優先；ids 自 1:N 起語意 = work id）。

    dual-write：以同 id 更新 crm_projects.public_sort_order — 主作品命中、
    子作品的 work id 不會對到任何 project 天然 no-op（Phase 4 移除）。
    """
    total = len(ordered_ids)
    for idx, wid in enumerate(ordered_ids):
        val = total - idx
        await session.execute(
            update(CrmProjectShowcase).where(CrmProjectShowcase.id == wid).values(
                sort_order=val
            )
        )
        await session.execute(
            update(CrmProject).where(CrmProject.id == wid).values(
                public_sort_order=val
            )
        )
    await session.commit()


async def toggle_featured(session: AsyncSession, work_id: str, featured: bool) -> bool:
    """切換單一作品的精選狀態（dual-write 同 reorder_projects 的 no-op 技巧）。"""
    sc_result = await session.execute(
        update(CrmProjectShowcase).where(CrmProjectShowcase.id == work_id).values(featured=featured)
    )
    p_result = await session.execute(
        update(CrmProject).where(CrmProject.id == work_id).values(public_featured=featured)
    )
    await session.commit()
    return sc_result.rowcount > 0 or p_result.rowcount > 0


# ── Redirects（聚合所有 published 作品的 old_slugs） ──

async def list_redirects(session: AsyncSession) -> dict[str, str]:
    """聚合所有 published 作品的 old_slugs → 新 URL。

    衝突（同 old_url 對應多個 work）：依 published_at 排序，最新的勝
    （跟 post_service.list_redirects 同邏輯）。Unpublish 的作品不算（published=False
    時 redirects 不該繼續生效）。
    """
    stmt = _published_works_base().where(
        func.jsonb_array_length(CrmProjectShowcase.old_slugs) > 0
    ).order_by(
        CrmProjectShowcase.published_at.is_(None).asc(),
        CrmProjectShowcase.published_at.asc(),
    )
    out: dict[str, str] = {}
    for sc, _p in await session.execute(stmt):
        new_url = f"/works/{_slug_or_fallback(sc)}"
        for old_slug in (sc.old_slugs or []):
            if not isinstance(old_slug, str):
                continue
            old_url = _old_slug_to_url(old_slug)
            if not old_url:
                continue  # 跨網域 URL 或空字串 — 跳過
            # 跳過自循環（理論不該發生，append_old_slug_if_changed_work 已防護）
            if old_url == new_url:
                continue
            out[old_url] = new_url   # 後寫入勝
    return out
