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
    hash_password, verify_password, create_token, _extract_token, check_admin,
    load_users_json, sync_user_to_json, remove_user_from_json,
    LEGACY_ROLE_LEVELS, ALL_MODULES, grant_admin_all_modules,
)
try:
    from core.google_auth import verify_google_id_token, GoogleTokenError
except ImportError:
    verify_google_id_token = None  # google-auth not installed on this machine
    class GoogleTokenError(Exception): pass
from config import load_settings
import core.state as state
import re
import asyncio

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


# ── Schemas ──

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    modules: List[str] = []        # 直接授權的模組清單
    access_level: int = 1          # 3=管理員, 1=一般


class UpdateUserRequest(BaseModel):
    password: Optional[str] = None
    modules: Optional[List[str]] = None
    access_level: Optional[int] = None
    staff_id: Optional[str] = None   # N0: 綁定 crm_staff.id；傳 "" = 解綁（None = 不變）


class UpdateMeRequest(BaseModel):
    password: Optional[str] = None


class GoogleLoginRequest(BaseModel):
    credential: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str           # 確認信收件信箱（必填）
    tax_id: str          # 驗證題 1：公司統編
    company_name: str    # 驗證題 2：公司名稱（前端四選一）


class ForgotRequest(BaseModel):
    account: str         # 帳號或 Email（同事常搞混，兩種都吃）


class ResetRequest(BaseModel):
    token: str           # 重設信裡的 token
    new_password: str


# ── Constants ──
DEFAULT_ROLE = 'editor'

# ── 自助註冊（/my.html 員工登入頁）──
# 兩步驟公司知識驗證（owner 指定，2026-07-22）；答案只在伺服端比對。
_REGISTER_TAX_ID = "90371657"
_REGISTER_COMPANY = "源日影像"
# 選擇題選項（正解 + 干擾項）— 回給前端渲染，順序由前端洗牌
_REGISTER_COMPANY_CHOICES = ["源日影像", "日源映畫", "源源製作", "日日有限公司"]
# 新帳號預設權限：**只有**「基本資料」（owner 2026-09-08：剛註冊只能看到基本資料，其餘依授權開放；
# 2026-07-22 曾連工作日誌一起給，現在收回）。其餘功能一律由管理員在「使用者管理」逐項開通。
# me_profile 只開個人資料卡，不開「今天與這週」（那一區一顆功能一把：core.auth.ME_ZONE1_KEYS）。
# Google 第一次登入自動建的帳號也用這份（settings.google_oauth.default_modules 有給才覆蓋）。
_REGISTER_DEFAULT_MODULES = ['me_profile']
# 防暴力：同 IP 連錯 N 次驗證題 → 鎖 M 秒（單機記憶體即可）
_REGISTER_MAX_FAILS = 5
_REGISTER_LOCK_SEC = 600
_register_fails: dict = {}   # ip -> [fail_count, lock_until_monotonic]

# ── 忘記密碼（/my.html）──
# 重設 token = 短效 JWT，claims 夾「目前密碼雜湊的指紋」→ 密碼一改舊連結
# 自動失效（單次使用），不需要任何 DB 欄位。claims 刻意**不含 sub** ——
# 同一把 secret 簽的 token 會被 _extract_token 當 Bearer 收下，不帶 sub /
# modules / access_level 它就過不了任何守衛，重設信外洩也換不到 API 權限。
_RESET_TOKEN_MIN = 30            # 連結有效分鐘數
_FORGOT_IP_MAX = 5               # 同 IP 10 分鐘內最多申請次數（寄信是有成本的）
_FORGOT_IP_WINDOW = 600
_FORGOT_COOLDOWN_SEC = 60        # 同帳號兩封信之間的最短間隔
_forgot_hits: dict = {}          # ip -> [count, window_start_monotonic]
_forgot_last: dict = {}          # username -> last_sent_monotonic
# my.html 只在 master serve（www 不 serve、曾寄錯 404）—— 與註冊信同一個入口，
# 改一起改（另一處：website/src/components/layout/Footer.astro 的登入按鈕）。
_MY_PAGE_URL = "https://foundry.originsun-studio.com/my.html"


# ── Helpers ──

def _user_orm_to_dict(u) -> dict:
    """Convert an ORM User object to a plain dict (single source of truth)."""
    return {
        'username': u.username, 'password_hash': u.password_hash,
        'role': u.role, 'role_id': u.role_id,
        'modules': getattr(u, 'modules', None),            # RBAC v2: per-user
        'access_level': getattr(u, 'access_level', None),  # RBAC v2: per-user
        'visible_tabs': u.visible_tabs, 'first_login': u.first_login,
        'google_id': getattr(u, 'google_id', None),
        'email': getattr(u, 'email', None),
        'avatar_url': getattr(u, 'avatar_url', None),
        'staff_id': getattr(u, 'staff_id', None),          # N0: 帳號 ↔ crm_staff
    }


def _get_user_role_name(u: dict) -> str:
    """Resolve the role name from a user dict with 3-level fallback."""
    return u.get('role_name') or u.get('role') or DEFAULT_ROLE


def _issue_token(user: dict, **extra) -> dict:
    """簽一顆登入 JWT 並回 login 形狀的 dict（token／username／role_name／access_level／modules）。

    密碼登入、重設密碼、Google 登入、token 續期四條路都從這裡出去 —— 各自拼一次的話，
    payload 多一個 claim 就會有一條路漏掉（續期那條當初就是這樣長出來的）。
    `extra` 是各入口自己的附加鍵（first_login／email／avatar_url／auth_method）。
    """
    role_name = _get_user_role_name(user)
    access_level = user.get('access_level', 0)
    modules = user.get('modules', [])
    token = create_token({
        'sub': user['username'], 'role_name': role_name,
        'access_level': access_level, 'modules': modules,
    })
    return {
        'token': token, 'username': user['username'], 'role_name': role_name,
        'access_level': access_level, 'modules': modules, **extra,
    }


def _compute_auth_method(u: dict) -> str:
    """Compute auth method string from a user dict."""
    has_pwd = bool(u.get('password_hash'))
    has_google = bool(u.get('google_id'))
    return 'both' if has_pwd and has_google else ('google' if has_google else 'password')


def _enrich_user(u_dict: dict) -> dict:
    """Attach authorization (modules + access_level) to a user dict.

    RBAC v2: per-user `modules` + `access_level` are authoritative (no role
    layer). The legacy `role` string is only a fallback for any pre-migration
    user row that somehow lacks the per-user fields.
    """
    if u_dict.get('modules') is not None and u_dict.get('access_level') is not None:
        u_dict.setdefault('role_name', u_dict.get('role') or DEFAULT_ROLE)
    else:
        u_dict.setdefault('role_name', u_dict.get('role', 'editor'))
        u_dict.setdefault('access_level', LEGACY_ROLE_LEVELS.get(u_dict.get('role', ''), 0))
        u_dict.setdefault('modules', [])
    # 管理員（Lv3）= 完整權限：見 core.auth.grant_admin_all_modules（單一來源）。
    u_dict['modules'] = grant_admin_all_modules(u_dict.get('access_level'), u_dict.get('modules'))
    return u_dict


async def _get_all_users() -> list:
    """Get users from DB + JSON merge (ensures JSON-only users are not lost)."""
    db_users = []
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import select
                from db.models import User
                async with factory() as session:
                    rows = (await session.execute(select(User))).scalars().all()
                    db_users = [_enrich_user(_user_orm_to_dict(u)) for u in rows]
        except Exception:
            pass
    # Merge with JSON: add any JSON-only users not found in DB
    json_users = load_users_json()
    db_usernames = {u['username'] for u in db_users}
    for u in json_users:
        if u['username'] in db_usernames:
            continue
        db_users.append(_enrich_user(u))
    return db_users


async def _find_user_by(column_name: str, value) -> Optional[dict]:
    """Generic: find a single user by any column (DB first, then JSON fallback).

    IMPORTANT: If DB query succeeds but returns no rows, we still check JSON
    because Google OAuth users may exist only in JSON (not yet synced to DB).
    """
    if value is None:
        return None
    if state.db_online:
        try:
            from db.session import get_session_factory
            factory = get_session_factory()
            if factory:
                from sqlalchemy import select
                from db.models import User
                async with factory() as session:
                    col = getattr(User, column_name, None)
                    if col is not None:
                        u = (await session.execute(
                            select(User).where(col == value)
                        )).scalars().first()
                        if u:
                            return _enrich_user(_user_orm_to_dict(u))
                        # DB returned no rows — fall through to JSON
        except Exception:
            pass
    # JSON fallback (always checked if DB didn't find the user)
    users = load_users_json()
    for u in users:
        if u.get(column_name) != value:
            continue
        return _enrich_user(u)
    return None


async def _find_user(username: str) -> Optional[dict]:
    """Find a single user by username."""
    return await _find_user_by('username', username)


async def _persist_user(user_data: dict):
    """Save user to both JSON and DB (single call site for all mutations)."""
    sync_user_to_json(_build_json_mirror(user_data))
    await _save_user_to_db(user_data)


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
                modules=user_data.get('modules'),
                access_level=user_data.get('access_level'),
                visible_tabs=user_data.get('visible_tabs'),
                first_login=user_data.get('first_login', False),
                google_id=user_data.get('google_id'),
                email=user_data.get('email'),
                avatar_url=user_data.get('avatar_url'),
                staff_id=user_data.get('staff_id'),
            ).on_conflict_do_update(
                index_elements=['username'],
                set_={
                    'password_hash': user_data.get('password_hash'),
                    'role': user_data.get('role_name', user_data.get('role', 'editor')),
                    'role_id': user_data.get('role_id'),
                    'modules': user_data.get('modules'),
                    'access_level': user_data.get('access_level'),
                    'visible_tabs': user_data.get('visible_tabs'),
                    'first_login': user_data.get('first_login', False),
                    'google_id': user_data.get('google_id'),
                    'email': user_data.get('email'),
                    'avatar_url': user_data.get('avatar_url'),
                    'staff_id': user_data.get('staff_id'),
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
        'staff_id': user_data.get('staff_id'),
    }


# ── Endpoints ──

@router.post("/login")
async def login(req: LoginRequest):
    """Login and get JWT token."""
    user = await _find_user(req.username)
    # 帳號欄打 Email 也放行 —— 同事記得信箱、不一定記得帳號名（2026-08-07
    # 截圖實案：帳號欄打 email 反覆「帳號或密碼錯誤」）。帳號名優先，
    # 查不到才用 email 找（帳號名允許含 @ 的既有資料不受影響）。
    if not user and "@" in req.username:
        user = await _find_user_by_email(req.username.strip())

    # 第 3 層保險：預設 admin/admin（無任何使用者時）
    if not user and req.username == 'admin' and req.password == 'admin':
        all_users = await _get_all_users()
        if not all_users:
            # Bootstrap admin (no users yet): full modules + Lv3.
            access_level = 3
            modules = list(ALL_MODULES)
            user_data = {
                'username': 'admin',
                'password_hash': hash_password('admin'),
                'role_name': 'admin',
                'access_level': access_level,
                'modules': modules,
                'first_login': True,
            }
            await _persist_user(user_data)
            return _issue_token(user_data, first_login=True)

    if not user:
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    # Google-only user has no password
    if not user.get('password_hash'):
        raise HTTPException(status_code=401, detail="此帳號使用 Google 登入，請點擊 Google 按鈕")

    if not verify_password(req.password, user['password_hash']):
        raise HTTPException(status_code=401, detail="帳號或密碼錯誤")

    return _issue_token(user, first_login=user.get('first_login', False))


@router.post("/refresh")
async def refresh_token(request: Request):
    """拿一顆**還沒過期**的登入 token 換一顆新的（預設效期同登入：7 天）。

    給手機版殼（frontend/m/shell.js）用：加到主畫面的頁面沒人會重新登入，
    token 到期就是「按鈕突然沒反應」。權限**從 DB 重讀**、不是把舊 payload
    重簽 —— 這段期間被拔掉的模組要在下一次續期就消失。

    只認登入 token：API key 走 `_extract_token` 也拿得到 payload，但那把鑰匙
    有自己的效期與撤銷機制，不能拿來鑄一顆 7 天的 JWT（等於繞過 is_active）。
    帳號已不存在 → 401（本系統沒有「停用」欄，刪帳號就是停用）。
    """
    payload = _extract_token(request)
    if not payload or payload.get('auth_method') == 'api_key':
        raise HTTPException(status_code=401, detail="未登入或 token 已過期")
    if payload.get('scope') or payload.get('purpose'):
        raise HTTPException(status_code=401, detail="分享連結的 token 不能換登入 token")
    # 絕對壽命：從第一次登入（login_at；舊 token 退回 iat）起 90 天，續期只能在這之內滾；遺失的手機 token 不能無限續
    import time as _t
    _login_at = int(payload.get('login_at') or payload.get('iat') or 0)
    if _login_at and _t.time() - _login_at > 90 * 86400:
        raise HTTPException(status_code=401, detail="登入已超過 90 天，請重新登入")
    user = await _find_user_by('username', payload.get('sub') or '')
    if not user:
        raise HTTPException(status_code=401, detail="帳號不存在或已停用")
    return _issue_token(user, login_at=_login_at or int(_t.time()))   # 第一次登入的時間跟著續下去，90 天絕對壽命才算得到


@router.get("/register/config")
async def register_config():
    """註冊頁題目設定（公開）— 只回選項不回答案，正解比對在 /register 伺服端。"""
    return {"company_choices": list(_REGISTER_COMPANY_CHOICES)}


@router.post("/register")
async def register(req: RegisterRequest, request: Request):
    """員工自助註冊（/my.html）— 兩步驟公司知識驗證通過才建帳號。

    新帳號 access_level=1 + 只有 me_profile（基本資料卡），其餘功能管理員之後在使用者管理逐項開通。
    成功直接回 login 同形狀（自動登入）。"""
    import time as _time
    ip = (request.client.host if request.client else "") or "?"
    rec = _register_fails.get(ip)
    if rec and rec[1] > _time.monotonic():
        raise HTTPException(status_code=429, detail="嘗試次數過多，請 10 分鐘後再試")

    # 伺服端比對驗證題（答案不下發前端）
    if (req.tax_id or "").strip() != _REGISTER_TAX_ID or \
       (req.company_name or "").strip() != _REGISTER_COMPANY:
        cnt = (rec[0] if rec else 0) + 1
        lock = _time.monotonic() + _REGISTER_LOCK_SEC if cnt >= _REGISTER_MAX_FAILS else 0.0
        _register_fails[ip] = [0 if lock else cnt, lock]
        raise HTTPException(status_code=400, detail="公司驗證未通過，請確認答案")
    _register_fails.pop(ip, None)

    username = (req.username or "").strip()
    if not (2 <= len(username) <= 32) or any(c.isspace() for c in username):
        raise HTTPException(status_code=400, detail="帳號需 2-32 字元且不含空白")
    if len(req.password or "") < 6:
        raise HTTPException(status_code=400, detail="密碼至少 6 個字元")
    email = (req.email or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email) or len(email) > 128:
        raise HTTPException(status_code=400, detail="請填寫有效的電子信箱")
    if await _find_user(username):
        raise HTTPException(status_code=409, detail=f"帳號「{username}」已存在，請改用其他名稱或直接登入")

    modules = list(_REGISTER_DEFAULT_MODULES)
    user_data = {
        'username': username,
        'password_hash': hash_password(req.password),
        'email': email,
        'role_name': 'user',
        'access_level': 1,
        'modules': modules,
        'first_login': False,
    }
    await _persist_user(user_data)

    email_sent = await _send_register_email(email, username)
    try:
        from notifier import notify_tab_async
        await notify_tab_async("user_registered", username=username, email=email)
    except Exception:
        pass  # 團隊通知失敗不影響註冊

    token = create_token({
        'sub': username, 'role_name': 'user',
        'access_level': 1, 'modules': modules,
    })
    return {
        'token': token, 'username': username, 'role_name': 'user',
        'access_level': 1, 'modules': modules, 'first_login': False,
        'email_sent': email_sent,
    }


async def _send_staff_email(to_email: str, subject: str, body: str) -> bool:
    """寄員工系統信（註冊確認/密碼重設共用）。best-effort —— SMTP 設定在
    website_settings、只有 master 有；設定不全或寄失敗回 False，呼叫端
    自己決定怎麼往下走。"""
    try:
        from db.session import get_session_factory
        from services.website.notify_service import _smtp_send
        from services.website.settings_service import get_all_settings
        factory = get_session_factory()
        if factory is None:
            return False
        async with factory() as session:
            settings = await get_all_settings(session)
        if not ((settings.get("notify.smtp_user") or "").strip()
                and (settings.get("notify.smtp_password") or "").strip()):
            return False   # SMTP 未設定 — 誠實回報未寄出
        await asyncio.to_thread(_smtp_send, settings, [to_email], subject, body)
        return True
    except Exception:
        return False


async def _send_register_email(to_email: str, username: str) -> bool:
    """註冊確認信（best-effort，不影響註冊本身）。"""
    body = (
        f"{username} 您好：\n\n"
        f"您的源日影像員工帳號已完成公司驗證並建立成功。\n\n"
        f"目前帳號已開通「個人基本資料」編修功能；其他功能（專案、工時、請假等）\n"
        f"將由管理員依您的職務開通，開通後重新登入即可使用。\n\n"
        # 官網（www）不 serve /my.html（曾寄錯 404，2026-07-24 劉禮瑜回報）——
        # 員工工作台正確入口是 foundry 隧道（→ master 8000，my.html + API 同源）。
        f"個人工作台：{_MY_PAGE_URL}\n\n"
        f"— 源日影像 Originsun Studio（此信由系統自動寄出，請勿直接回覆）"
    )
    return await _send_staff_email(to_email, "源日影像 — 員工帳號註冊確認", body)


# ── 忘記密碼 ──

def _pwd_fingerprint(user: dict) -> str:
    """目前密碼雜湊的指紋（重設 token 的單次使用鎖）。Google-only 帳號沒有
    雜湊也給固定指紋 —— 他們一樣可以用重設流程「設定」第一組密碼。"""
    import hashlib
    return hashlib.sha256((user.get('password_hash') or 'none').encode()).hexdigest()[:16]


def _mask_email(email: str) -> str:
    """o***@gmail.com — 讓申請的人確認寄去哪，又不把整個信箱亮在畫面上。"""
    local, _, domain = (email or "").partition("@")
    return (local[:1] + "***@" + domain) if domain else "***"


@router.post("/forgot")
async def forgot_password(req: ForgotRequest, request: Request):
    """忘記密碼（/my.html）—— 寄 30 分鐘有效的單次重設連結到註冊信箱。

    帳號或 Email 都吃。查無帳號回泛用訊息（不確認存在與否）；有帳號但沒留
    Email 的舊帳號誠實告知找管理員。Google-only 帳號照寄 —— 重設流程等於
    幫他設第一組密碼（之後兩種登入都通）。"""
    import time as _time
    ip = (request.client.host if request.client else "") or "?"
    now = _time.monotonic()
    hits = _forgot_hits.get(ip)
    if hits and now - hits[1] < _FORGOT_IP_WINDOW and hits[0] >= _FORGOT_IP_MAX:
        raise HTTPException(status_code=429, detail="嘗試次數過多，請 10 分鐘後再試")
    _forgot_hits[ip] = [1, now] if (not hits or now - hits[1] >= _FORGOT_IP_WINDOW) \
        else [hits[0] + 1, hits[1]]

    account = (req.account or "").strip()
    generic = {"sent": False, "message": "若帳號存在且有留 Email，重設信已寄出"}
    if not account:
        return generic
    user = await _find_user(account)
    if not user and "@" in account:
        user = await _find_user_by_email(account)
    if not user:
        return generic          # 不確認帳號存在與否（此端點對外網開放）
    email = (user.get('email') or "").strip()
    if not email:
        return {"sent": False,
                "message": "此帳號沒有留 Email，請聯絡管理員重設密碼"}
    username = user['username']
    if now - _forgot_last.get(username, -_FORGOT_COOLDOWN_SEC) < _FORGOT_COOLDOWN_SEC:
        return {"sent": True, "message": f"重設信剛寄出過（{_mask_email(email)}），"
                                         f"請稍候再試或檢查垃圾信件匣"}

    token = create_token(
        {'purpose': 'pwd_reset', 'account': username, 'pfp': _pwd_fingerprint(user)},
        expires_days=_RESET_TOKEN_MIN / 1440)
    body = (
        f"{username} 您好：\n\n"
        f"我們收到重設密碼的申請。您的帳號名稱是：{username}\n\n"
        f"請在 {_RESET_TOKEN_MIN} 分鐘內點擊以下連結設定新密碼（連結只能用一次）：\n"
        f"{_MY_PAGE_URL}?reset={token}\n\n"
        f"如果這不是您本人申請的，忽略此信即可，密碼不會被更改。\n\n"
        f"— 源日影像 Originsun Studio（此信由系統自動寄出，請勿直接回覆）"
    )
    sent = await _send_staff_email(email, "源日影像 — 重設員工帳號密碼", body)
    if not sent:
        return {"sent": False, "message": "寄信失敗，請稍後再試或聯絡管理員"}
    _forgot_last[username] = now
    return {"sent": True, "message": f"重設信已寄出至 {_mask_email(email)}，"
                                     f"信裡也會提醒你的帳號名稱"}


@router.post("/reset")
async def reset_password(req: ResetRequest):
    """用重設信的 token 設新密碼 —— 成功直接回 login 同形狀（自動登入）。"""
    from core.auth import verify_token
    payload = verify_token((req.token or "").strip()) or {}
    if payload.get('purpose') != 'pwd_reset':
        raise HTTPException(status_code=400, detail="連結無效或已過期，請重新申請重設")
    user = await _find_user(payload.get('account') or "")
    if not user:
        raise HTTPException(status_code=400, detail="連結無效或已過期，請重新申請重設")
    if payload.get('pfp') != _pwd_fingerprint(user):
        raise HTTPException(status_code=400,
                            detail="此連結已使用過（或密碼已變更），請重新申請重設")
    if len(req.new_password or "") < 6:
        raise HTTPException(status_code=400, detail="密碼至少 6 個字元")

    user['password_hash'] = hash_password(req.new_password)
    user['first_login'] = False
    await _persist_user(user)
    return _issue_token(user, first_login=False)


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
        'staff_id': user.get('staff_id'),
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
            'staff_id': u.get('staff_id'),
        })
    return result


@router.post("/users")
async def create_user(req: CreateUserRequest, request: Request):
    """Create a new user (admin only)."""
    _check_admin(request)

    existing = await _find_user(req.username)
    if existing:
        raise HTTPException(status_code=409, detail=f"使用者 '{req.username}' 已存在")

    access_level = 3 if req.access_level >= 3 else 1
    user_data = {
        'username': req.username,
        'password_hash': hash_password(req.password),
        'role_name': 'admin' if access_level >= 3 else 'user',  # 裝飾性，僅供顯示
        'access_level': access_level,
        'modules': req.modules,
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
    if req.modules is not None:
        user['modules'] = req.modules
    if req.access_level is not None:
        user['access_level'] = 3 if req.access_level >= 3 else 1
        user['role_name'] = 'admin' if user['access_level'] >= 3 else 'user'  # 裝飾性
    if req.staff_id is not None:
        # N0 綁定人員檔案："" = 解綁（Optional[None] 只能代表「不變」，需哨兵值）
        user['staff_id'] = req.staff_id or None

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

    # Cascade: delete all API keys belonging to this user
    try:
        from routers.api_api_keys import _delete_keys_by_username
        await _delete_keys_by_username(username)
    except Exception:
        pass

    return {'status': 'ok'}


# ── Google OAuth Endpoints ──

_LOCAL_HOSTS = None  # cached {hostname, own IPs, localhost...} — constant per process


def _local_hosts() -> set:
    """This machine's host identifiers, computed once (getaddrinfo is constant
    for the process lifetime, so don't re-resolve it on every call)."""
    global _LOCAL_HOSTS
    if _LOCAL_HOSTS is None:
        hosts = {"", "127.0.0.1", "localhost", "::1"}
        try:
            import socket
            hn = socket.gethostname()
            hosts.add(hn.lower())
            hosts.update(info[4][0] for info in socket.getaddrinfo(hn, None))
        except Exception:
            pass
        _LOCAL_HOSTS = hosts
    return _LOCAL_HOSTS


def _master_is_self(master_url: str) -> bool:
    """True if master_url points at THIS machine (localhost / own IP).

    The master's master_server points at itself (e.g. .107). google_config must
    NOT fetch its google config FROM ITSELF: a synchronous urlopen on the event
    loop to our own server DEADLOCKS the loop (it cannot serve the self-request
    while it is blocked making it) → the whole server wedges. This is the actual
    root cause of the 2026-06 8000 wedges (confirmed by a watchdog stack dump:
    the event-loop thread frozen in google_config → urlopen → socket.readinto).
    """
    try:
        from urllib.parse import urlparse
        host = (urlparse(master_url).hostname or "").lower()
        return host in _local_hosts()
    except Exception:
        return False


@router.get("/google/config")
async def google_config():
    """Return Google OAuth config for frontend (public, no auth required).
    If local settings have no google_oauth, AGENT machines fetch from master."""
    settings = load_settings()
    g = settings.get("google_oauth", {})

    # If local config has it, return directly
    if g.get("enabled") and g.get("client_id"):
        return {"enabled": True, "client_id": g["client_id"]}

    # Agent fallback: fetch from master. Two guards (see _master_is_self):
    #  (a) NEVER fetch from ourselves — the master's master_server is itself, and
    #      a sync urlopen to self on the loop deadlocks the whole server.
    #  (b) run the fetch in a THREAD so a slow/hung master can never block the loop.
    master = settings.get("master_server", "")
    if master and not _master_is_self(master):
        def _fetch():
            import urllib.request, json as _json
            url = f"{master.rstrip('/')}/api/v1/auth/google/config"
            req = urllib.request.Request(url, headers={"User-Agent": "OriginsunAgent/2.0"})
            with urllib.request.urlopen(req, timeout=3) as r:
                return _json.loads(r.read().decode())
        try:
            return await asyncio.to_thread(_fetch)
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
                    # Sync the user locally so agent knows about them. Mirror the
                    # master's authoritative per-user authorization too.
                    if result.get("username"):
                        await _persist_user({
                            "username": result["username"],
                            "role_name": result.get("role_name", DEFAULT_ROLE),
                            "access_level": result.get("access_level", 1),
                            "modules": result.get("modules", []),
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
            # 3. Auto-create new user — least privilege (RBAC v2): 一般使用者、
            #    預設模組同自助註冊（只有基本資料）；settings.google_oauth.default_modules 有給才覆蓋。
            #    管理員之後在「使用者管理」直接授權。
            username = _generate_unique_username(email, name, load_users_json())
            user = {
                'username': username,
                'password_hash': None,
                'role_name': 'user',
                'access_level': 1,
                'modules': list(g.get('default_modules') or _REGISTER_DEFAULT_MODULES),
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
    return _issue_token(
        user, email=user.get('email'), avatar_url=user.get('avatar_url'),
        first_login=user.get('first_login', False), auth_method=_compute_auth_method(user))
