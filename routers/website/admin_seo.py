"""routers/website/admin_seo.py
---
管理端：SEO 內容 CRUD（FAQ / Testimonial / QuickFact）。

對應前端「🔍 SEO / AI SEO 管理」子視圖的 Card 2/3/4。
任一寫入觸發 rebuild_service.mark_dirty()，60s debounce 後重 build 對外網站。
"""
from __future__ import annotations

from fastapi import APIRouter

from ._common import register_crud
from core.schemas_website import (
    WebsiteFAQCreate, WebsiteFAQUpdate,
    WebsiteTestimonialCreate, WebsiteTestimonialUpdate,
    WebsiteQuickFactCreate, WebsiteQuickFactUpdate,
)
from services.website import rebuild_service, seo_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-seo"])


register_crud(
    router,
    prefix="faqs", name="FAQ",
    list_fn=seo_service.list_faqs,
    create_fn=seo_service.create_faq,
    update_fn=seo_service.update_faq,
    delete_fn=seo_service.delete_faq,
    create_schema=WebsiteFAQCreate,
    update_schema=WebsiteFAQUpdate,
    on_change=rebuild_service.mark_dirty,
)

register_crud(
    router,
    prefix="testimonials", name="Testimonial",
    list_fn=seo_service.list_testimonials,
    create_fn=seo_service.create_testimonial,
    update_fn=seo_service.update_testimonial,
    delete_fn=seo_service.delete_testimonial,
    create_schema=WebsiteTestimonialCreate,
    update_schema=WebsiteTestimonialUpdate,
    on_change=rebuild_service.mark_dirty,
)

register_crud(
    router,
    prefix="quick_facts", name="Quick fact",
    list_fn=seo_service.list_quick_facts,
    create_fn=seo_service.create_quick_fact,
    update_fn=seo_service.update_quick_fact,
    delete_fn=seo_service.delete_quick_fact,
    create_schema=WebsiteQuickFactCreate,
    update_schema=WebsiteQuickFactUpdate,
    on_change=rebuild_service.mark_dirty,
)
