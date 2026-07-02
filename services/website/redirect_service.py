"""services/website/redirect_service.py
---
Legacy 頁面級 301 轉址 CRUD + 合併用 map。

from_path / to_path 一律正規化（strip 網域、去結尾斜線、拒絕含空白/引號/控制字元），
跟 post_service._normalize_old_url 行為一致，確保存進去的 key 跟 works/posts 的
redirect key 同格式，nginx 生成端才好統一處理。
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import WebsiteRedirect
from . import _crud_base as _crud


def _normalize_path(raw: str) -> Optional[str]:
    """正規化成相對路徑（無結尾斜線）。無效回 None。"""
    s = (raw or "").strip()
    if not s:
        return None
    if s.startswith("http://") or s.startswith("https://"):
        try:
            s = urlparse(s).path or "/"
        except Exception:
            return None
    if not s.startswith("/"):
        s = "/" + s
    if len(s) > 1 and s.endswith("/"):
        s = s.rstrip("/")
    if re.search(r"[\s\"'\x00-\x1f]", s):
        return None
    return s


def _to_dict(o: WebsiteRedirect) -> dict[str, Any]:
    return {
        "id": o.id,
        "from_path": o.from_path,
        "to_path": o.to_path,
        "note": o.note,
        "sort_order": o.sort_order,
        "visible": o.visible,
    }


def _pre_write(data: dict) -> dict:
    """正規化 from_path / to_path（若提供）。normalize 失敗則保留原值交給 DB 約束把關。"""
    for key in ("from_path", "to_path"):
        if data.get(key):
            n = _normalize_path(data[key])
            if n:
                data[key] = n
    return data


# ── CRUD（admin，走 register_crud）──

async def create_redirect(session: AsyncSession, data: dict) -> dict:
    return await _crud.create_item(session, WebsiteRedirect, data, _to_dict, pre_write=_pre_write)


async def update_redirect(session: AsyncSession, item_id: int, data: dict) -> Optional[dict]:
    return await _crud.update_item(session, WebsiteRedirect, item_id, data, _to_dict, pre_write=_pre_write)


async def delete_redirect(session: AsyncSession, item_id: int) -> bool:
    return await _crud.delete_item(session, WebsiteRedirect, item_id)


async def list_admin(session: AsyncSession) -> list[dict]:
    return await _crud.list_items(session, WebsiteRedirect, _to_dict)


# ── 合併用（給 public.py /redirects）──

async def list_redirects(session: AsyncSession) -> dict[str, str]:
    """visible 的 legacy 轉址 → {from_path: to_path}。"""
    stmt = (
        select(WebsiteRedirect)
        .where(WebsiteRedirect.visible.is_(True))
        .order_by(asc(WebsiteRedirect.sort_order), asc(WebsiteRedirect.id))
    )
    out: dict[str, str] = {}
    for o in (await session.execute(stmt)).scalars():
        if o.from_path and o.to_path:
            out[o.from_path] = o.to_path
    return out


# ── 批次 upsert（seed / 匯入用；依 from_path 去重覆寫）──

async def upsert_many(session: AsyncSession, rows: list[dict]) -> dict:
    """rows: [{"from_path","to_path","note"?}]。依 from_path upsert。回 {created, updated, skipped}。"""
    created = updated = skipped = 0
    for r in rows:
        nf = _normalize_path(r.get("from_path", ""))
        nt = _normalize_path(r.get("to_path", ""))
        if not nf or not nt:
            skipped += 1
            continue
        existing = (await session.execute(
            select(WebsiteRedirect).where(WebsiteRedirect.from_path == nf)
        )).scalar_one_or_none()
        if existing:
            existing.to_path = nt
            if r.get("note"):
                existing.note = r["note"]
            updated += 1
        else:
            session.add(WebsiteRedirect(from_path=nf, to_path=nt, note=r.get("note")))
            created += 1
    await session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}
