"""services/website/nav_service.py
---
頂部導覽選單 CRUD — 走 _crud_base 泛型（同 seo_service 的 thin-wrapper 模式）。

公開端 list_nav(visible_only=True) 給 Header.astro fetch；admin 走 register_crud。
排序 / 顯示隱藏 / 改名都只是 update sort_order / visible / label_*，
不需要獨立 reorder endpoint（前端編 sort_order 數字欄位直接存）。
"""
from __future__ import annotations

from typing import Any

from db.models_website import WebsiteNavItem
from . import _crud_base as _crud


def _nav_to_dict(o: WebsiteNavItem) -> dict[str, Any]:
    return {
        "id": o.id,
        "label_zh": o.label_zh,
        "label_en": o.label_en,
        "href": o.href,
        "sort_order": o.sort_order,
        "visible": o.visible,
    }


async def list_nav(session, visible_only=False):
    return await _crud.list_items(session, WebsiteNavItem, _nav_to_dict, visible_only=visible_only)


async def create_nav(session, data):
    return await _crud.create_item(session, WebsiteNavItem, data, _nav_to_dict)


async def update_nav(session, item_id, data):
    return await _crud.update_item(session, WebsiteNavItem, item_id, data, _nav_to_dict)


async def delete_nav(session, item_id):
    return await _crud.delete_item(session, WebsiteNavItem, item_id)
