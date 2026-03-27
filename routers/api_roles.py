"""
api_roles.py — 角色 CRUD API（RBAC）
Endpoints:
  GET    /api/v1/roles        — 列出所有角色（公開）
  POST   /api/v1/roles        — 新增角色（admin）
  PUT    /api/v1/roles/{id}   — 修改角色（admin）
  DELETE /api/v1/roles/{id}   — 刪除角色（admin，禁刪 admin）
"""
from fastapi import APIRouter, HTTPException, Request

from core.auth import (
    check_admin, sync_role_to_json, remove_role_from_json,
    get_all_roles, find_role_by_name, find_role_by_id, load_roles_json,
)
from core.schemas import CreateRoleRequest, UpdateRoleRequest
import core.state as state

router = APIRouter(prefix="/api/v1/roles", tags=["Roles"])


async def _save_role_to_db(role_data: dict):
    """Save role to DB (upsert). Silent on failure."""
    if not state.db_online:
        return
    try:
        from db.session import get_session_factory
        factory = get_session_factory()
        if not factory:
            return
        from db.models import Role
        from sqlalchemy.dialects.postgresql import insert
        async with factory() as session:
            stmt = insert(Role).values(
                id=role_data['id'],
                name=role_data['name'],
                access_level=role_data.get('access_level', 1),
                modules=role_data.get('modules', []),
                description=role_data.get('description', ''),
            ).on_conflict_do_update(
                index_elements=['id'],
                set_={
                    'name': role_data['name'],
                    'access_level': role_data.get('access_level', 1),
                    'modules': role_data.get('modules', []),
                    'description': role_data.get('description', ''),
                }
            )
            await session.execute(stmt)
            await session.commit()
    except Exception:
        pass


async def _delete_role_from_db(role_id: int):
    """Delete role from DB. Silent on failure."""
    if not state.db_online:
        return
    try:
        from db.session import get_session_factory
        factory = get_session_factory()
        if not factory:
            return
        from db.models import Role
        from sqlalchemy import delete
        async with factory() as session:
            await session.execute(delete(Role).where(Role.id == role_id))
            await session.commit()
    except Exception:
        pass


async def _create_role_in_db(role_data: dict) -> int | None:
    """Insert a new role and return its auto-generated id."""
    if not state.db_online:
        return None
    try:
        from db.session import get_session_factory
        factory = get_session_factory()
        if not factory:
            return None
        from db.models import Role
        async with factory() as session:
            r = Role(
                name=role_data['name'],
                access_level=role_data.get('access_level', 1),
                modules=role_data.get('modules', []),
                description=role_data.get('description', ''),
            )
            session.add(r)
            await session.commit()
            await session.refresh(r)
            return r.id
    except Exception:
        return None


# ── Endpoints ──

@router.get("")
async def list_roles():
    """List all roles (public — needed for dropdowns)."""
    return await get_all_roles(order_by_level=True)


@router.post("")
async def create_role(req: CreateRoleRequest, request: Request):
    """Create a new role (admin only)."""
    check_admin(request)

    existing = await find_role_by_name(req.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"角色 '{req.name}' 已存在")

    role_data = {
        'name': req.name,
        'access_level': req.access_level,
        'modules': req.modules,
        'description': req.description,
    }

    role_id = await _create_role_in_db(role_data)
    if role_id is None:
        # JSON-only fallback: generate id
        roles = load_roles_json()
        role_id = max((r.get('id', 0) for r in roles), default=0) + 1

    role_data['id'] = role_id
    sync_role_to_json(role_data)
    return {'status': 'ok', 'id': role_id, 'name': req.name}


@router.put("/{role_id}")
async def update_role(role_id: int, req: UpdateRoleRequest, request: Request):
    """Update a role (admin only)."""
    check_admin(request)

    role = await find_role_by_id(role_id)
    if not role:
        raise HTTPException(status_code=404, detail=f"角色 id={role_id} 不存在")

    if req.name is not None:
        # Check for name conflict
        conflict = await find_role_by_name(req.name)
        if conflict and conflict['id'] != role_id:
            raise HTTPException(status_code=409, detail=f"角色名稱 '{req.name}' 已被使用")
        role['name'] = req.name
    if req.access_level is not None:
        role['access_level'] = req.access_level
    if req.modules is not None:
        role['modules'] = req.modules
    if req.description is not None:
        role['description'] = req.description

    await _save_role_to_db(role)
    sync_role_to_json(role)
    return {'status': 'ok'}


@router.delete("/{role_id}")
async def delete_role(role_id: int, request: Request):
    """Delete a role (admin only). Cannot delete the 'admin' role."""
    check_admin(request)

    role = await find_role_by_id(role_id)
    if not role:
        raise HTTPException(status_code=404, detail=f"角色 id={role_id} 不存在")
    if role['name'] == 'admin':
        raise HTTPException(status_code=400, detail="不能刪除 admin 角色")

    # Check if any users are assigned to this role
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import select, func as sa_func
                from db.models import User
                async with factory() as session:
                    count = (await session.execute(
                        select(sa_func.count()).select_from(User).where(User.role_id == role_id)
                    )).scalar() or 0
                    if count > 0:
                        raise HTTPException(
                            status_code=400,
                            detail=f"有 {count} 個使用者仍在使用此角色，請先重新指派"
                        )
        except HTTPException:
            raise
        except Exception:
            pass

    await _delete_role_from_db(role_id)
    remove_role_from_json(role_id)
    return {'status': 'ok'}
