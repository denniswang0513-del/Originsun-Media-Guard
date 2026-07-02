"""routers/website/ — Phase M 官網模組路由.

此 package 的路由 ONLY 掛載在 NAS website-api container（`main_website.py`），
Windows 的 `main.py` 不掛載（網站邏輯 100% 在 NAS）。

使用：
    from routers.website import router as website_router
    app.include_router(website_router)
"""
from fastapi import APIRouter

from .public import router as _public_router
from .admin_works import router as _admin_works_router
from .admin_categories import router as _admin_categories_router
from .admin_services import router as _admin_services_router
from .admin_inquiries import router as _admin_inquiries_router
from .admin_settings import router as _admin_settings_router
from .admin_rebuild import router as _admin_rebuild_router
from .admin_seo import router as _admin_seo_router
from .admin_credits import router as _admin_credits_router
from .admin_posts import router as _admin_posts_router
from .admin_team import router as _admin_team_router
from .admin_initiatives import router as _admin_initiatives_router
from .admin_redirects import router as _admin_redirects_router
from .admin_translation import router as _admin_translation_router

router = APIRouter()
router.include_router(_public_router)
router.include_router(_admin_works_router)
router.include_router(_admin_categories_router)
router.include_router(_admin_services_router)
router.include_router(_admin_inquiries_router)
router.include_router(_admin_settings_router)
router.include_router(_admin_rebuild_router)
router.include_router(_admin_seo_router)
router.include_router(_admin_credits_router)
router.include_router(_admin_posts_router)
router.include_router(_admin_team_router)
router.include_router(_admin_initiatives_router)
router.include_router(_admin_redirects_router)
router.include_router(_admin_translation_router)

__all__ = ["router"]
