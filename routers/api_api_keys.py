"""API Key management endpoints — CRUD for programmatic access keys."""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

router = APIRouter()

MAX_KEYS_PER_USER = 10


# ── Schemas ──

class CreateKeyRequest(BaseModel):
    name: str
    expires_days: Optional[int] = None  # null = 永不過期


# ── Helpers ──

def _require_auth(request: Request) -> dict:
    """Require authenticated user (JWT or API Key). Returns payload."""
    try:
        from core.auth import _extract_token
        payload = _extract_token(request)
    except Exception:
        payload = None
    if not payload:
        raise HTTPException(status_code=401, detail="未登入或 token 已過期")
    return payload


def _require_admin(request: Request) -> dict:
    """Require admin user."""
    try:
        from core.auth import check_admin
        return check_admin(request)
    except ImportError:
        raise HTTPException(status_code=501, detail="Auth module not available")


def _key_to_safe_dict(k: dict) -> dict:
    """Convert key record to response dict. Includes raw_key for internal tool use."""
    return {
        'id': k.get('id'),
        'key_prefix': k.get('key_prefix', ''),
        'raw_key': k.get('raw_key', ''),
        'name': k.get('name', ''),
        'username': k.get('username', ''),
        'created_at': k.get('created_at', ''),
        'expires_at': k.get('expires_at'),
        'last_used_at': k.get('last_used_at'),
        'is_active': k.get('is_active', True),
    }


async def _get_all_keys() -> list:
    """Get all API keys. Always reads from JSON (which has raw_key).
    JSON is the primary source since it stores raw_key; DB is for queries only."""
    from core.auth import load_api_keys_json
    return load_api_keys_json()


async def _persist_key(key_data: dict):
    """Write key to DB + JSON (dual-write)."""
    from core.auth import sync_api_key_to_json
    sync_api_key_to_json(key_data)

    import core.state as state
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from db.models import ApiKey
                async with factory() as session:
                    obj = ApiKey(
                        key_hash=key_data['key_hash'],
                        key_prefix=key_data['key_prefix'],
                        name=key_data['name'],
                        username=key_data['username'],
                        expires_at=datetime.fromisoformat(key_data['expires_at']) if key_data.get('expires_at') else None,
                        is_active=key_data.get('is_active', True),
                    )
                    session.add(obj)
                    await session.commit()
                    await session.refresh(obj)
                    # Update JSON with DB-assigned id
                    key_data['id'] = obj.id
                    sync_api_key_to_json(key_data)
        except Exception:
            pass


async def _deactivate_key(key_id: int, username: str, is_admin: bool):
    """Deactivate (soft-delete) a key by id."""
    # JSON update
    from core.auth import load_api_keys_json, save_api_keys_json
    keys = load_api_keys_json()
    found = False
    for k in keys:
        if k.get('id') == key_id:
            if not is_admin and k.get('username') != username:
                raise HTTPException(status_code=403, detail="只能撤銷自己的 API Key")
            k['is_active'] = False
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    save_api_keys_json(keys)

    # DB update
    import core.state as state
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import update
                from db.models import ApiKey
                async with factory() as session:
                    await session.execute(
                        update(ApiKey).where(ApiKey.id == key_id).values(is_active=False)
                    )
                    await session.commit()
        except Exception:
            pass


async def _hard_delete_key(key_id: int, username: str, is_admin: bool):
    """Permanently remove a key from DB + JSON."""
    from core.auth import load_api_keys_json, remove_api_key_from_json
    keys = load_api_keys_json()
    found = next((k for k in keys if k.get('id') == key_id), None)
    if not found:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    if not is_admin and found.get('username') != username:
        raise HTTPException(status_code=403, detail="只能刪除自己的 API Key")

    remove_api_key_from_json(key_id)

    import core.state as state
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import delete as sa_delete
                from db.models import ApiKey
                async with factory() as session:
                    await session.execute(sa_delete(ApiKey).where(ApiKey.id == key_id))
                    await session.commit()
        except Exception:
            pass


async def _rename_key(key_id: int, new_name: str, username: str, is_admin: bool):
    """Rename a key in DB + JSON."""
    from core.auth import load_api_keys_json, save_api_keys_json
    keys = load_api_keys_json()
    found = False
    for k in keys:
        if k.get('id') == key_id:
            if not is_admin and k.get('username') != username:
                raise HTTPException(status_code=403, detail="只能修改自己的 API Key")
            k['name'] = new_name
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    save_api_keys_json(keys)

    import core.state as state
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import update
                from db.models import ApiKey
                async with factory() as session:
                    await session.execute(
                        update(ApiKey).where(ApiKey.id == key_id).values(name=new_name)
                    )
                    await session.commit()
        except Exception:
            pass


async def _set_key_active(key_id: int, active: bool, username: str, is_admin: bool):
    """Enable or disable a key in DB + JSON."""
    from core.auth import load_api_keys_json, save_api_keys_json
    keys = load_api_keys_json()
    found = False
    for k in keys:
        if k.get('id') == key_id:
            if not is_admin and k.get('username') != username:
                raise HTTPException(status_code=403, detail="只能修改自己的 API Key")
            k['is_active'] = active
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    save_api_keys_json(keys)

    import core.state as state
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import update
                from db.models import ApiKey
                async with factory() as session:
                    await session.execute(
                        update(ApiKey).where(ApiKey.id == key_id).values(is_active=active)
                    )
                    await session.commit()
        except Exception:
            pass


async def _delete_keys_by_username(username: str):
    """Delete all API keys for a user (when user is deleted)."""
    from core.auth import remove_api_keys_by_username_json
    remove_api_keys_by_username_json(username)

    import core.state as state
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import delete
                from db.models import ApiKey
                async with factory() as session:
                    await session.execute(
                        delete(ApiKey).where(ApiKey.username == username)
                    )
                    await session.commit()
        except Exception:
            pass


def _next_json_id() -> int:
    """Get next available ID for JSON-only storage."""
    from core.auth import load_api_keys_json
    keys = load_api_keys_json()
    if not keys:
        return 1
    return max(k.get('id', 0) for k in keys) + 1


# ── Endpoints ──

@router.post("/api/v1/api_keys")
async def create_api_key(req: CreateKeyRequest, request: Request):
    """Create a new API key. Returns the raw key ONCE — it cannot be retrieved later."""
    payload = _require_auth(request)
    username = payload.get('sub', '')

    # Check per-user limit
    all_keys = await _get_all_keys()
    user_active = [k for k in all_keys if k.get('username') == username and k.get('is_active', True)]
    if len(user_active) >= MAX_KEYS_PER_USER:
        raise HTTPException(status_code=400, detail=f"每個使用者最多 {MAX_KEYS_PER_USER} 把有效 API Key")

    # Generate key
    from core.auth import generate_api_key, hash_api_key
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    key_prefix = raw_key[:8] + '****'

    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    expires_at = None
    if req.expires_days and req.expires_days > 0:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=req.expires_days)).isoformat(timespec='seconds')

    key_data = {
        'id': _next_json_id(),
        'key_hash': key_hash,
        'key_prefix': key_prefix,
        'raw_key': raw_key,
        'name': req.name.strip()[:64] or 'Unnamed',
        'username': username,
        'created_at': now,
        'expires_at': expires_at,
        'last_used_at': None,
        'is_active': True,
    }

    await _persist_key(key_data)

    return {
        'key': raw_key,  # Only returned ONCE
        'id': key_data['id'],
        'key_prefix': key_prefix,
        'name': key_data['name'],
        'username': username,
        'expires_at': expires_at,
    }


@router.get("/api/v1/api_keys")
async def list_my_keys(request: Request):
    """List current user's API keys (no hashes exposed)."""
    payload = _require_auth(request)
    username = payload.get('sub', '')
    all_keys = await _get_all_keys()
    my_keys = [_key_to_safe_dict(k) for k in all_keys if k.get('username') == username]
    return {'keys': my_keys}


@router.get("/api/v1/api_keys/all")
async def list_all_keys(request: Request):
    """List all API keys across all users (admin only)."""
    _require_admin(request)
    all_keys = await _get_all_keys()
    return {'keys': [_key_to_safe_dict(k) for k in all_keys]}


@router.delete("/api/v1/api_keys/{key_id}")
async def revoke_or_delete_key(key_id: int, request: Request, permanent: bool = False):
    """Revoke (deactivate) or permanently delete an API key."""
    payload = _require_auth(request)
    username = payload.get('sub', '')
    is_admin = payload.get('access_level', 0) >= 3

    if permanent:
        await _hard_delete_key(key_id, username, is_admin)
        return {'status': 'deleted', 'id': key_id}
    else:
        await _deactivate_key(key_id, username, is_admin)
        return {'status': 'revoked', 'id': key_id}


class UpdateKeyRequest(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None


@router.patch("/api/v1/api_keys/{key_id}")
async def update_key(key_id: int, req: UpdateKeyRequest, request: Request):
    """Update an API key (rename and/or enable/disable)."""
    payload = _require_auth(request)
    username = payload.get('sub', '')
    is_admin = payload.get('access_level', 0) >= 3

    if req.name is not None:
        new_name = req.name.strip()[:64]
        if not new_name:
            raise HTTPException(status_code=400, detail="名稱不能為空")
        await _rename_key(key_id, new_name, username, is_admin)

    if req.is_active is not None:
        await _set_key_active(key_id, req.is_active, username, is_admin)

    return {'status': 'ok', 'id': key_id}
