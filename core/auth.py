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


# ── Role Decorator ──

def _extract_token(request: Request) -> Optional[dict]:
    """Extract and verify token from Authorization header."""
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return verify_token(auth[7:])
    return None


def check_admin(request: Request):
    """Check admin permission. Raises 401/403 if not admin."""
    payload = _extract_token(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="未登入或 token 已過期")
    if payload.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="權限不足")
    return payload


def require_role(*roles: str):
    """FastAPI dependency: require authenticated user with specified role."""
    from fastapi import Depends

    async def _check(request: Request):
        payload = _extract_token(request)
        if not payload:
            raise HTTPException(status_code=401, detail="未登入或 token 已過期")
        if payload.get('role') not in roles:
            raise HTTPException(status_code=403, detail="權限不足")
        return payload

    return Depends(_check)


# ── Local JSON fallback for users ──

_USERS_JSON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'users.json')


def load_users_json() -> list:
    """Load users from local JSON file."""
    try:
        with open(_USERS_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def save_users_json(users: list):
    """Save users to local JSON file."""
    try:
        with open(_USERS_JSON, 'w', encoding='utf-8') as f:
            json.dump(users, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def sync_user_to_json(user_data: dict):
    """Add or update a single user in local JSON (mirror of DB)."""
    users = load_users_json()
    existing = next((i for i, u in enumerate(users) if u['username'] == user_data['username']), None)
    if existing is not None:
        users[existing] = user_data
    else:
        users.append(user_data)
    save_users_json(users)


def remove_user_from_json(username: str):
    """Remove a user from local JSON."""
    users = load_users_json()
    users = [u for u in users if u['username'] != username]
    save_users_json(users)
