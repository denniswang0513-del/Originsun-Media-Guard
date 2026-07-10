"""
google_oauth.py — Google 服務帳戶 OAuth2 + JSON HTTP（供所有 Google API client 共用）

手刻 RS256 JWT → access token 交換，只用 stdlib + cryptography（零新依賴）。
cryptography 在函式內延遲 import：agent 機器不打 Google API，也就不需要裝它。

目前的使用者：
  - services/ga_service.py   （GA4 Data API，scope analytics.readonly）
  - services/gsc_service.py  （Search Console URL Inspection，scope webmasters.readonly）

⚠ `scope` 是必填、沒有預設值。有預設值的話，任何新的 caller 忘了傳就會拿到別的 API
的 token，然後在遠處以 403 現身 —— 很難查。寧可在呼叫端多寫一個參數。
"""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

_TOKEN_URL = "https://oauth2.googleapis.com/token"

# (client_email, scope) -> (access_token, exp_epoch)
# ⚠ key 必須含 scope：同一把金鑰換不同 API 權限的 token，絕不能互相污染。
_token_cache: dict = {}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def urlopen_json(req, timeout: int, api_name: str) -> dict:
    """urlopen + 解 JSON；HTTPError 時把 Google 的錯誤 body 抽出來（權限沒開、資源不
    存在這類設定錯誤，全靠這段訊息才看得懂）。

    api_name 決定錯誤前綴（"GA" / "Search Console"）—— 這些訊息會直接顯示在後台，
    標錯來源會讓人往錯的方向查。
    """
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            msg = body.get("error", {}).get("message") or body.get("error_description") or str(body)
        except Exception:
            msg = str(e)
        raise RuntimeError(f"{api_name} API {e.code}: {msg}") from e


def get_access_token(sa: dict, scope: str) -> str:
    """用服務帳戶金鑰換指定 scope 的 access token（到期前 60s 內重用快取）。"""
    email = sa["client_email"]
    ck = (email, scope)
    now = time.time()
    cached = _token_cache.get(ck)
    if cached and cached[1] - 60 > now:
        return cached[0]

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64url(json.dumps({
        "iss": email, "scope": scope, "aud": _TOKEN_URL,
        "iat": int(now), "exp": int(now) + 3600,
    }).encode())
    signing_input = f"{header}.{claims}".encode()
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    assertion = f"{header}.{claims}.{_b64url(sig)}"

    data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data)
    tok = urlopen_json(req, 15, "Google OAuth")
    at = tok["access_token"]
    _token_cache[ck] = (at, now + int(tok.get("expires_in", 3600)))
    return at


def parse_service_account(sa_json_raw) -> dict:
    """把服務帳戶 JSON（字串或 dict）解析成 dict；缺欄位/格式錯 → ValueError。"""
    raw = sa_json_raw
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            raise ValueError("尚未貼上服務帳戶 JSON")
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"服務帳戶 JSON 格式錯誤：{e}") from e
    if not isinstance(raw, dict) or "client_email" not in raw or "private_key" not in raw:
        raise ValueError("服務帳戶 JSON 缺 client_email / private_key")
    return raw
