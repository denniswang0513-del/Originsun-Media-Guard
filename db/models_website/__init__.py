"""db/models_website/ — Phase M 官網模組 SQLAlchemy Models.

Re-exports all models for easy import:
    from db.models_website import (
        WebsiteCategory, WebsiteProjectCategory, WebsiteSetting,
        WebsiteService, WebsiteContactInquiry,
    )

所有 Model 共用 db.models.Base，與既有表共享 metadata。
"""
from .category import WebsiteCategory
from .project_category import WebsiteProjectCategory
from .setting import WebsiteSetting
from .service import WebsiteService
from .inquiry import WebsiteContactInquiry

__all__ = [
    "WebsiteCategory",
    "WebsiteProjectCategory",
    "WebsiteSetting",
    "WebsiteService",
    "WebsiteContactInquiry",
]
