"""services/website/seo_service.py
---
SEO 內容（FAQ / Testimonial / QuickFact）CRUD。

3 個實體共用同一套 list/create/update/delete 模式 — 以 `_create/_update/_delete/_list`
泛型函式接受 model class，每實體只要 4 行 thin wrapper。
"""
from __future__ import annotations

import logging
from datetime import date as _date, datetime as _dt
from typing import Any, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CrmProject
from db.models_website import (
    WebsiteFAQ, WebsiteTestimonial, WebsiteQuickFact, WebsiteProjectSeo,
)
from . import _crud_base as _crud

logger = logging.getLogger(__name__)


def _faq_to_dict(o: WebsiteFAQ) -> dict[str, Any]:
    return {
        "id": o.id,
        "question_zh": o.question_zh,
        "question_en": o.question_en,
        "answer_zh": o.answer_zh,
        "answer_en": o.answer_en,
        "sort_order": o.sort_order,
        "visible": o.visible,
    }


def _testimonial_to_dict(o: WebsiteTestimonial) -> dict[str, Any]:
    return {
        "id": o.id,
        "author_zh": o.author_zh,
        "author_en": o.author_en,
        "role_zh": o.role_zh,
        "role_en": o.role_en,
        "company": o.company,
        "rating": o.rating,
        "content_zh": o.content_zh,
        "content_en": o.content_en,
        "sort_order": o.sort_order,
        "visible": o.visible,
        "date_published": o.date_published.isoformat() if o.date_published else None,
    }


def _quick_fact_to_dict(o: WebsiteQuickFact) -> dict[str, Any]:
    return {
        "id": o.id,
        "label_zh": o.label_zh,
        "label_en": o.label_en,
        "value": o.value,
        "sort_order": o.sort_order,
        "visible": o.visible,
    }


def _coerce_testimonial_date(data: dict) -> dict:
    """testimonial 的 date_published ISO string → date 物件。

    純函式：回傳新 dict 不 mutate 入參，避免污染 caller payload（log / retry 用）。
    """
    if "date_published" not in data or not isinstance(data["date_published"], str):
        return data
    try:
        coerced = _date.fromisoformat(data["date_published"]) if data["date_published"] else None
    except ValueError:
        coerced = None
    return {**data, "date_published": coerced}


# ── FAQ ──
async def list_faqs(session, visible_only=False):
    return await _crud.list_items(session, WebsiteFAQ, _faq_to_dict, visible_only=visible_only)
async def create_faq(session, data):
    return await _crud.create_item(session, WebsiteFAQ, data, _faq_to_dict)
async def update_faq(session, item_id, data):
    return await _crud.update_item(session, WebsiteFAQ, item_id, data, _faq_to_dict)
async def delete_faq(session, item_id):
    return await _crud.delete_item(session, WebsiteFAQ, item_id)


# ── Testimonial ──
async def list_testimonials(session, visible_only=False):
    return await _crud.list_items(session, WebsiteTestimonial, _testimonial_to_dict, visible_only=visible_only)
async def create_testimonial(session, data):
    return await _crud.create_item(session, WebsiteTestimonial, data, _testimonial_to_dict, pre_write=_coerce_testimonial_date)
async def update_testimonial(session, item_id, data):
    return await _crud.update_item(session, WebsiteTestimonial, item_id, data, _testimonial_to_dict, pre_write=_coerce_testimonial_date)
async def delete_testimonial(session, item_id):
    return await _crud.delete_item(session, WebsiteTestimonial, item_id)


# ── Quick Fact ──
async def list_quick_facts(session, visible_only=False):
    return await _crud.list_items(session, WebsiteQuickFact, _quick_fact_to_dict, visible_only=visible_only)
async def create_quick_fact(session, data):
    return await _crud.create_item(session, WebsiteQuickFact, data, _quick_fact_to_dict)
async def update_quick_fact(session, item_id, data):
    return await _crud.update_item(session, WebsiteQuickFact, item_id, data, _quick_fact_to_dict)
async def delete_quick_fact(session, item_id):
    return await _crud.delete_item(session, WebsiteQuickFact, item_id)


# ══════════════════════════════════════════════════════════
# 作品級 SEO（pipeline 給 AI 補內容用 — 不走 _crud_base 因為 1:1 upsert 模型）
# ══════════════════════════════════════════════════════════

def project_seo_to_dict(o: WebsiteProjectSeo) -> dict[str, Any]:
    return {
        "project_id": o.project_id,
        "seo_title": o.seo_title,
        "seo_description": o.seo_description,
        "keywords": list(o.keywords or []),
        "canonical_url": o.canonical_url,
        "narrative_long": o.narrative_long,
        "key_facts": list(o.key_facts or []),
        "faqs": list(o.faqs or []),
        "needs_ai_review": bool(o.needs_ai_review),
        "last_ai_review_at": o.last_ai_review_at.isoformat() if o.last_ai_review_at else None,
        "last_ai_review_by": o.last_ai_review_by,
        "ai_review_notes": o.ai_review_notes,
    }


def _empty_seo_dict(project_id: str) -> dict[str, Any]:
    """還沒生過 SEO 的作品 — 預設值跟 model defaults 對齊，needs_ai_review=True
    讓它一定會出現在 audit list。"""
    return {
        "project_id": project_id,
        "seo_title": None,
        "seo_description": None,
        "keywords": [],
        "canonical_url": None,
        "narrative_long": None,
        "key_facts": [],
        "faqs": [],
        "needs_ai_review": True,
        "last_ai_review_at": None,
        "last_ai_review_by": None,
        "ai_review_notes": None,
    }


async def get_project_seo(session: AsyncSession, project_id: str) -> dict[str, Any]:
    """取單一作品 SEO 內容。沒 row 視同 defaults（不 auto-create —
    第一次 PATCH 才寫；保持 DB 不被 audit 端點意外灌一堆空 row）。"""
    row = await session.get(WebsiteProjectSeo, project_id)
    return project_seo_to_dict(row) if row else _empty_seo_dict(project_id)


_PATCHABLE_FIELDS = {
    "seo_title", "seo_description", "keywords", "canonical_url",
    "narrative_long", "key_facts", "faqs",
    "ai_review_notes",
}


def _coerce_canonical_url(payload: dict) -> dict:
    """canonical_url 必須是 http(s):// 起頭、有 path、無控制字元；否則丟掉。

    PM 從 showcase-edit 貼錯（typo / 空格 / 無 protocol）會被靜默丟掉而非一路寫到
    `<link rel="canonical">` HTML。empty string / null 視為「清空」由 setattr 寫 NULL。
    """
    if "canonical_url" not in payload:
        return payload
    raw = payload["canonical_url"]
    if raw is None:
        return payload  # 明確清空 — 接受
    if not isinstance(raw, str):
        return {**payload, "canonical_url": None}
    s = raw.strip()
    if not s:
        return {**payload, "canonical_url": None}
    # 須 http(s):// 起頭、之後接 host
    import re
    if not re.match(r"^https?://[^\s/?#]+", s):
        return {**payload, "canonical_url": None}
    if re.search(r"[\s\"'\x00-\x1f]", s):
        return {**payload, "canonical_url": None}
    return {**payload, "canonical_url": s}

# PM 從 showcase-edit token 路徑可改的欄位 — 只讓 PM 動「短文字覆寫」類，
# 不暴露 narrative_long / key_facts / faqs / ai_review_notes（這些是 AI pipeline 寫
# 的長文，PM 改了會被下次 AI 覆蓋；要修長文應從 admin Tab 走 JWT auth）。
SHOWCASE_EDIT_PATCHABLE_FIELDS = frozenset({
    "seo_title", "seo_description", "keywords", "canonical_url",
})


async def update_project_seo(
    session: AsyncSession, project_id: str, data: dict, *, by: Optional[str] = None,
) -> dict[str, Any]:
    """Upsert SEO content for a work. 只接 _PATCHABLE_FIELDS 內欄位。

    自動 set last_ai_review_at = now + last_ai_review_by = by；needs_ai_review 由
    approve endpoint 切換、不在這裡動（避免 PATCH 立刻消失於 audit list）。
    """
    row = await session.get(WebsiteProjectSeo, project_id)
    if not row:
        row = WebsiteProjectSeo(project_id=project_id)
        session.add(row)

    data = _coerce_canonical_url(data)
    for k, v in data.items():
        if k in _PATCHABLE_FIELDS:
            setattr(row, k, v)
    row.last_ai_review_at = _dt.utcnow()
    if by:
        row.last_ai_review_by = by

    await session.commit()
    await session.refresh(row)
    return project_seo_to_dict(row)


async def approve_project_seo(session: AsyncSession, project_id: str) -> bool:
    """admin 看過 draft 後 approve — needs_ai_review=False。
    沒 row 直接 noop（沒生過 SEO 何來 approve）。"""
    row = await session.get(WebsiteProjectSeo, project_id)
    if not row:
        return False
    row.needs_ai_review = False
    await session.commit()
    return True


async def mark_project_seo_needs_review(session: AsyncSession, project_id: str) -> None:
    """Showcase 大幅編輯後 PM 想重生 SEO → 切回 needs_ai_review=True（upsert）。

    沒 row 也補建一筆 — 否則該作品根本不在 audit list 顯示出「等待 AI」狀態
    （audit 端點視沒 row 為 needs_review，但顯示不出「PM 主動觸發」這層意圖）。
    不動 last_ai_review_at — 那是「最後一次 AI 真的寫過內容」的時間。
    """
    row = await session.get(WebsiteProjectSeo, project_id)
    if not row:
        row = WebsiteProjectSeo(project_id=project_id, needs_ai_review=True)
        session.add(row)
    else:
        row.needs_ai_review = True
    await session.commit()


async def list_seo_audit(session: AsyncSession) -> list[dict]:
    """SEO audit 列表 — 所有 public 作品 + completeness 評分 + needs_ai_review flag。

    給 admin 一覽哪些缺 SEO；也給 Claude pipeline 知道要先處理誰。
    """
    # 撈所有 public works 的 id + 基本資訊
    proj_stmt = (
        select(CrmProject)
        .where(CrmProject.public.is_(True))
        .order_by(
            CrmProject.public_sort_order.desc().nullslast(),
            CrmProject.public_published_at.desc().nullslast(),
        )
    )
    projects = list((await session.execute(proj_stmt)).scalars())

    # 一次撈全部 SEO row（避免 N+1）
    if projects:
        seo_stmt = select(WebsiteProjectSeo).where(
            WebsiteProjectSeo.project_id.in_([p.id for p in projects])
        )
        seo_map = {r.project_id: r for r in (await session.execute(seo_stmt)).scalars()}
    else:
        seo_map = {}

    out: list[dict] = []
    for p in projects:
        seo_row = seo_map.get(p.id)
        seo = project_seo_to_dict(seo_row) if seo_row else _empty_seo_dict(p.id)

        # Completeness 評分：每填一項 +1，總分 6（seo_title/desc/keywords/narrative/facts/faqs）
        score = sum([
            1 if (seo["seo_title"] or "").strip() else 0,
            1 if (seo["seo_description"] or "").strip() else 0,
            1 if seo["keywords"] else 0,
            1 if (seo["narrative_long"] or "").strip() else 0,
            1 if seo["key_facts"] else 0,
            1 if seo["faqs"] else 0,
        ])

        out.append({
            "project_id": p.id,
            "title": p.public_title or p.name,
            "slug": (p.public_slug or "").strip() or (
                str(p.public_number) if p.public_number is not None else None
            ),
            "client": p.public_client,
            "year": p.public_year,
            "completeness": score,        # 0-6
            "needs_ai_review": seo["needs_ai_review"],
            "last_ai_review_at": seo["last_ai_review_at"],
            "last_ai_review_by": seo["last_ai_review_by"],
        })
    return out


async def get_project_seo_draft_context(
    session: AsyncSession, project_id: str,
) -> Optional[dict]:
    """給 Claude / admin 生 SEO draft 用的完整 context。

    回傳：作品本身所有公開欄位 + 既有 SEO（如果有）+ admin 提示（ai_review_notes）。
    Claude 拿到後可以自己 reasoning 生 seo_title / description / narrative / faqs，
    再 PATCH 回去。
    """
    proj = await session.get(CrmProject, project_id)
    if not proj or not proj.public:
        return None

    seo_row = await session.get(WebsiteProjectSeo, project_id)
    seo = project_seo_to_dict(seo_row) if seo_row else _empty_seo_dict(project_id)

    return {
        "project_id": proj.id,
        "title": proj.public_title or proj.name,
        "client": proj.public_client,
        "year": proj.public_year,
        "youtube_id": proj.public_youtube_id,
        "description": proj.public_description,
        "credits": proj.public_credits or [],
        "credits_text": proj.public_credits_text,
        "credits_mode": proj.public_credits_mode or "block",
        "current_seo": seo,
    }
