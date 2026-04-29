"""services/website/seo_service.py
---
SEO 內容（FAQ / Testimonial / QuickFact）CRUD。

3 個實體共用同一套 list/create/update/delete 模式 — 以 `_create/_update/_delete/_list`
泛型函式接受 model class，每實體只要 4 行 thin wrapper。
"""
from __future__ import annotations

from datetime import date as _date
from typing import Any, Optional

from sqlalchemy import asc, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import WebsiteFAQ, WebsiteTestimonial, WebsiteQuickFact


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


_TO_DICT = {
    WebsiteFAQ: _faq_to_dict,
    WebsiteTestimonial: _testimonial_to_dict,
    WebsiteQuickFact: _quick_fact_to_dict,
}


def _coerce_date(value: Any) -> Any:
    """字串 ISO date → date 物件；其他直接回傳。"""
    if isinstance(value, str) and value:
        try:
            return _date.fromisoformat(value)
        except ValueError:
            return None
    return value


async def _list(session: AsyncSession, model, visible_only: bool) -> list[dict]:
    stmt = select(model).order_by(asc(model.sort_order), asc(model.id))
    if visible_only:
        stmt = stmt.where(model.visible.is_(True))
    return [_TO_DICT[model](o) for o in (await session.execute(stmt)).scalars()]


async def _create(session: AsyncSession, model, data: dict) -> dict:
    if model is WebsiteTestimonial and "date_published" in data:
        data["date_published"] = _coerce_date(data["date_published"])
    obj = model(**data)
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    return _TO_DICT[model](obj)


async def _update(session: AsyncSession, model, item_id: int, data: dict) -> Optional[dict]:
    obj = await session.get(model, item_id)
    if not obj:
        return None
    if model is WebsiteTestimonial and "date_published" in data:
        data["date_published"] = _coerce_date(data["date_published"])
    # data 來自 model_dump(exclude_unset=True)：每個 key 都是 client 主動送的
    # （null = 明確清空、值 = 更新）。NOT NULL columns 由 DB constraint 把關。
    for k, v in data.items():
        setattr(obj, k, v)
    await session.commit()
    await session.refresh(obj)
    return _TO_DICT[model](obj)


async def _delete(session: AsyncSession, model, item_id: int) -> bool:
    result = await session.execute(delete(model).where(model.id == item_id))
    await session.commit()
    return (result.rowcount or 0) > 0


# ── FAQ ──
async def list_faqs(session, visible_only=False):     return await _list(session, WebsiteFAQ, visible_only)
async def create_faq(session, data):                  return await _create(session, WebsiteFAQ, data)
async def update_faq(session, item_id, data):         return await _update(session, WebsiteFAQ, item_id, data)
async def delete_faq(session, item_id):               return await _delete(session, WebsiteFAQ, item_id)


# ── Testimonial ──
async def list_testimonials(session, visible_only=False):  return await _list(session, WebsiteTestimonial, visible_only)
async def create_testimonial(session, data):               return await _create(session, WebsiteTestimonial, data)
async def update_testimonial(session, item_id, data):      return await _update(session, WebsiteTestimonial, item_id, data)
async def delete_testimonial(session, item_id):            return await _delete(session, WebsiteTestimonial, item_id)


# ── Quick Fact ──
async def list_quick_facts(session, visible_only=False):   return await _list(session, WebsiteQuickFact, visible_only)
async def create_quick_fact(session, data):                return await _create(session, WebsiteQuickFact, data)
async def update_quick_fact(session, item_id, data):       return await _update(session, WebsiteQuickFact, item_id, data)
async def delete_quick_fact(session, item_id):             return await _delete(session, WebsiteQuickFact, item_id)
