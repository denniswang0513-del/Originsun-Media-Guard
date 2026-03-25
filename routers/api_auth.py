"""
api_auth.py — 認證 API（登入 + 使用者管理）
Endpoints:
  POST   /auth/login       — 登入取得 JWT token
  GET    /auth/me           — 取得當前使用者資訊
  PUT    /auth/me           — 修改自己的密碼
  GET    /auth/users        — 列出所有使用者（admin）
  POST   /auth/users        — 新增使用者（admin）
  PUT    /auth/users/{id}   — 修改使用者（admin）
  DELETE /auth/users/{id}   — 刪除使用者（admin）
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List

from core.auth import (
    hash_password, verify_password, create_token, verify_token,
    _extract_token, check_admin,
    load_users_json, save_users_json, sync_user_to_json, remove_user_from_json,
    load_roles_json, LEGACY_ROLE_LEVELS,
    get_all_roles, find_role_by_name,
)
try:
    from core.google_auth import verify_google_id_token, GoogleTokenError
except ImportError:
    verify_google_id_token = None  # google-auth not installed on this machine
    class GoogleTokenError(Exception): pass
from config import load_settings
import core.state as state
import re

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


# ── Schemas ──

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role_name: str = "editor"


class UpdateUserRequest(BaseModel):
    password: Optional[str] = None
    role_name: Optional[str] = None


class UpdateMeRequest(BaseModel):
    password: Optional[str] = None


class GoogleLoginRequest(BaseModel):
    credential: str


# ── Constants ──
DEFAULT_ROLE = 'editor'


# ── Helpers ──

def _user_orm_to_dict(u) -> dict:
    """Convert an ORM User object to a plain dict (single source of truth)."""
    return {
        'username': u.username, 'password_hash': u.password_hash,
        'role': u.role, 'role_id': u.role_id,
        'visible_tabs': u.visible_tabs, 'first_login': u.first_login,
        'google_id': getattr(u, 'google_id', None),
        'email': getattr(u, 'email', None),
        'avatar_url': getattr(u, 'avatar_url', None),
    }


def _get_user_role_name(u: dict) -> str:
    """Resolve the role name from a user dict with 3-level fallback."""
    return u.get('role_name') or u.get('role') or DEFAULT_ROLE


def _compute_auth_method(u: dict) -> str:
    """Compute auth method string from a user dict."""
    has_pwd = bool(u.get('password_hash'))
    has_google = bool(u.get('google_id'))
    return 'both' if has_pwd and has_google else ('google' if has_google else 'password')


def _enrich_user(u_dict: dict, role) -> dict:
    """Attach role info to a user dict. `role` is a Role ORM object or a dict, or None."""
    if role and hasattr(role, 'name'):
        # ORM object
        u_dict['role_name'] = role.name
        u_dict['access_level'] = role.access_level
        u_dict['modules'] = role.modules or []
    elif role and isinstance(role, dict):
        u_dict['role_name'] = role['name']
        u_dict['access_level'] = role['access_level']
        u_dict['modules'] = role.get('modules', [])
    else:
        u_dict.setdefault('role_name', u_dict.get('role', 'editor'))
        u_dict.setdefault('access_level', LEGACY_ROLE_LEVELS.get(u_dict.get('role', ''), 0))
        u_dict.setdefault('modules', [])
    return u_dict


async def _get_all_users() -> list:
    """Get users from DB + JSON merge (ensures JSON-only users are not lost)."""
    db_users = []
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import select, outerjoin
                from db.models import User, Role
                async with factory() as session:
                    stmt = select(User, Role).select_from(
                        outerjoin(User, Role, User.role_id == Role.id)
                    )
                    rows = (await session.execute(stmt)).all()
                    db_users = [_enrich_user(_user_orm_to_dict(u), r) for u, r in rows]
        except Exception:
            pass
    # Merge with JSON: add any JSON-only users not found in DB
    json_users = load_users_json()
    roles = load_roles_json()
    role_map = {r['id']: r for r in roles}
    role_name_map = {r['name']: r for r in roles}
    db_usernames = {u['username'] for u in db_users}
    for u in json_users:
        if u['username'] in db_usernames:
            continue
        role = role_map.get(u.get('role_id')) or role_name_map.get(u.get('role_name')) or role_name_map.get(u.get('role'))
        _enrich_user(u, role)
        db_users.append(u)
    return db_users


async def _find_user_by(column_name: str, value) -> Optional[dict]:
    """Generic: find a single user by any column (DB first, JSON fallback)."""
    if value is None:
        return None
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import select, outerjoin
                from db.models import User, Role
                async with factory() as session:
                    col = getattr(User, column_name, None)
                    if col is None:
                        return None
                    stmt = select(User, Role).select_from(
                        outerjoin(User, Role, User.role_id == Role.id)
                    ).where(col == value)
                    row = (await session.execute(stmt)).first()
                    if not row:
                        return None
                    u, r = row
                    return _enrich_user(_user_orm_to_dict(u), r)
        except Exception:
            pass
    # JSON fallback
    users = load_users_json()
    roles = load_roles_json()
    role_map = {r['id']: r for r in roles}
    role_name_map = {r['name']: r for r in roles}
    for u in users:
        if u.get(column_name) != value:
            continue
        role = role_map.get(u.get('role_id')) or role_name_map.get(u.get('role_name')) or role_name_map.get(u.get('role'))
        return _enrich_user(u, role)
    return None


async def _find_user(username: str) -> Optional[dict]:
    """Find a single user by username."""
    return await _find_user_by('username', username)


async def _persist_user(user_data: dict):
    """Save user to both JSON and DB (single call site for all mutations)."""
    await _persist_user(user_data)


async def _save_user_to_db(user_data: dict):
    """Save user to DB (upsert). Silent on failure."""
    if not state.db_online:
        return
    try:
        from db.session import get_session_factory
        factory = get_session_factory()
        if not factory:
            return
        from db.models import User
        from sqlalchemy.dialects.postgresql import insert
        async with factory() as session:
            stmt = insert(User).values(
                username=user_data['username'],
                password_hash=user_data.get('password_hash'),
                role=user_data.get('role_name', user_data.get('role', 'editor')),
                role_id=user_data.get('role_id'),
                visible_tabs=user_data.get('visible_tabs'),
                first_login=user_data.get('first_login', False),
                google_id=user_data.get('google_id'),
                email=user_data.get('email'),
                avatar_url=user_data.get('avatar_url'),
            ).on_conflict_do_update(
                index_elements=['username'],
                set_={
                    'password_hash': user_data.get('password_hash'),
                    'role': user_data.get('role_name', user_data.get('role', 'editor')),
                    'role_id': user_data.get('role_id'),
                    'visible_tabs': user_data.get('visible_tabs'),
                    'first_login': user_data.get('first_login', False),
                    'google_id': user_data.get('google_id'),
                    'email': user_data.get('email'),
                    'avatar_url': user_data.get('avatar_url'),
                }
            )
            await session.execute(stmt)
            await session.commit()
    except Exception:
        pass


async def _delete_user_from_db(username: str):
    """Delete user from DB. Silent on failure."""
    if not state.db_online:
        return
    try:
        from db.session import get_session_factory
        factory = get_session_factory()
        if not factory:
            return
        from db.models import User
        from sqlalchemy import delete
        async with factory() as session:
            await session.execute(delete(User).where(User.username == username))
            await session.commit()
    except Exception:
        pass


_check_admin = check_admin  # use shared implementation from core.auth


def _build_json_mirror(user_data: dict) -> dict:
    """Build a JSON-friendly user dict with denormalized role info for offline use."""
    rn = _get_user_role_name(user_data)
    return {
        'username': user_data['username'],
        'password_hash': user_data.get('password_hash') or '',
        'role': rn,       # legacy compat
        'role_id': user_data.get('role_id'),
        'role_name': rn,
        'access_level': user_data.get('access_level', 0),
        'modules': user_data.get('modules', []),
        'visible_tabs': user_data.get('visible_tabs'),  # legacy
        'first_login': user_data.get('first_login', False),
        'google_id': user_data.get('google_id'),
        'email': user_data.get('email'),
        'avatar_url': user_data.get('avatar_url'),
    }


# ── Endpoints ──

@router.post("/login")
async def login(req: LoginRequest):
    """Login and get JWT token."""
    user = await _find_user(req.username)

    # 第 3 層保險：預設 admin/admin（無任何使用者時）
    if not user and req.username == 'admin' and req.password == 'admin':
        all_users = await _get_all_users()
        if not all_users:
            # Find admin role
            admin_role = await find_role_by_name('admin')
            role_id = admin_role['id'] if admin_role else None
            access_level = admin_role['access_level'] if admin_role else 3
            modules = admin_role['modules'] if admin_role else []

            user_data = {
                'username': 'admin',
                'password_hash': hash_password('admin'),
                'role_name': 'admin',
                'role_id': role_id,
                'access_level': access_level,
                'modules': modules,
                'first_login': True,
            }
            await _persist_user(user_data)
            token = create_token({
                'sub': 'admin', 'role_name': 'admin',
                'access_level': access_level, 'modules': modules,
            })
            return {
                'token': token, 'username': 'admin',
                'role_name': 'admin', 'access_level': access_level,
                'modules': modules, 'first_login': True,
            }

    if not user:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    # Google-only user has no password
    if not user.get('password_hash'):
        raise HTTPException(status_code=401, detail="此帳號使用 Google 登入，請點擊 Google 按鈕")

    if not verify_password(req.password, user['password_hash']):
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    role_name = _get_user_role_name(user)
    access_level = user.get('access_level', 0)
    modules = user.get('modules', [])

    token = create_token({
        'sub': user['username'], 'role_name': role_name,
        'access_level': access_level, 'modules': modules,
    })
    return {
        'token': token,
        'username': user['username'],
        'role_name': role_name,
        'access_level': access_level,
        'modules': modules,
        'first_login': user.get('first_login', False),
    }


@router.get("/me")
async def get_me(request: Request):
    """Get current user info."""
    payload = _extract_token(request)
    if not payload:
        raise HTTPException(status_code=401, detail="未登入")
    user = await _find_user(payload.get('sub', ''))
    if not user:
        # Fallback to token payload
        return {
            'username': payload.get('sub'),
            'role_name': payload.get('role_name', payload.get('role', '')),
            'access_level': payload.get('access_level', 0),
            'modules': payload.get('modules', []),
        }
    auth_method = _compute_auth_method(user)
    return {
        'username': user['username'],
        'role_name': user.get('role_name', user.get('role', '')),
        'access_level': user.get('access_level', 0),
        'modules': user.get('modules', []),
        'email': user.get('email'),
        'avatar_url': user.get('avatar_url'),
        'auth_method': auth_method,
    }


@router.put("/me")
async def update_me(request: Request):
    """Update own password."""
    payload = _extract_token(request)
    if not payload:
        raise HTTPException(status_code=401, detail="未登入")

    username = payload.get('sub', '')
    user = await _find_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="使用者不存在")

    body = await request.json()
    if body.get('password'):
        user['password_hash'] = hash_password(body['password'])
        user['first_login'] = False

    await _persist_user(user)
    return {'status': 'ok'}


@router.get("/users")
async def list_users(request: Request):
    """List all users (admin only)."""
    _check_admin(request)
    users = await _get_all_users()
    result = []
    for u in users:
        auth_method = _compute_auth_method(u)
        result.append({
            'username': u['username'],
            'role_name': u.get('role_name', u.get('role', '')),
            'role_id': u.get('role_id'),
            'access_level': u.get('access_level', 0),
            'modules': u.get('modules', []),
            'email': u.get('email'),
            'avatar_url': u.get('avatar_url'),
            'auth_method': auth_method,
        })
    return result


@router.post("/users")
async def create_user(req: CreateUserRequest, request: Request):
    """Create a new user (admin only)."""
    _check_admin(request)

    existing = await _find_user(req.username)
    if existing:
        raise HTTPException(status_code=409, detail=f"使用者 '{req.username}' 已存在")

    # Look up role by name
    role = await find_role_by_name(req.role_name)
    if not role:
        raise HTTPException(status_code=400, detail=f"角色 '{req.role_name}' 不存在")

    user_data = {
        'username': req.username,
        'password_hash': hash_password(req.password),
        'role_name': role['name'],
        'role_id': role['id'],
        'access_level': role['access_level'],
        'modules': role['modules'],
        'first_login': False,
    }
    await _persist_user(user_data)
    return {'status': 'ok', 'username': req.username}


@router.put("/users/{username}")
async def update_user(username: str, req: UpdateUserRequest, request: Request):
    """Update a user (admin only)."""
    _check_admin(request)

    user = await _find_user(username)
    if not user:
        raise HTTPException(status_code=404, detail=f"使用者 '{username}' 不存在")

    if req.password:
        user['password_hash'] = hash_password(req.password)
    if req.role_name:
        role = await find_role_by_name(req.role_name)
        if not role:
            raise HTTPException(status_code=400, detail=f"角色 '{req.role_name}' 不存在")
        user['role_name'] = role['name']
        user['role_id'] = role['id']
        user['access_level'] = role['access_level']
        user['modules'] = role['modules']

    await _persist_user(user)
    return {'status': 'ok'}


@router.delete("/users/{username}")
async def delete_user(username: str, request: Request):
    """Delete a user (admin only). Cannot delete yourself."""
    payload = _check_admin(request)

    if payload.get('sub') == username:
        raise HTTPException(status_code=400, detail="不能刪除自己")

    user = await _find_user(username)
    if not user:
        raise HTTPException(status_code=404, detail=f"使用者 '{username}' 不存在")

    remove_user_from_json(username)
    await _delete_user_from_db(username)
    return {'status': 'ok'}


# ── Google OAuth Endpoints ──

@router.get("/google/config")
async def google_config():
    """Return Google OAuth config for frontend (public, no auth required).
    If local settings have no google_oauth, try fetching from master server."""
    settings = load_settings()
    g = settings.get("google_oauth", {})

    # If local config has it, return directly
    if g.get("enabled") and g.get("client_id"):
        return {"enabled": True, "client_id": g["client_id"]}

    # Otherwise try master server (agent machines don't have google_oauth in settings)
    master = settings.get("master_server", "")
    if master:
        try:
            import urllib.request, json as _json
            url = f"{master.rstrip('/')}/api/v1/auth/google/config"
            req = urllib.request.Request(url, headers={"User-Agent": "OriginsunAgent/2.0"})
            with urllib.request.urlopen(req, timeout=3) as r:
                return _json.loads(r.read().decode())
        except Exception:
            pass

    return {"enabled": g.get("enabled", False), "client_id": g.get("client_id", "")}


async def _find_user_by_google_id(google_id: str) -> Optional[dict]:
    """Find user by Google ID."""
    return await _find_user_by('google_id', google_id)


async def _find_user_by_email(email: str) -> Optional[dict]:
    """Find user by email."""
    return await _find_user_by('email', email)


def _generate_unique_username(email: str, display_name: str, existing_users: list = None) -> str:
    """Generate a unique username from email or display name."""
    # Try email prefix first
    if email and '@' in email:
        base = email.split('@')[0]
    elif display_name:
        base = display_name.lower().replace(' ', '_')
    else:
        base = 'user'
    # Sanitize: only allow a-z, 0-9, underscore
    base = re.sub(r'[^a-z0-9_]', '', base.lower())[:32] or 'user'

    # Check for collision (caller can pass pre-loaded list to avoid redundant I/O)
    if existing_users is None:
        existing_users = load_users_json()
    existing = {u['username'] for u in existing_users}
    if base not in existing:
        return base
    for i in range(2, 100):
        candidate = f"{base}_{i}"
        if candidate not in existing:
            return candidate
    import time as _time
    return f"{base}_{int(_time.time())}"


@router.post("/google/login")
async def google_login(req: GoogleLoginRequest):
    """Authenticate via Google OAuth. Auto-creates user on first login.
    If google-auth is not installed (agent machine), proxy to master server."""
    settings = load_settings()

    # Agent machines: proxy Google login to master server
    if verify_google_id_token is None:
        master = settings.get("master_server", "")
        if master:
            try:
                import urllib.request, json as _json
                url = f"{master.rstrip('/')}/api/v1/auth/google/login"
                data = _json.dumps({"credential": req.credential}).encode()
                _req = urllib.request.Request(url, data=data, headers={
                    "Content-Type": "application/json",
                    "User-Agent": "OriginsunAgent/2.0",
                })
                with urllib.request.urlopen(_req, timeout=10) as r:
                    result = _json.loads(r.read().decode())
                    # Sync the user locally so agent knows about them
                    if result.get("username"):
                        _persist_user({
                            "username": result["username"],
                            "role_name": result.get("role_name", DEFAULT_ROLE),
                            "google_id": result.get("google_id", ""),
                            "email": result.get("email", ""),
                            "avatar_url": result.get("avatar_url", ""),
                        })
                    return result
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Master server Google login failed: {e}")
        raise HTTPException(status_code=501, detail="google-auth not installed and no master_server configured")
    g = settings.get("google_oauth", {})
    if not g.get("enabled"):
        raise HTTPException(status_code=400, detail="Google OAuth is not enabled")

    client_id = g.get("client_id", "")
    try:
        idinfo = verify_google_id_token(req.credential, client_id)
    except GoogleTokenError as e:
        raise HTTPException(status_code=401, detail=str(e))

    google_id = idinfo["sub"]
    email = idinfo.get("email", "")
    name = idinfo.get("name", "")
    picture = idinfo.get("picture", "")

    # Domain restriction
    allowed_domains = g.get("allowed_domains", [])
    if allowed_domains:
        hd = idinfo.get("hd", "")
        email_domain = email.split("@")[1] if "@" in email else ""
        if hd not in allowed_domains and email_domain not in allowed_domains:
            raise HTTPException(status_code=403, detail=f"此 Google 帳號的網域不被允許")

    # 1. Look up by google_id
    user = await _find_user_by_google_id(google_id)

    if not user:
        # 2. Try to find by email (for linking existing account)
        user = await _find_user_by_email(email) if email else None

        if user:
            # Link Google to existing account
            user['google_id'] = google_id
            user['avatar_url'] = picture
            if not user.get('email'):
                user['email'] = email
            sync_user_to_json(_build_json_mirror(user))
            await _save_user_to_db(user)
        else:
            # 3. Auto-create new user
            default_role_name = g.get("default_role", "editor")
            role = await find_role_by_name(default_role_name)
            if not role:
                role = await find_role_by_name("editor")
            if not role:
                # Absolute fallback
                role = {'id': None, 'name': 'editor', 'access_level': 1, 'modules': []}

            username = _generate_unique_username(email, name, load_users_json())
            user = {
                'username': username,
                'password_hash': None,
                'role_name': role['name'],
                'role_id': role['id'],
                'access_level': role['access_level'],
                'modules': role.get('modules', []),
                'google_id': google_id,
                'email': email,
                'avatar_url': picture,
                'first_login': True,
            }
            sync_user_to_json(_build_json_mirror(user))
            await _save_user_to_db(user)
    else:
        # Update avatar on each login
        if picture and user.get('avatar_url') != picture:
            user['avatar_url'] = picture
            sync_user_to_json(_build_json_mirror(user))
            await _save_user_to_db(user)

    # Issue JWT (same as password login)
    role_name = _get_user_role_name(user)
    access_level = user.get('access_level', 0)
    modules = user.get('modules', [])

    token = create_token({
        'sub': user['username'], 'role_name': role_name,
        'access_level': access_level, 'modules': modules,
    })
    return {
        'token': token,
        'username': user['username'],
        'role_name': role_name,
        'access_level': access_level,
        'modules': modules,
        'email': user.get('email'),
        'avatar_url': user.get('avatar_url'),
        'first_login': user.get('first_login', False),
        'auth_method': _compute_auth_method(user),
    }
