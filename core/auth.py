"""
Auth utilities — JWT token + password hashing + role decorator + API Key.
Uses stdlib only (no bcrypt dependency).
"""
import asyncio
import hashlib
import hmac
import json
import os
import time
import secrets
import threading
from collections import defaultdict
from datetime import datetime, timezone
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

_cached_secret: Optional[str] = None


def _get_secret() -> str:
    """JWT secret: env var（containers）→ settings.json（master）→ raise。

    每個 JWT verify 都呼叫一次，cache 進 module 變數避免每次讀檔。
    secret 只在啟動時產生 / 載入，永遠不輪替；rotate 時手動重啟 process。
    """
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret
    env_secret = os.environ.get('JWT_SECRET', '').strip()
    if env_secret:
        _cached_secret = env_secret
        return env_secret
    try:
        from config import load_settings, save_settings
        settings = load_settings()
        secret = settings.get('jwt_secret', '')
        if not secret:
            secret = secrets.token_hex(32)
            settings['jwt_secret'] = secret
            save_settings(settings)
        _cached_secret = secret
        return secret
    except Exception as e:
        # 不 fallback 到已知字串 — 寧可 hard-fail 也不要 silent 降為弱 key
        raise RuntimeError(f"JWT secret unavailable: {e}") from e


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

# Canonical module keys (mirrors frontend tab-config.js ALL_MODULES). Source of
# truth now that the role layer is gone — used for the bootstrap admin and as
# the "grant everything" set.
ALL_MODULES = [
    'bulletin',
    'preprod_plan', 'preprod_locations', 'preprod_proposals', 'intel', 'equipment',
    'backup', 'verify', 'transcode', 'concat', 'report', 'transcribe', 'tts', 'footage',
    'drone_meta', 'projects', 'crm_clients', 'crm_projects', 'crm_quotes',
    'crm_staff', 'crm_invoices', 'timesheets', 'portal', 'website_admin',
    # N0 個人工作台（獨立頁 /my.html 的卡片；無 SPA tab）。
    # ⚠ 新 key 一律 append 在尾端 — admin 帳號的 modules[0] 決定 SPA 登入
    #   預設落地頁，插前面會改掉所有管理員的首頁。
    'me_projects', 'me_profile', 'me_todos', 'me_finance',
    # N-hr 人事管理：出缺勤 tab + /my.html 我的請假卡
    'hr_leave', 'me_leave',
]


def grant_admin_all_modules(access_level, modules):
    """RBAC v2 invariant (single source of truth): an admin (access_level>=3)
    implicitly holds every module. The frontend shouldShowTab gates nav purely
    by `modules` (access_level doesn't auto-grant tabs), so an admin must carry
    the full set or they'd lose tabs when stored modules are incomplete. Both
    the JWT-payload builder and api_auth._enrich_user funnel through here so the
    rule can't drift between them.
    """
    return list(ALL_MODULES) if (access_level or 0) >= 3 else modules


# ── Role Decorator ──

def _extract_token(request: Request) -> Optional[dict]:
    """Extract and verify auth from Authorization header OR X-API-Key header.

    Priority: JWT Bearer token → API Key.
    API Key authentication builds a payload identical to JWT so all
    downstream checks (check_admin, require_role, etc.) work unchanged.
    """
    # 1. Try JWT Bearer token first
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        payload = verify_token(auth[7:])
        if payload is not None:
            return payload

    # 2. Try X-API-Key header
    api_key = request.headers.get('X-API-Key', '').strip()
    if api_key:
        return _verify_api_key(api_key, request)

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


def check_admin_or_module(request: Request, *module_keys: str):
    """Like check_admin, but ALSO passes if the token grants any of module_keys.

    For subsystem guards (e.g. 官網管理) that a non-admin should be able to use
    when their per-account `modules` includes the relevant key — WITHOUT granting
    global admin. Full admins (access_level>=3 / legacy role) always pass. The
    `modules` list is server-set at login and HMAC-signed in the JWT, so it
    can't be forged client-side. Does NOT replace check_admin — call it only
    within the specific subsystem guard you want to open up.
    """
    payload = _extract_token(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="未登入或 token 已過期")
    if payload.get('access_level', -1) >= 3 or payload.get('role') == 'admin':
        return payload
    user_modules = payload.get('modules') or []
    if any(k in user_modules for k in module_keys):
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
    return _load_json_cached(_USERS_JSON)

def save_users_json(users: list):
    _save_json(_USERS_JSON, users)

def sync_user_to_json(user_data: dict):
    _sync_to_json(_USERS_JSON, user_data, 'username')

def remove_user_from_json(username: str):
    _remove_from_json(_USERS_JSON, 'username', username)


# ── API Key Authentication ──

_API_KEYS_JSON = os.path.join(_BASE_DIR, 'api_keys.json')

# Rate limiter: track failed API key attempts per IP
_fail_counts: dict[str, list[float]] = defaultdict(list)  # ip → [timestamps]
_fail_lock = threading.Lock()
_RATE_LIMIT_MAX = 10       # max failures in window
_RATE_LIMIT_WINDOW = 300   # 5 minutes


def _check_rate_limit(ip: str) -> bool:
    """Return True if IP is rate-limited (too many failed API key attempts)."""
    now = time.time()
    with _fail_lock:
        attempts = _fail_counts[ip]
        # Prune old entries
        _fail_counts[ip] = [t for t in attempts if now - t < _RATE_LIMIT_WINDOW]
        if not _fail_counts[ip]:
            del _fail_counts[ip]
            return False
        return len(_fail_counts[ip]) >= _RATE_LIMIT_MAX


def _record_fail(ip: str):
    """Record a failed API key attempt."""
    with _fail_lock:
        _fail_counts[ip].append(time.time())


def hash_api_key(key: str) -> str:
    """SHA-256 hash of an API key string."""
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> str:
    """Generate a new API key: osk_ + 32 hex chars (128-bit entropy)."""
    return 'osk_' + secrets.token_hex(16)


# ── Cached JSON reads for hot-path (API Key auth) ──
_json_cache: dict[str, tuple[float, list]] = {}  # path → (mtime, data)


def _load_json_cached(path: str) -> list:
    """Read JSON with mtime-based cache — avoids disk I/O on every request."""
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return []
    cached = _json_cache.get(path)
    if cached and cached[0] == mt:
        return cached[1]
    data = _load_json(path)
    _json_cache[path] = (mt, data)
    return data


def load_api_keys_json() -> list:
    return _load_json_cached(_API_KEYS_JSON)


def save_api_keys_json(keys: list):
    _save_json(_API_KEYS_JSON, keys)


def sync_api_key_to_json(key_data: dict):
    _sync_to_json(_API_KEYS_JSON, key_data, 'id')


def remove_api_key_from_json(key_id: int):
    _remove_from_json(_API_KEYS_JSON, 'id', key_id)


def remove_api_keys_by_username_json(username: str):
    """Remove all API keys for a given username from JSON."""
    items = _load_json(_API_KEYS_JSON)
    _save_json(_API_KEYS_JSON, [k for k in items if k.get('username') != username])


def _verify_api_key(raw_key: str, request: Request) -> Optional[dict]:
    """Verify an API key and return a JWT-compatible payload, or None.

    Checks: rate limit → hash lookup (DB then JSON) → is_active → expires_at → user exists.
    On success, schedules a background update of last_used_at.
    """

    # Rate limit check
    client_ip = request.client.host if request.client else '0.0.0.0'
    if _check_rate_limit(client_ip):
        raise HTTPException(status_code=429, detail="請求過於頻繁，請稍後再試")

    key_hash = hash_api_key(raw_key)
    key_record = _find_api_key_by_hash(key_hash)

    if not key_record:
        _record_fail(client_ip)
        return None

    # Check active
    if not key_record.get('is_active', True):
        _record_fail(client_ip)
        return None

    # Check expiry
    expires = key_record.get('expires_at')
    if expires:
        if isinstance(expires, str):
            try:
                exp_dt = datetime.fromisoformat(expires)
            except Exception:
                exp_dt = None
        else:
            exp_dt = expires
        if exp_dt and exp_dt.replace(tzinfo=timezone.utc if exp_dt.tzinfo is None else exp_dt.tzinfo) < datetime.now(timezone.utc):
            _record_fail(client_ip)
            return None

    # Look up user to build payload
    username = key_record.get('username', '')
    user_payload = _build_user_payload_for_api_key(username)
    if user_payload is None:
        _record_fail(client_ip)
        return None

    # Background update last_used_at (fire and forget)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_update_last_used(key_record['id']))
    except Exception:
        pass

    return user_payload


def _find_api_key_by_hash(key_hash: str) -> Optional[dict]:
    """Find an API key record by its SHA-256 hash. Uses JSON (sync-safe).

    Note: DB api_keys table is write-only for auth lookups because
    _extract_token runs in a sync context and can't await DB queries.
    """
    keys = load_api_keys_json()
    return next((k for k in keys if k.get('key_hash') == key_hash), None)


def _build_user_payload_for_api_key(username: str) -> Optional[dict]:
    """Build a JWT-compatible payload dict for the given username.

    Returns None if user not found. Looks up user's role to populate
    access_level, modules, role_name — identical to JWT token payload.
    """
    # Find user in JSON (sync-safe, no await needed)
    users = load_users_json()
    user = next((u for u in users if u.get('username') == username), None)
    if not user:
        return None

    # Build payload matching JWT format. RBAC v2: authorization is per-user —
    # read modules + access_level straight off the user row (no role lookup).
    role_name = user.get('role_name') or user.get('role', 'editor')
    access_level = user.get('access_level', 1)
    modules = grant_admin_all_modules(access_level, user.get('modules', []))

    return {
        'sub': username,
        'role_name': role_name,
        'access_level': access_level,
        'modules': modules,
        'auth_method': 'api_key',
    }


_last_used_written: dict[int, float] = {}  # key_id → last write timestamp
_LAST_USED_DEBOUNCE = 60  # only write once per 60 seconds per key


async def _update_last_used(key_id: int):
    """Background task: update last_used_at (debounced to avoid disk write storm)."""
    now = time.time()
    if now - _last_used_written.get(key_id, 0) < _LAST_USED_DEBOUNCE:
        return
    _last_used_written[key_id] = now

    now_iso = datetime.now(timezone.utc).isoformat(timespec='seconds')

    # Update JSON
    keys = _load_json(_API_KEYS_JSON)  # bypass cache — we're writing
    for k in keys:
        if k.get('id') == key_id:
            k['last_used_at'] = now_iso
            break
    save_api_keys_json(keys)

    # Update DB
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
                        update(ApiKey).where(ApiKey.id == key_id).values(
                            last_used_at=datetime.now(timezone.utc)
                        )
                    )
                    await session.commit()
        except Exception:
            pass
