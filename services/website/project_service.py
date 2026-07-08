"""services/website/project_service.py
---
作品查詢、排序、公開切換邏輯。

操作 crm_projects 的 public_* 欄位 + website_project_categories 關聯表。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Client, CrmProject
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


async def _showcase_first_for_projects(
    session: AsyncSession, project_ids: list[str]
) -> dict[str, str]:
    """Batch：project_id → 成果展示第一張圖 URL（gallery[0].url）。首頁輪播精選圖的 fallback。"""
    if not project_ids:
        return {}
    from db.models import CrmProjectShowcase
    # crm_project_showcase.id == project_id（1:1；無 project_id 欄位）
    stmt = select(CrmProjectShowcase.id, CrmProjectShowcase.gallery).where(
        CrmProjectShowcase.id.in_(project_ids)
    )
    out: dict[str, str] = {}
    for pid, gallery in await session.execute(stmt):
        if isinstance(gallery, list) and gallery and isinstance(gallery[0], dict):
            url = (gallery[0].get("url") or "").strip()
            if url:
                out[pid] = url
    return out


def _slug_or_fallback(p: CrmProject) -> str:
    """slug 優先序：admin 自訂 > public_number（1, 2, 3...）> 'work' 兜底。

    public_number 在第一次 publish 時 auto-assign（在 toggle_public），永久不變
    避免 republish 改編號破壞 SEO 連結。fresh public 還沒分配 number → 用
    'work' 字串避免空 URL；理論上不該發生（toggle_public 會分配）。"""
    custom = (p.public_slug or "").strip()
    if custom:
        return custom
    if p.public_number is not None:
        return str(p.public_number)
    return "work"


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
    p: CrmProject,
    cat_data: Optional[dict[str, list[str]]] = None,
    client_name: Optional[str] = None,
    showcase_first: Optional[str] = None,
) -> dict:
    """投影 public 欄位到 dict — caller 必須保證 p 是 public（list/get caller 都在 SQL 層
    `WHERE public.is_(True)` 過濾過）。本函式不檢查 public flag，避免 admin path
    （_to_admin_dict）誤拒未公開作品。新增 caller 時請 SQL 層先過濾。

    cat_data: {"categories": [...], "tags": [...]} — _categories_for_projects 的單筆。
    client_name: _clients_for_projects 解析的 client 顯示名(short_name fallback full_name)。
        Source of truth 是 CRM 的 client_id;public_client 純 SEO 覆寫,空值就 fallback。
        title 同理:public_title 空就 fallback 到 name。
    """
    cd = cat_data or _empty_cat_data()
    return {
        "slug": _slug_or_fallback(p),
        "title": (p.public_title or "").strip() or p.name,
        "client": (p.public_client or "").strip() or (client_name or ""),
        "youtube_id": p.public_youtube_id,
        "description": p.public_description,
        "year": p.public_year,
        "categories": cd.get("categories") or [],
        "tags": cd.get("tags") or [],
        "credits_mode": p.public_credits_mode or "block",
        "credits_text": p.public_credits_text,
        "thumbnail_url": _youtube_thumbnail(p.public_youtube_id),
        # OG image fallback chain：work cover > YouTube thumb > BaseLayout meta.seo_og_image
        "cover_url": p.public_cover_url or _youtube_thumbnail(p.public_youtube_id),
        # 首頁輪播取圖：精選圖 → 成果展示第一張（→ 前端再接 YouTube 縮圖，見 HomeSlideshow）。
        # 對齊上面 cover_url 的「後端把可解析的都收斂成單一欄位」做法。showcase_first 只有
        # featured 清單 caller 會帶，其他 caller 為 None（此欄本就是輪播專用）。
        "carousel_image": p.public_featured_image or showcase_first or None,
        "featured": bool(p.public_featured),
        "noindex": bool(p.public_noindex),
        # SEO 301 來源舊 URL — Astro JSON-LD sameAs / markdown 「曾用 URL」用
        # 支援兩種寫法：純 slug（自動 prepend /works/）+ '/' 開頭完整路徑（外部 CMS 遷入）
        "old_urls": [u for u in (_old_slug_to_url(s) for s in (p.public_old_slugs or [])) if u],
        # 列表卡片摘要 — 給 WorkCard 一行顯示用
        "credits_summary": _credits_summary(p.public_credits),
        # 英文版（transcreation；空則前端 fallback 中文。client_en 為手動指定）
        "title_en": p.public_title_en,
        "description_en": p.public_description_en,
        "client_en": p.public_client_en,
    }


def _to_admin_dict(
    p: CrmProject,
    cat_data: Optional[dict[str, list[str]]] = None,
    client_name: Optional[str] = None,
) -> dict:
    d = _to_public_dict(p, cat_data, client_name)
    d.update({
        "id": p.id,
        "name": p.name,
        "public": bool(p.public),
        "public_sort_order": p.public_sort_order or 0,
        "credits": p.public_credits or [],
        "published_at": p.public_published_at.isoformat() if p.public_published_at else None,
        "redirect_count": len(p.public_old_slugs or []),
    })
    return d


async def list_public_projects(
    session: AsyncSession,
    category_slug: Optional[str] = None,
    page: int = 1,
    limit: int = 12,
) -> tuple[list[dict], int]:
    """列出對外公開的作品（分頁 + 分類篩選）。"""
    base = select(CrmProject).where(CrmProject.public.is_(True))

    if category_slug:
        cat_subq = select(WebsiteCategory.id).where(WebsiteCategory.slug == category_slug).scalar_subquery()
        base = base.join(
            WebsiteProjectCategory, WebsiteProjectCategory.project_id == CrmProject.id
        ).where(WebsiteProjectCategory.category_id == cat_subq)

    total = (await session.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0

    # 排序原則（依序）：1 精選作品先 → 2 年份新→舊 → 3 發布日新→舊。
    # public_sort_order 當最後平手 tiebreaker（保留手動微調、且排序穩定）。
    stmt = base.order_by(
        CrmProject.public_featured.desc().nullslast(),
        CrmProject.public_year.desc().nullslast(),
        CrmProject.public_published_at.desc().nullslast(),
        CrmProject.public_sort_order.desc().nullslast(),
    ).offset((page - 1) * limit).limit(limit)

    projects = list((await session.execute(stmt)).scalars())
    pids = [p.id for p in projects]
    cat_map = await _categories_for_projects(session, pids)
    client_map = await _clients_for_projects(session, pids)
    return [
        _to_public_dict(p, cat_map.get(p.id), client_map.get(p.id))
        for p in projects
    ], total


async def get_public_project_by_slug(session: AsyncSession, slug: str) -> Optional[dict]:
    """單一公開作品詳情。slug 解析順序：
       1. public_slug（admin 自訂語意 slug）
       2. public_number（純數字 slug，第一次 publish 時 auto-assign）
    """
    stmt = select(CrmProject).where(
        and_(CrmProject.public.is_(True), CrmProject.public_slug == slug)
    )
    project = (await session.execute(stmt)).scalar_one_or_none()
    if not project and slug.isdigit():
        stmt = select(CrmProject).where(
            and_(CrmProject.public.is_(True), CrmProject.public_number == int(slug))
        )
        project = (await session.execute(stmt)).scalar_one_or_none()
    if not project:
        return None

    cat_map = await _categories_for_projects(session, [project.id])
    client_map = await _clients_for_projects(session, [project.id])
    data = _to_public_dict(project, cat_map.get(project.id), client_map.get(project.id))
    data["credits"] = project.public_credits or []
    data["published_at"] = project.public_published_at.isoformat() if project.public_published_at else None

    # Detail-only：showcase（gallery/process_items）+ SEO（faqs/narrative...）並行撈（同 PK 兩張表）
    from db.models import CrmProjectShowcase
    import asyncio
    from . import seo_service
    sc, seo = await asyncio.gather(
        session.get(CrmProjectShowcase, project.id),
        seo_service.get_project_seo(session, project.id),
    )
    if sc:
        data["gallery"] = sc.gallery or []
        data["process_items"] = sc.process_items or []
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
    stmt = select(CrmProject).where(
        and_(CrmProject.public.is_(True), CrmProject.public_featured.is_(True))
    ).order_by(
        CrmProject.public_sort_order.desc().nullslast(),
        CrmProject.public_published_at.desc().nullslast(),
    ).limit(limit)
    featured = list((await session.execute(stmt)).scalars())

    if len(featured) < limit:
        remaining = limit - len(featured)
        existing_ids = [p.id for p in featured]
        fb_stmt = select(CrmProject).where(CrmProject.public.is_(True))
        if existing_ids:
            fb_stmt = fb_stmt.where(CrmProject.id.notin_(existing_ids))
        fb_stmt = fb_stmt.order_by(
            CrmProject.public_published_at.desc().nullslast()
        ).limit(remaining)
        featured.extend((await session.execute(fb_stmt)).scalars())

    return await _build_public_dicts(session, featured)


async def _build_public_dicts(session: AsyncSession, projects: list) -> list[dict]:
    """一批 CrmProject → 對外 dict（帶 categories / client / carousel_image）。
    list_featured / list_hero 共用；carousel_image 的 showcase_first 只對缺精選圖者查。"""
    pids = [p.id for p in projects]
    cat_map = await _categories_for_projects(session, pids)
    client_map = await _clients_for_projects(session, pids)
    sc_first_map = await _showcase_first_for_projects(
        session, [p.id for p in projects if not p.public_featured_image]
    )
    return [
        _to_public_dict(p, cat_map.get(p.id), client_map.get(p.id), sc_first_map.get(p.id))
        for p in projects
    ]


async def list_hero_projects(session: AsyncSession, limit: int = 8) -> list[dict]:
    """首頁最上方 Hero 輪播：admin 在「首頁設定」挑選 + 排序的作品（home.hero_work_ids）。
    未設定 / 全部失效（作品被刪或下架）→ fallback list_featured_projects（精選前 N），首頁不空。"""
    from services.website.settings_service import get_all_settings
    settings = await get_all_settings(session)
    raw = settings.get("home.hero_work_ids")
    ids = [str(x) for x in raw if x] if isinstance(raw, list) else []
    rows = (await session.execute(
        select(CrmProject).where(
            and_(CrmProject.public.is_(True), CrmProject.id.in_(ids))
        )
    )).scalars() if ids else []
    found = {p.id: p for p in rows}
    ordered = [found[i] for i in ids if i in found]  # 保留 admin 拖曳排序、丟掉失效/未公開
    if not ordered:  # 未挑選 / 全失效 → fallback 精選作品，首頁不空
        return await list_featured_projects(session, limit=limit)
    return await _build_public_dicts(session, ordered[:limit])


async def list_all_featured_projects(session: AsyncSession) -> list[dict]:
    """首頁「精選作品」網格：所有 ⭐首頁精選 作品（不限數量、不補非精選作品）。"""
    stmt = select(CrmProject).where(
        and_(CrmProject.public.is_(True), CrmProject.public_featured.is_(True))
    ).order_by(
        CrmProject.public_sort_order.desc().nullslast(),
        CrmProject.public_published_at.desc().nullslast(),
    )
    projects = list((await session.execute(stmt)).scalars())
    return await _build_public_dicts(session, projects)


async def _completeness_for_projects(session: AsyncSession, projects: list) -> dict[str, dict]:
    """Batch：project_id → {video, images, description, credits} 完成度（與結案看板同邏輯）。
    一次 IN 撈 showcase 需要的欄位，避免逐件 get（作品上百件 = N+1）。"""
    from db.models import CrmProjectShowcase
    pids = [p.id for p in projects]
    if not pids:
        return {}
    sc_map: dict = {}
    stmt = select(
        CrmProjectShowcase.id, CrmProjectShowcase.video_url, CrmProjectShowcase.gallery,
        CrmProjectShowcase.cover_url, CrmProjectShowcase.description,
        CrmProjectShowcase.credits, CrmProjectShowcase.credits_text,
    ).where(CrmProjectShowcase.id.in_(pids))
    for sid, video_url, gallery, cover_url, description, credits, credits_text in await session.execute(stmt):
        sc_map[sid] = (video_url, gallery, cover_url, description, credits, credits_text)
    out: dict = {}
    for p in projects:
        video_url, gallery, cover_url, description, credits, credits_text = sc_map.get(
            p.id, (None, None, None, None, None, None))
        out[p.id] = {
            "video": bool(video_url or p.public_youtube_id),
            "images": bool((gallery and len(gallery) > 0) or p.public_featured_image or cover_url),
            "description": bool((description or "").strip() or (p.public_description or "").strip()),
            # credits 有填：結構化 blocks 或 文字層 credits_text 或 公開 credits 任一有內容
            "credits": bool((credits and len(credits) > 0)
                            or (credits_text or "").strip()
                            or (p.public_credits and len(p.public_credits) > 0)),
        }
    return out


async def list_admin_projects(
    session: AsyncSession, include_non_public: bool = True
) -> list[dict]:
    """管理 Tab 列出所有作品（預設含未公開）。"""
    stmt = select(CrmProject)
    if not include_non_public:
        stmt = stmt.where(CrmProject.public.is_(True))
    stmt = stmt.order_by(CrmProject.updated_at.desc().nullslast())

    projects = list((await session.execute(stmt)).scalars())
    pids = [p.id for p in projects]
    cat_map = await _categories_for_projects(session, pids)
    client_map = await _clients_for_projects(session, pids)
    comp_map = await _completeness_for_projects(session, projects)
    result = []
    for p in projects:
        d = _to_admin_dict(p, cat_map.get(p.id), client_map.get(p.id))
        d["completeness"] = comp_map.get(p.id)
        result.append(d)
    return result


_UPDATABLE_FIELDS = {
    "public", "public_slug", "public_title", "public_client", "public_youtube_id",
    "public_description", "public_year", "public_featured",
    "public_sort_order", "public_published_at", "public_old_slugs", "public_noindex",
    # 不含 public_credits / public_cover_url — credits / cover 只能從 showcase-edit
    # 編輯走 _sync_showcase_to_public 反向 mirror。若這裡也允許 admin Tab 直寫，
    # 下次 PM save showcase 會整個覆蓋。
}


def append_old_slug_if_changed(project: CrmProject, new_slug):
    """偵測 public_slug 變動 → 把舊 slug 加進 public_old_slugs（去重）。

    呼叫前 setattr 還沒跑，project.public_slug 仍是舊值。回傳 True 表示有 append。
    純數字 slug（=public_number）不加，避免污染 redirect map（number 路由仍 work）。
    """
    old = (project.public_slug or "").strip()
    new = (new_slug or "").strip()
    if not old or old == new or old.isdigit():
        return False
    existing = list(project.public_old_slugs or [])
    if old in existing:
        return False
    if new and new in existing:
        # 新 slug 曾是舊 slug — 從 old_slugs 移除（避免 redirect 自循環）
        existing = [s for s in existing if s != new]
    existing.append(old)
    project.public_old_slugs = existing
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


async def update_project_public(
    session: AsyncSession,
    project_id: str,
    updates: dict,
    category_ids: Optional[list[int]] = None,
) -> bool:
    """更新 crm_projects.public_* 欄位 + 重置分類關聯。

    若 updates 把 public 從 False 切到 True 且 project 還沒分配 public_number，
    自動 assign max+1。Unpublish 不釋放號碼避免 republish 編號跳動。
    """
    project = await session.get(CrmProject, project_id)
    if not project:
        return False

    # SEO 301：偵測 slug 變動 — 先讀舊值再 setattr
    if "public_slug" in updates:
        append_old_slug_if_changed(project, updates["public_slug"])

    # public_old_slugs 寫入前 normalize（strip 網域、trim 結尾 /、去重）
    if "public_old_slugs" in updates and isinstance(updates["public_old_slugs"], list):
        updates = {**updates, "public_old_slugs": normalize_old_slugs_input(updates["public_old_slugs"])}

    for k, v in updates.items():
        if k in _UPDATABLE_FIELDS:
            setattr(project, k, v)

    await mirror_public_to_crm(session, project, updates)

    # First-publish auto-number
    if project.public and project.public_number is None:
        project.public_number = await _next_public_number(session)

    if category_ids is not None:
        await session.execute(
            delete(WebsiteProjectCategory).where(WebsiteProjectCategory.project_id == project_id)
        )
        session.add_all([
            WebsiteProjectCategory(project_id=project_id, category_id=cid)
            for cid in category_ids
        ])

    await session.commit()
    return True


async def _next_public_number(session: AsyncSession) -> int:
    """取下一個 public_number — 從 1 開始，避免跳號。"""
    n = (await session.execute(
        select(func.coalesce(func.max(CrmProject.public_number), 0) + 1)
    )).scalar_one()
    return int(n)


async def assign_public_number_if_needed(session: AsyncSession, project_id: str) -> Optional[int]:
    """showcase publish flow（_sync_showcase_to_public）也走這條 — 若 project
    切到 public 但還沒有 number 就配一個。"""
    project = await session.get(CrmProject, project_id)
    if not project or not project.public or project.public_number is not None:
        return None
    project.public_number = await _next_public_number(session)
    return project.public_number


async def reorder_projects(session: AsyncSession, ordered_ids: list[str]) -> None:
    """批次更新 public_sort_order（靠前的優先）。"""
    total = len(ordered_ids)
    for idx, pid in enumerate(ordered_ids):
        await session.execute(
            update(CrmProject).where(CrmProject.id == pid).values(
                public_sort_order=total - idx
            )
        )
    await session.commit()


async def toggle_featured(session: AsyncSession, project_id: str, featured: bool) -> bool:
    """切換單一作品的精選狀態。"""
    result = await session.execute(
        update(CrmProject).where(CrmProject.id == project_id).values(public_featured=featured)
    )
    await session.commit()
    return result.rowcount > 0


# ── Redirects（聚合所有 public works 的 public_old_slugs） ──

async def list_redirects(session: AsyncSession) -> dict[str, str]:
    """聚合所有 public works 的 old_slugs → 新 URL。

    衝突（同 old_url 對應多個 work）：依 public_published_at 排序，最新的勝
    （跟 post_service.list_redirects 同邏輯）。Unpublish 的作品不算（public=False
    時 redirects 不該繼續生效）。
    """
    stmt = (
        select(CrmProject)
        .where(and_(CrmProject.public.is_(True),
                    func.jsonb_array_length(CrmProject.public_old_slugs) > 0))
        .order_by(CrmProject.public_published_at.is_(None).asc(),
                  CrmProject.public_published_at.asc())
    )
    out: dict[str, str] = {}
    for project in (await session.execute(stmt)).scalars():
        new_url = f"/works/{_slug_or_fallback(project)}"
        for old_slug in (project.public_old_slugs or []):
            if not isinstance(old_slug, str):
                continue
            old_url = _old_slug_to_url(old_slug)
            if not old_url:
                continue  # 跨網域 URL 或空字串 — 跳過
            # 跳過自循環（理論不該發生，append_old_slug_if_changed 已防護）
            if old_url == new_url:
                continue
            out[old_url] = new_url   # 後寫入勝
    return out
