"""routers/website/admin_redirects.py
---
管理端：Legacy 頁面級 301 轉址 CRUD（prefix /api/website/admin）。

補「舊站頁面 → 新站頁面」這種非作品/文章 slug 變動的轉址（作品換 slug 走
works.public_old_slugs、文章走 posts.old_urls）。任一寫入 mark_dirty → 重建。
合併後的完整轉址 map 在 public.py 的 /redirects；sync_redirects_to_nas() 撈那個
生成 nginx（含帶/不帶結尾斜線兩種變體）。

注意：POST /redirects（新增）與 admin_posts 的 POST /redirects/sync（強制重推 nginx）
路徑不衝突；PUT/DELETE /redirects/{item_id} 的 item_id 是 int，不會吃到字面的 /sync。
"""
from __future__ import annotations

from fastapi import APIRouter

from ._common import register_crud
from core.schemas_website import WebsiteRedirectCreate, WebsiteRedirectUpdate
from services.website import redirect_service, rebuild_service

router = APIRouter(prefix="/api/website/admin", tags=["website-admin-redirects"])

register_crud(
    router,
    prefix="redirects", name="Redirect",
    list_fn=redirect_service.list_admin,
    create_fn=redirect_service.create_redirect,
    update_fn=redirect_service.update_redirect,
    delete_fn=redirect_service.delete_redirect,
    create_schema=WebsiteRedirectCreate,
    update_schema=WebsiteRedirectUpdate,
    on_change=rebuild_service.mark_dirty,
)
