"""services/website/settings_service.py
---
website_settings key-value store 的 get/put。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import WebsiteSetting


async def get_all_settings(session: AsyncSession) -> dict[str, Any]:
    """撈全部 key-value，回傳 dict。"""
    stmt = select(WebsiteSetting.key, WebsiteSetting.value)
    return {row[0]: row[1] for row in await session.execute(stmt)}


async def get_meta(session: AsyncSession) -> dict[str, Any]:
    """公開端 /meta：對外需要的欄位（company / seo / social / about / home.hero）。

    這些欄位皆對應 website_settings 表中的 key，admin 在「🌐 官網管理」Tab 可編輯。
    前端（Astro build）一次撈全部，避免多次 round-trip。
    """
    s = await get_all_settings(session)

    # 首頁信任數字「自動抓真實數據」：完成作品=公開作品數、合作品牌=客戶資料數、
    # 年資=今年−2013（成立年）。向下取整加「+」（隨資料增長、避免出現難看的精確數）。
    # admin 在「🏠 首頁設定」仍可逐欄覆寫（home.stat{i}_*，空則用即時真實值）。已移除「平均評分」。
    from sqlalchemy import text as _text
    from datetime import datetime as _dt
    try:
        _works = (await session.execute(_text("select count(*) from crm_projects where public=true"))).scalar() or 0
        _clients = (await session.execute(_text("select count(*) from clients"))).scalar() or 0
    except Exception:
        _works = _clients = 0
    def _floor10(n):
        return (int(n) // 10) * 10
    _real_stats = [
        (str(_dt.now().year - 2013), "年製作經驗", "Years"),
        (f"{_floor10(_works)}+", "完成作品", "Projects"),
        (f"{_floor10(_clients)}+", "合作品牌", "Brands"),
    ]

    # 頁面文案（marketing copy）：scheme `copy.<page>.<block>_<lang>`。
    # 掃出所有 copy.* key，組成巢狀 dict：copy[page][block_lang] = value。
    # Astro 各頁用 meta.copy?.<page>?.<block>_zh ?? "<硬寫 fallback>" 渲染，
    # admin 在各子視圖的「📝 頁面文案」卡編輯（透過共用 /admin/settings 寫入）。
    copy: dict[str, dict[str, str]] = {}
    for k, v in s.items():
        if not k.startswith("copy."):
            continue
        parts = k.split(".", 2)  # ["copy", "<page>", "<block_lang>"]
        if len(parts) != 3:
            continue
        _, page, block = parts
        # 空字串 / None 不放進 copy → Astro 的 `meta.copy?.x ?? "literal"`（nullish）
        # 才會回退硬寫預設（兌現各 admin 卡「留空則維持預設文案」的承諾）。
        if v:
            copy.setdefault(page, {})[block] = v

    return {
        "company_name_zh": s.get("company.name_zh", ""),
        "company_name_en": s.get("company.name_en", ""),
        "tagline": s.get("company.tagline", ""),
        "subtitle": s.get("company.subtitle", ""),
        "address": s.get("company.address", ""),
        "phone": s.get("company.phone", ""),
        "email": s.get("company.email", ""),
        "seo_default_title": s.get("seo.default_title", ""),
        "seo_default_description": s.get("seo.default_description", ""),
        "seo_og_image": s.get("seo.og_image", ""),
        # 品牌 Logo / favicon（admin「⚙️ 網站設定」品牌卡可上傳）；空則前端用預設 SVG。
        "brand_logo_url": s.get("brand.logo_url", ""),
        "brand_favicon_url": s.get("brand.favicon_url", ""),
        "social": {
            "youtube": s.get("social.youtube", ""),
            "instagram": s.get("social.instagram", ""),
            "facebook": s.get("social.facebook", ""),
        },
        "about_intro_zh": s.get("about.intro_zh", ""),
        "about_intro_en": s.get("about.intro_en", ""),
        "about_founded_year": s.get("about.founded_year", ""),
        "about_team_intro_zh": s.get("about.team_intro_zh", ""),
        "home_hero_youtube_id": s.get("home.hero_youtube_id", ""),
        # 首頁 WhoWeAre 段 Showreel（admin「🏠 首頁設定」可編；空則沿用 hero_youtube_id）
        "home_showreel_id": s.get("home.showreel_id", ""),
        # 首頁 WhoWeAre 段 3 欄 stats，自動抓真實數據（admin 可逐欄覆寫；空則用即時真實值）
        "home_stats": [
            {
                "value": s.get(f"home.stat{i+1}_value") or _real_stats[i][0],
                "label_zh": s.get(f"home.stat{i+1}_label_zh") or _real_stats[i][1],
                "label_en": s.get(f"home.stat{i+1}_label_en") or _real_stats[i][2],
            }
            for i in range(3)
        ],
        # 首頁 Testimonials 段整體評分 badge（admin「🏠 首頁設定」可編；空則前端用預設）
        "home_rating_value": s.get("home.rating_value", ""),
        "home_rating_count": s.get("home.rating_count", ""),
        # SEO 索引控制（影響 BaseLayout noindex meta + robots.txt 動態端點）。
        # `is True` 比 `bool()` 嚴：避免使用者誤把 "false" 字串塞進去後變 truthy。
        "indexable": s.get("seo.indexable") is True,
        "ai_allow": s.get("seo.ai_allow") is True,
        # admin 在「llms.txt 編輯器」自填的 body；空則 /llms.txt 走自動生成
        "llms_txt_body": s.get("seo.llms_txt_body", ""),
        # /portfolio 頁面頂部「下載作品集 PDF」按鈕；空則隱藏按鈕。
        # admin 在「作品集管理」Tab 頂部設定（works.js）。
        "portfolio_pdf_url": s.get("portfolio.pdf_url", ""),
        # 頁面行銷文案覆寫（copy.<page>.<block>_<lang>）；空則 Astro 用硬寫 fallback。
        "copy": copy,
        # 聯絡表單選項清單（forms.contact.service_types / budget_ranges）。
        # JSON list of {value, label_zh, label_en}；value 保持穩定 token（後端/CRM 存這個），
        # 只 label 可編。空 / 非 list → ContactForm.astro 用硬寫 fallback 選項。
        "forms": {
            "contact": {
                "service_types": _coerce_option_list(s.get("forms.contact.service_types")),
                "budget_ranges": _coerce_option_list(s.get("forms.contact.budget_ranges")),
            },
        },
    }


def _coerce_option_list(raw: Any) -> list[dict[str, str]]:
    """settings JSON → 乾淨的 [{value, label_zh, label_en}]。

    防呆：非 list / 缺 value / value 非字串的項目丟掉；label 缺則退回 value。
    空 list 直接回 []（ContactForm.astro 看到空就用硬寫 fallback 選項）。
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        value = it.get("value")
        if not isinstance(value, str) or not value.strip():
            continue
        label_zh = it.get("label_zh") if isinstance(it.get("label_zh"), str) else ""
        label_en = it.get("label_en") if isinstance(it.get("label_en"), str) else ""
        out.append({
            "value": value.strip(),
            "label_zh": (label_zh or value).strip(),
            "label_en": (label_en or label_zh or value).strip(),
        })
    return out


async def update_settings(
    session: AsyncSession,
    values: dict[str, Any],
    updated_by: Optional[str] = None,
) -> int:
    """批次 upsert settings。回傳更新的 key 數量。"""
    if not values:
        return 0
    rows = [
        {"key": k, "value": v, "updated_by": updated_by, "updated_at": datetime.now(timezone.utc)}
        for k, v in values.items()
    ]
    stmt = insert(WebsiteSetting).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={
            "value": stmt.excluded.value,
            "updated_by": stmt.excluded.updated_by,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await session.execute(stmt)
    await session.commit()
    return len(rows)
