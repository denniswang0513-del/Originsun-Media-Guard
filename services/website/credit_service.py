"""services/website/credit_service.py
---
演職員職位庫 + 模板 CRUD。

Roles 跟 SEO 三表類似 — 走泛型 _create/_update/_delete/_list；
Templates 額外要 hydrate role 詳情、計算 usage_count。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import asc, delete, select, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import WebsiteCreditRole, WebsiteCreditTemplate


def _role_to_dict(o: WebsiteCreditRole, usage: int = 0) -> dict:
    return {
        "id": o.id,
        "name_zh": o.name_zh,
        "name_en": o.name_en,
        "sort_order": o.sort_order,
        "visible": o.visible,
        "usage_count": usage,
    }


async def list_roles(session: AsyncSession, visible_only: bool = False) -> list[dict]:
    stmt = select(WebsiteCreditRole).order_by(
        asc(WebsiteCreditRole.sort_order), asc(WebsiteCreditRole.id)
    )
    if visible_only:
        stmt = stmt.where(WebsiteCreditRole.visible.is_(True))
    rows = list((await session.execute(stmt)).scalars())

    # usage_count: 撈所有 crm_project_showcase.credits，數 role_id 出現次數
    # （JSONB array of blocks，每 block 的 role_id 算一次）。
    # visible_only=True 走公開 endpoint，不 join 不必要的統計。
    usage_map = {} if visible_only else await _compute_role_usage(session)
    return [_role_to_dict(r, usage_map.get(r.id, 0)) for r in rows]


async def find_projects_by_staff(session: AsyncSession, staff_id: str) -> list[dict]:
    """從所有 showcase.credits.entries 反查含此 staff_id 的位置。

    回傳 [{project_id, role_zh, duty}, ...]（無分組，由 caller 自己 group by project_id）。
    若 PG 不可用 / 表不存在，靜默回傳 []。
    """
    sql = text("""
        SELECT sc.id AS project_id,
               block->>'name_zh' AS role_zh,
               entry->>'duty' AS duty
        FROM crm_project_showcase sc,
             jsonb_array_elements(sc.credits) AS block,
             jsonb_array_elements(block->'entries') AS entry
        WHERE sc.credits IS NOT NULL
          AND jsonb_typeof(sc.credits) = 'array'
          AND entry ? 'staff_id'
          AND (entry->>'staff_id') ~ '^[0-9a-f]+$'
          AND entry->>'staff_id' = :staff_id
    """)
    try:
        rows = (await session.execute(sql, {"staff_id": staff_id})).mappings()
        return [{"project_id": r["project_id"], "role_zh": r["role_zh"] or "",
                 "duty": r["duty"] or ""} for r in rows]
    except (OperationalError, ProgrammingError):
        return []


async def cleanup_staff_id_from_credits(session: AsyncSession, staff_id: str) -> int:
    """刪 staff 時的 cascade — 從所有 showcase.credits.entries 清除此 staff_id 引用。

    保留 entry.name snapshot（不刪整個 entry），但移除 staff_id 跟 resume_url。
    回傳影響的 showcase 筆數（caller 統一 commit）。
    """
    from db.models import CrmProjectShowcase
    rows = (await session.execute(text("""
        SELECT DISTINCT sc.id FROM crm_project_showcase sc,
            jsonb_array_elements(sc.credits) block,
            jsonb_array_elements(block->'entries') entry
        WHERE jsonb_typeof(sc.credits) = 'array'
          AND entry ? 'staff_id'
          AND entry->>'staff_id' = :sid
    """), {"sid": staff_id})).all()
    affected = 0
    for (sc_id,) in rows:
        sc = await session.get(CrmProjectShowcase, sc_id)
        if not sc or not sc.credits:
            continue
        new_credits = []
        for block in sc.credits:
            new_block = dict(block)
            new_block["entries"] = [
                {k: v for k, v in entry.items() if k not in ("staff_id", "resume_url")}
                if entry.get("staff_id") == staff_id else entry
                for entry in (block.get("entries") or [])
            ]
            new_credits.append(new_block)
        sc.credits = new_credits
        affected += 1
    return affected


async def _compute_role_usage(session: AsyncSession) -> dict[int, int]:
    """掃所有 showcase.credits（block 結構），count role_id 出現次數。

    Block 結構 [{role_id, ...}]，flat 結構 [{role, name}] 沒有 role_id 不算。
    若 PostgreSQL 不可用或 crm_project_showcase 表還沒有 credits 欄位，
    靜默回傳空 dict（admin UI 只是少顯示 usage_count，不影響功能）。
    """
    sql = text("""
        SELECT (block->>'role_id')::int AS role_id, COUNT(*) AS cnt
        FROM crm_project_showcase sc, jsonb_array_elements(sc.credits) AS block
        WHERE sc.credits IS NOT NULL
          AND jsonb_typeof(sc.credits) = 'array'
          AND block ? 'role_id'
          AND (block->>'role_id') ~ '^[0-9]+$'
        GROUP BY (block->>'role_id')::int
    """)
    try:
        result = await session.execute(sql)
        return {row.role_id: row.cnt for row in result}
    except (OperationalError, ProgrammingError):
        # 表不存在 / jsonb 不可用：靜默；其他例外讓上層感知
        return {}


async def create_role(session: AsyncSession, data: dict) -> dict:
    from . import _crud_base as _crud
    return await _crud.create_item(session, WebsiteCreditRole, data, _role_to_dict)


async def update_role(session: AsyncSession, item_id: int, data: dict) -> Optional[dict]:
    from . import _crud_base as _crud
    return await _crud.update_item(session, WebsiteCreditRole, item_id, data, _role_to_dict)


async def delete_role(session: AsyncSession, item_id: int) -> bool:
    # Cascade: 從所有 templates.role_ids 移除此 role_id（避免 dead reference）。
    # jsonb_typeof = 'number' 守門：避免資料壞掉時 elem::int 在非 number 元素 raise。
    await session.execute(text("""
        UPDATE website_credit_templates
        SET role_ids = COALESCE((
            SELECT jsonb_agg(elem)
            FROM jsonb_array_elements(role_ids) elem
            WHERE jsonb_typeof(elem) = 'number' AND elem::int <> :rid
        ), '[]'::jsonb)
        WHERE role_ids @> jsonb_build_array(:rid)
    """), {"rid": item_id})
    result = await session.execute(
        delete(WebsiteCreditRole).where(WebsiteCreditRole.id == item_id)
    )
    await session.commit()
    return (result.rowcount or 0) > 0


# ── Templates ──

def _template_to_dict(o: WebsiteCreditTemplate, role_lookup: dict[int, dict]) -> dict:
    role_ids = list(o.role_ids or [])
    roles = [role_lookup[rid] for rid in role_ids if rid in role_lookup]
    return {
        "id": o.id,
        "name": o.name,
        "description": o.description,
        "role_ids": role_ids,
        "roles": roles,
        "sort_order": o.sort_order,
    }


async def _role_lookup_all(session: AsyncSession) -> dict[int, dict]:
    """全表 lookup — 給 list_templates 用（要 hydrate N 個 template）。"""
    rows = list((await session.execute(select(WebsiteCreditRole))).scalars())
    return {
        r.id: {"id": r.id, "name_zh": r.name_zh, "name_en": r.name_en}
        for r in rows
    }


async def _role_lookup_for_ids(session: AsyncSession, ids: list[int]) -> dict[int, dict]:
    """僅 hydrate 指定 role_ids — 單一 template mutation/hydrate 用。

    避免每次 mutation 後撈整張 roles 表（職位庫長到 100+ 時尤其值得）。
    """
    if not ids:
        return {}
    rows = list((await session.execute(
        select(WebsiteCreditRole).where(WebsiteCreditRole.id.in_(ids))
    )).scalars())
    return {
        r.id: {"id": r.id, "name_zh": r.name_zh, "name_en": r.name_en}
        for r in rows
    }


async def list_templates(session: AsyncSession) -> list[dict]:
    role_lookup = await _role_lookup_all(session)
    rows = list((await session.execute(
        select(WebsiteCreditTemplate).order_by(
            asc(WebsiteCreditTemplate.sort_order), asc(WebsiteCreditTemplate.id)
        )
    )).scalars())
    return [_template_to_dict(t, role_lookup) for t in rows]


async def create_template(session: AsyncSession, data: dict) -> dict:
    obj = WebsiteCreditTemplate(**data)
    session.add(obj)
    await session.commit()
    await session.refresh(obj)
    role_lookup = await _role_lookup_for_ids(session, list(obj.role_ids or []))
    return _template_to_dict(obj, role_lookup)


async def update_template(session: AsyncSession, item_id: int, data: dict) -> Optional[dict]:
    obj = await session.get(WebsiteCreditTemplate, item_id)
    if not obj:
        return None
    for k, v in data.items():
        setattr(obj, k, v)
    await session.commit()
    await session.refresh(obj)
    role_lookup = await _role_lookup_for_ids(session, list(obj.role_ids or []))
    return _template_to_dict(obj, role_lookup)


async def delete_template(session: AsyncSession, item_id: int) -> bool:
    from . import _crud_base as _crud
    return await _crud.delete_item(session, WebsiteCreditTemplate, item_id)


