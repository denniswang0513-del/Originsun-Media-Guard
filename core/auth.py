"""
Auth utilities — JWT token + password hashing + role decorator.
Uses stdlib only (no bcrypt dependency).
"""
import hashlib
import hmac
import json
import os
import time
import secrets
from functools import wraps
from typing import Optional

from fastapi import Request, HTTPException


# ── Password Hashing (stdlib pbkdf2) ──

def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    """Hash password with PBKDF2-SHA256. Returns 'salt$hash' string."""
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100_000)
    return salt.hex() + '$' + dk.hex()


def verify_password(password: str, stored: str) -> bool:
    """Verify password against 'salt$hash' string."""
    try:
        salt_hex, hash_hex = stored.split('$', 1)
        salt = bytes.fromhex(salt_hex)
        dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 100_000)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ── JWT Token (minimal, stdlib-based) ──

def _get_secret() -> str:
    """Get or create JWT secret from settings.json."""
    try:
        from config import load_settings, save_settings
        settings = load_settings()
        secret = settings.get('jwt_secret', '')
        if not secret:
            secret = secrets.token_hex(32)
            settings['jwt_secret'] = secret
            save_settings(settings)
        return secret
    except Exception:
        return 'originsun-fallback-secret-key'


def _b64url_encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _b64url_decode(s: str) -> bytes:
    import base64
    padding = 4 - len(s) % 4
    if padding != 4:
        s += '=' * padding
    return base64.urlsafe_b64decode(s)


def create_token(payload: dict, expires_days: int = 7) -> str:
    """Create a JWT-like token (HS256)."""
    secret = _get_secret()
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload['exp'] = int(time.time()) + expires_days * 86400
    payload['iat'] = int(time.time())
    body = _b64url_encode(json.dumps(payload).encode())
    signature = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
    sig = _b64url_encode(signature)
    return f"{header}.{body}.{sig}"


def verify_token(token: str) -> Optional[dict]:
    """Verify token and return payload, or None if invalid/expired."""
    try:
        secret = _get_secret()
        parts = token.split('.')
        if len(parts) != 3:
            return None
        header, body, sig = parts
        expected = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_encode(expected), sig):
            return None
        payload = json.loads(_b64url_decode(body))
        if payload.get('exp', 0) < time.time():
            return None
        return payload
    except Exception:
        return None


# ── Constants ──

LEGACY_ROLE_LEVELS = {'admin': 3, 'editor': 1, 'viewer': 0}


# ── Role Decorator ──

def _extract_token(request: Request) -> Optional[dict]:
    """Extract and verify token from Authorization header."""
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return verify_token(auth[7:])
    return None


def check_admin(request: Request):
    """Check admin permission. Raises 401/403 if not admin.
    Supports both new RBAC tokens (access_level) and legacy tokens (role string).
    """
    payload = _extract_token(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="未登入或 token 已過期")
    # New RBAC: check access_level >= 3
    if payload.get('access_level', -1) >= 3:
        return payload
    # Legacy fallback: check role string
    if payload.get('role') == 'admin':
        return payload
    raise HTTPException(status_code=403, detail="權限不足")


def require_role(*roles: str):
    """FastAPI dependency: require authenticated user with specified role.
    Supports both role_name (new RBAC) and role (legacy) fields.
    """
    from fastapi import Depends

    async def _check(request: Request):
        payload = _extract_token(request)
        if not payload:
            raise HTTPException(status_code=401, detail="未登入或 token 已過期")
        user_role = payload.get('role_name') or payload.get('role')
        if user_role not in roles:
            raise HTTPException(status_code=403, detail="權限不足")
        return payload

    return Depends(_check)


def require_access_level(min_level: int):
    """FastAPI dependency: require authenticated user with access_level >= min_level."""
    from fastapi import Depends

    async def _check(request: Request):
        payload = _extract_token(request)
        if not payload:
            raise HTTPException(status_code=401, detail="未登入或 token 已過期")
        level = payload.get('access_level', 0)
        # Legacy fallback
        if level == 0 and 'access_level' not in payload:
            legacy = payload.get('role', '')
            level = LEGACY_ROLE_LEVELS.get(legacy, 0)
        if level < min_level:
            raise HTTPException(status_code=403, detail="權限不足")
        return payload

    return Depends(_check)


# ── Generic JSON fallback helpers ──

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_USERS_JSON = os.path.join(_BASE_DIR, 'users.json')
_ROLES_JSON = os.path.join(_BASE_DIR, 'roles.json')


def _load_json(path: str) -> list:
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save_json(path: str, data: list):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _sync_to_json(path: str, item: dict, key: str):
    items = _load_json(path)
    idx = next((i for i, x in enumerate(items) if x.get(key) == item[key]), None)
    if idx is not None:
        items[idx] = item
    else:
        items.append(item)
    _save_json(path, items)


def _remove_from_json(path: str, key: str, value):
    items = _load_json(path)
    _save_json(path, [x for x in items if x.get(key) != value])


# ── Users JSON (public API, delegates to generic helpers) ──

def load_users_json() -> list:
    return _load_json(_USERS_JSON)

def save_users_json(users: list):
    _save_json(_USERS_JSON, users)

def sync_user_to_json(user_data: dict):
    _sync_to_json(_USERS_JSON, user_data, 'username')

def remove_user_from_json(username: str):
    _remove_from_json(_USERS_JSON, 'username', username)


# ── Roles JSON (public API, delegates to generic helpers) ──

def load_roles_json() -> list:
    return _load_json(_ROLES_JSON)

def save_roles_json(roles: list):
    _save_json(_ROLES_JSON, roles)

def sync_role_to_json(role_data: dict):
    _sync_to_json(_ROLES_JSON, role_data, 'id')

def remove_role_from_json(role_id: int):
    _remove_from_json(_ROLES_JSON, 'id', role_id)


# ── Shared async role query helpers (used by api_auth + api_roles) ──

def _role_to_dict(r) -> dict:
    """Convert a Role ORM object to a serializable dict."""
    return {'id': r.id, 'name': r.name, 'access_level': r.access_level,
            'modules': r.modules or [], 'description': r.description or ''}


async def get_all_roles(*, order_by_level: bool = False) -> list:
    """Get roles from DB or JSON fallback. Shared by api_auth and api_roles."""
    import core.state as state
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import select
                from db.models import Role
                async with factory() as session:
                    stmt = select(Role)
                    if order_by_level:
                        stmt = stmt.order_by(Role.access_level.desc(), Role.id)
                    result = await session.execute(stmt)
                    return [_role_to_dict(r) for r in result.scalars().all()]
        except Exception:
            pass
    return load_roles_json()


async def find_role_by_name(name: str):
    """Find a single role by name. Returns dict or None."""
    roles = await get_all_roles()
    return next((r for r in roles if r['name'] == name), None)


async def find_role_by_id(role_id: int):
    """Find a single role by id. Returns dict or None."""
    roles = await get_all_roles()
    return next((r for r in roles if r['id'] == role_id), None)
