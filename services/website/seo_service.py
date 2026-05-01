"""services/website/seo_service.py
---
SEO 內容（FAQ / Testimonial / QuickFact）CRUD。

3 個實體共用同一套 list/create/update/delete 模式 — 以 `_create/_update/_delete/_list`
泛型函式接受 model class，每實體只要 4 行 thin wrapper。
"""
from __future__ import annotations

from datetime import date as _date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import WebsiteFAQ, WebsiteTestimonial, WebsiteQuickFact
from . import _crud_base as _crud


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
