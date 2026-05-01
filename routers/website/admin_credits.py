"""routers/website/admin_credits.py
---
管理端：演職員職位庫 + 模板 CRUD。

無 rebuild_service.mark_dirty —— credits 不直接出現在 Astro build 結果，
是被 work 的 public_credits 引用，rebuild 由作品儲存時各自觸發。

對應前端「🎭 演職員管理」子視圖的 2 個 CRUD 卡片：
- 職位庫（中英對照、排序、可見性、usage_count）
- 模板（name/description/role_ids；list 已 hydrate roles 詳情）
"""
from __future__ import annotations

from fastapi import APIRouter

from ._common import register_crud
from core.schemas_website import (
    CreditRoleCreate, CreditRoleUpdate,
    CreditTemplateCreate, CreditTemplateUpdate,
)
from services.website import credit_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-credits"])


register_crud(
    router,
    prefix="credit_roles", name="Credit role",
    list_fn=credit_service.list_roles,
    create_fn=credit_service.create_role,
    update_fn=credit_service.update_role,
    delete_fn=credit_service.delete_role,
    create_schema=CreditRoleCreate,
    update_schema=CreditRoleUpdate,
)

register_crud(
    router,
    prefix="credit_templates", name="Credit template",
    list_fn=credit_service.list_templates,
    create_fn=credit_service.create_template,
    update_fn=credit_service.update_template,
    delete_fn=credit_service.delete_template,
    create_schema=CreditTemplateCreate,
    update_schema=CreditTemplateUpdate,
)
