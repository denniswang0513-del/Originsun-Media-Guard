"""
api_auth.py — 認證 API（登入 + 使用者管理）
Endpoints:
  POST   /auth/login       — 登入取得 JWT token
  GET    /auth/me           — 取得當前使用者資訊
  PUT    /auth/me           — 修改自己的密碼/頁籤
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
    _extract_token, load_users_json, save_users_json,
    sync_user_to_json, remove_user_from_json,
)
import core.state as state  # noqa: E402

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


# ── Schemas ──

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "editor"
    visible_tabs: Optional[List[str]] = None


class UpdateUserRequest(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    visible_tabs: Optional[List[str]] = None


class UpdateMeRequest(BaseModel):
    password: Optional[str] = None
    visible_tabs: Optional[List[str]] = None


# ── Helpers ──

async def _get_all_users() -> list:
    """Get users from DB or JSON fallback."""
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import select
                from db.models import User
                async with factory() as session:
                    result = await session.execute(select(User))
                    return [
                        {'username': u.username, 'password_hash': u.password_hash,
                         'role': u.role, 'visible_tabs': u.visible_tabs,
                         'first_login': u.first_login}
                        for u in result.scalars().all()
                    ]
        except Exception:
            pass
    return load_users_json()


async def _find_user(username: str) -> Optional[dict]:
    users = await _get_all_users()
    return next((u for u in users if u['username'] == username), None)


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
                password_hash=user_data['password_hash'],
                role=user_data.get('role', 'editor'),
                visible_tabs=user_data.get('visible_tabs'),
                first_login=user_data.get('first_login', False),
            ).on_conflict_do_update(
                index_elements=['username'],
                set_={
                    'password_hash': user_data['password_hash'],
                    'role': user_data.get('role', 'editor'),
                    'visible_tabs': user_data.get('visible_tabs'),
                    'first_login': user_data.get('first_login', False),
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


def _check_admin(request: Request):
    """Verify request has admin token."""
    payload = _extract_token(request)
    if not payload:
        raise HTTPException(status_code=401, detail="未登入或 token 已過期")
    if payload.get('role') != 'admin':
        raise HTTPException(status_code=403, detail="權限不足")
    return payload


# ── Endpoints ──

@router.post("/login")
async def login(req: LoginRequest):
    """Login and get JWT token."""
    user = await _find_user(req.username)

    # 第 3 層保險：預設 admin/admin（無任何使用者時）
    if not user and req.username == 'admin' and req.password == 'admin':
        all_users = await _get_all_users()
        if not all_users:
            # 首次啟動，自動建立 admin
            user_data = {
                'username': 'admin',
                'password_hash': hash_password('admin'),
                'role': 'admin',
                'visible_tabs': None,
                'first_login': True,
            }
            sync_user_to_json(user_data)
            await _save_user_to_db(user_data)
            token = create_token({'sub': 'admin', 'role': 'admin'})
            return {
                'token': token, 'username': 'admin', 'role': 'admin',
                'visible_tabs': None, 'first_login': True,
            }

    if not user:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    if not verify_password(req.password, user['password_hash']):
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    token = create_token({'sub': user['username'], 'role': user['role']})
    return {
        'token': token,
        'username': user['username'],
        'role': user['role'],
        'visible_tabs': user.get('visible_tabs'),
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
        return {'username': payload.get('sub'), 'role': payload.get('role'), 'visible_tabs': None}
    return {
        'username': user['username'],
        'role': user['role'],
        'visible_tabs': user.get('visible_tabs'),
    }


@router.put("/me")
async def update_me(req: UpdateMeRequest, request: Request):
    """Update own password or visible tabs."""
    payload = _extract_token(request)
    if not payload:
        raise HTTPException(status_code=401, detail="未登入")

    username = payload.get('sub', '')
    user = await _find_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="使用者不存在")

    if req.password:
        user['password_hash'] = hash_password(req.password)
        user['first_login'] = False
    if req.visible_tabs is not None:
        user['visible_tabs'] = req.visible_tabs

    sync_user_to_json(user)
    await _save_user_to_db(user)
    return {'status': 'ok'}


@router.get("/users")
async def list_users(request: Request):
    """List all users (admin only)."""
    _check_admin(request)
    users = await _get_all_users()
    # Don't return password hashes
    return [
        {
            'username': u['username'],
            'role': u['role'],
            'visible_tabs': u.get('visible_tabs'),
        }
        for u in users
    ]


@router.post("/users")
async def create_user(req: CreateUserRequest, request: Request):
    """Create a new user (admin only)."""
    _check_admin(request)

    if _find_user(req.username):
        raise HTTPException(status_code=409, detail=f"使用者 '{req.username}' 已存在")

    if req.role not in ('admin', 'editor', 'viewer'):
        raise HTTPException(status_code=400, detail="角色必須是 admin/editor/viewer")

    user_data = {
        'username': req.username,
        'password_hash': hash_password(req.password),
        'role': req.role,
        'visible_tabs': req.visible_tabs,
    }
    sync_user_to_json(user_data)
    await _save_user_to_db(user_data)
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
    if req.role:
        if req.role not in ('admin', 'editor', 'viewer'):
            raise HTTPException(status_code=400, detail="角色必須是 admin/editor/viewer")
        user['role'] = req.role
    if req.visible_tabs is not None:
        user['visible_tabs'] = req.visible_tabs

    sync_user_to_json(user)
    await _save_user_to_db(user)
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
