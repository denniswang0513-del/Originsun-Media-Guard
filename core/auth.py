"""
Auth utilities — JWT token + password hashing + role decorator + API Key.
Uses stdlib only (no bcrypt dependency).
"""
import asyncio
import hashlib
import hmac
import json
import os
import re
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
    # 每週工作日誌（全員可讀、本人可寫；新註冊預設就有 — api_auth._REGISTER_DEFAULT_MODULES）
    'journal',
    # 影像紀錄總覽（業務管理 › 跨專案管理各專案收集牆；單專案面板仍在專案詳情內）
    'media_log',
    # 參考影片庫 v2（前期製作 › 片庫；docs/REFERENCE_LIBRARY.md）
    'references',
    # 金額檢視（owner 2026-08-15「預設不要看到金額，除非我授權」）。
    # ⚠ **不是** tab —— 它是一個橫切的能力鍵：有它才看得到合約金額／日費／成本。
    # 刻意不讓 crm_invoices / crm_quotes / crm_projects / crm_staff 隱含它
    # （隱含＝有人不經 owner 的手就拿到鑰匙）。政策正本 core/money.py。
    'money_view',
    # 零用金請款的審核／匯款（docs/PETTY_CASH_PLAN.md §4）。
    # ⚠ 與 money_view 一樣是橫切能力鍵、不是 tab；而且**兩個都要**才審得了
    #   （審核畫面本身就是別人的金額）。刻意不用 Lv3 當閘門 —— 主管未必是
    #   管理員，用 Lv3 等於逼 owner 把管理員發出去。
    'finance_approve',
    # 零用金（/petty-cash.html 與 /my.html 零用金卡）。原本沿用 me_finance，
    # owner 2026-08-19「請款要單獨控制的授權按鈕」→ 拆出獨立 key。
    # own-scope 後端本來就只認登入＋綁定人員，這把鑰匙管的是 UI 入口。
    'me_petty',
    # 兩本帳的兩把帳本 key（docs/LEDGER_ENTITY_PLAN.md §2.1；scope 判定正本 core/ledger.py）。
    # finance_partner（label：母公司報表）：合夥人用。母公司帳**報表唯讀**
    #   （儀表板/三表/drilldown/稅務包）。橫切「帳本」key，不是 tab。
    #   合夥人帳號唯一該有的 key —— **絕不可與 money_view 同給**（money_view
    #   是橫切金額鑰匙，會破報表唯讀邊界，見 plan §2.3），也絕不給 Lv3。
    # finance_mine（label：我的帳）：owner 私帳（entity='mine'）全功能
    #   ＋獨立頂層 tab 的入口 key。Lv3 經 grant_admin_all_modules 自動持有。
    # ⚠ 一律 append 在尾端 — modules[0] 決定 admin 落地頁。
    'finance_partner',
    'finance_mine',
    # 福委會（人事管理 › 福委會；docs/BENEFIT_POOL_PLAN.md）。
    # 這把只管管理端 tab 入口 —— 池與登記的金額本來就受 money_view 抹除層管，
    # 審核／匯款另外要 finance_approve（審的是別人的錢，同零用金）。
    'hr_benefits',
    # 員工自助登記（/my.html 的福委會卡片）。own-scope 後端本來就只認登入＋
    # 綁定人員，這把鑰匙管的是 UI 入口 —— 同 me_petty 的作法。
    'me_benefits',
    # ComfyUI（GPU 機 originsun/100.125.114.5 上的影像生成）。橫切能力鍵、非 tab
    # —— 服務不在這個 app 裡，是 GPU 機上的獨立行程，前面掛一支閘門
    # （D:\AI\authgate\authgate.py）用同一把 jwt_secret 離線驗 cookie 裡的 token，
    # 認的就是這個 key。刻意獨立成一把鑰匙而不是沿用 transcode/drone_meta：
    # ComfyUI 的自訂節點等於那台機器上的任意程式碼執行，不該隨後期製作權限外溢。
    'comfyui',
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


# 進得去某個 tab 的模組（任一即可）。**只有需要額外放行的 tab 才列一行**，
# 值是完整名單（含同名模組）；其餘由 tab_modules() 回 (key,)。
#
# 為什麼住在這裡：這幾個 tuple 本來就是 `check_admin_or_module` 的參數，而這支
# 檔案已經是「admin 或模組」這條規則的正本（見 payload_grants）。原本它們散在
# 各自的 router 裡，於是「誰進得去提案庫」在 repo 裡有三份（router 的閘門、
# tab-config.js 的 TAB_EXTRA_ACCESS、加上工作流 deep-link 要不要畫成可點），
# 而且**已經漂了**：router 收 preprod_plan，前端那份沒有 —— 拍攝企劃的人打
# API 進得去，畫面上卻看不到那個 tab，而工作流的燈還會對他說「你沒有權限」。
#
# 兩道測試把這份釘成事實而不是宣告（tests/unit/test_rbac_module_sync）：
# 掃 routers/ 比對真閘門、比對 tab-config.js 的跨語言鏡射。
TAB_ACCESS: dict = {
    'preprod_proposals': ('preprod_proposals', 'preprod_plan', 'crm_projects'),
    'references': ('references', 'preprod_proposals', 'preprod_plan', 'crm_projects'),
    'portal': ('portal', 'crm_projects'),
    'preprod_locations': ('preprod_locations', 'preprod_plan'),
    'footage': ('footage', 'transcribe'),
    'equipment': ('equipment', 'preprod_plan'),
    'intel': ('intel', 'preprod_plan'),
    # 兩本帳：只有母公司報表 key 的合夥人也進得了財務 tab（docs/LEDGER_ENTITY_PLAN.md §2.1）
    'crm_invoices': ('crm_invoices', 'finance_partner'),
}


def tab_modules(key: str) -> tuple:
    """哪些模組進得去 `key` 這個 tab（預設只有同名那個）。"""
    return TAB_ACCESS.get(key, (key,))


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
        # 帶 purpose 的是特殊用途 token（如密碼重設信裡的 pwd_reset）——
        # 同一把 secret 簽的，簽章驗得過，但**不是**登入憑證，一律拒收。
        # 擋在這個咽喉而不是逐端點檢查：任何「有 token 就放行」的端點
        # （如 /auth/me 的 payload fallback）都自動涵蓋。
        if payload is not None and not payload.get('purpose'):
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


def new_share_token(sub: str, scope: str, expires_days: int) -> str:
    """鑄一張**存進 DB 的分享 token**（提案共編頁、影像紀錄 QR、上架編輯連結…）。

    🔴 與 `create_token` 的差別只有一個 `n`（亂數），但那個差別是必要的：
    payload 其餘欄位（sub / scope / exp / iat）**完全由時間決定**，所以任何拿到
    jwt_secret 的人，只要知道 row id 又猜中發放的那一秒，就能重現出一模一樣的
    字串 —— 逐字比對就擋不住了。（實務上還要先猜中 uuid4 的 row id，難度極高；
    但這是白花力氣就能關掉的缺口。）

    加了 `n` 之後，就算 secret 外流也產不出對得上 DB 的字串，
    「分享連結不靠簽章保護」這句話才真的成立。

    ⚠️ 既有的舊 token 沒有 `n`，照樣有效 —— 驗證只比對字串，不看有沒有這個欄位。
    """
    import secrets
    return create_token({"sub": sub, "scope": scope,
                         "n": secrets.token_urlsafe(9)},
                        expires_days=expires_days)


def new_short_token(nbytes: int = 9) -> str:
    """鑄一張**短的**分享 token（純亂數，12 字元左右）。

    什麼時候用這支、什麼時候用 `new_share_token`：差別在**驗證端要不要從
    token 本身讀出東西**。`decode_unverified` 那條路要靠 payload 的 `sub`
    決定去查哪一列，所以那些 token 非得是 JWT 不可；而「直接拿整串字去 DB
    查」的（會議記錄單篇分享）根本沒解過它 —— JWT 的三段 base64 純粹是把
    網址撐成 400 多個字元（owner 2026-08-15 實際回報「網址好長」）。

    安全性不變：兩者都**不靠簽章保護**（見 `decode_unverified` 的說明），
    真正的憑證都是「這串字與我們存起來的完全相同」。9 bytes = 72 bits 亂數，
    猜中的機率遠低於猜中 uuid4 的 row id —— 而那本來就是舊格式的實際門檻。
    """
    import secrets
    return secrets.token_urlsafe(nbytes)


def decode_unverified(token: str) -> Optional[dict]:
    """解出 JWT 的 payload，**不驗簽章**（過期仍然擋）。

    🔴 這不是認證，別拿它當認證用。只給一種流程：**token 本身已經存在資料庫、
    而且呼叫端會逐字比對**。那種情況真正的憑證是「這串字與我們發出去並存起來
    的那串完全相同」——簽章是重複的第二道鎖。用它取 `sub`（決定去找哪一列）
    是安全的：取錯列的話後面的逐字比對就會失敗。

    **為什麼需要它**：jwt_secret 輪替時，簽章檢查會把所有**已經離開系統**的
    分享連結一起殺掉（印出來貼在現場的 QR、寄給客戶的提案網址）——而那些連結
    本來就偽造不了（光有 secret 產不出對得上 DB 的字串）。輪替真正要殺的是
    登入 token：純 JWT、沒有 DB 當後盾，有 secret 就能自簽一個管理員。
    兩者混在同一道檢查裡，就只能連坐。

    `exp` 讀自未驗簽的 payload，但它同樣被逐字比對釘住 —— 改了 exp 就等於改了
    整串字，那串就對不上 DB 了。
    """
    try:
        parts = str(token or "").split('.')
        if len(parts) != 3:
            return None
        payload = json.loads(_b64url_decode(parts[1]))
        if payload.get('exp', 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def stored_token_matches(stored: str, presented: str) -> bool:
    """已存 DB 的 token 與來訪者出示的是否完全相同（定時安全比較）。"""
    if not stored or not presented:
        return False
    return hmac.compare_digest(str(stored), str(presented))


def check_logged_in(request: Request):
    """只要是有效登入就放行（不分等級、不分模組）。

    給「一般同事日常都要用、但**絕不能對匿名開放**」的端點 —— 主要是 agent 上
    那批直接操作本機檔案系統的工具（讀檔、列目錄、上傳、開檔案總管）。它們對
    登入的同事是正常功能，對沒登入的人是任意讀寫這台機器。

    🔴 用 check_admin 會擋掉正常工作（剪輯師也要挑路徑），用模組守衛則要為此
    發明一個沒人看得懂的模組 key —— 這裡真正要表達的就是「你得先是我們的人」。
    """
    payload = _extract_token(request)
    if payload is None:
        raise HTTPException(status_code=401, detail="未登入或 token 已過期")
    return payload


# 私有網段（LAN 直連的 socket 對端）。讀的是 socket peer 不是 X-Forwarded-For
# —— 後者誰都能填，前者偽造不了。
_PRIVATE_HOST_RE = re.compile(
    r'^(127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)')


def check_lan_or_logged_in(request: Request):
    """LAN 直連免登入；對外（經 cloudflared）必須登入。

    後期製作流程（備份/轉檔/挑資料夾）的**產品前提是同事在本機 agent 上
    不登入直接用**（owner 2026-08-12 拍板）。8/08 對這批檔案系統端點加
    `check_logged_in` 擋的其實是**網際網路匿名者**（read_text 曾對外洩出
    jwt_secret），不是內網同事 —— 一律要登入是矯枉過正，讓全機隊的挑資料夾
    按鈕靜默壞了四天。

    兩種流量的區別**偽造不了**：
    - 經 cloudflared tunnel 進來的請求，Cloudflare edge 一定注入
      `CF-Connecting-IP`/`CF-Ray`（客戶端自己帶的會被 edge 覆寫），且 socket
      對端是本機的 cloudflared —— 所以「來自 127.0.0.1」**不能**單獨當信任
      依據，CF 標頭在就是對外流量，必須驗登入。
    - LAN 直連（同事的瀏覽器打 192.168.x.x:8000 或 localhost）不經 CF，
      socket 對端是私網位址。

    有帶有效 token 一律放行（對外的合法使用者走這條）。回 payload 或
    None（None = LAN 匿名放行 —— 呼叫端不要拿回傳值做權限判斷）。
    """
    payload = _extract_token(request)
    if payload is not None:
        return payload
    if request.headers.get('cf-connecting-ip') or request.headers.get('cf-ray'):
        raise HTTPException(status_code=401, detail="未登入或 token 已過期")
    host = (request.client.host if request.client else '') or ''
    if _PRIVATE_HOST_RE.match(host) or host in ('::1', 'localhost', 'testclient'):
        return None
    raise HTTPException(status_code=401, detail="未登入或 token 已過期")


def payload_grants(payload: Optional[dict], *module_keys: str) -> bool:
    """Verdict for an already-verified token payload: full admin (access_level>=3
    or legacy role=='admin') OR grants any of module_keys. Single source of the
    admin-or-module rule — reused by check_admin_or_module (header path) and by
    callers that verified the token some other way (e.g. a ?token= query param on
    <img>/<video> endpoints that can't send an Authorization header)."""
    if not payload:
        return False
    if payload.get('access_level', -1) >= 3 or payload.get('role') == 'admin':
        return True
    user_modules = payload.get('modules') or []
    return any(k in user_modules for k in module_keys)


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
    if payload_grants(payload, *module_keys):
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
