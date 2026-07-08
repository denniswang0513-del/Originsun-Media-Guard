"""
ga_service.py — GA4 Data API client（後台流量儀表板用）。

手刻服務帳戶 OAuth2（RS256 JWT → token 交換），只用 stdlib + cryptography（零新
依賴）。呼叫 runRealtimeReport / runReport 拿即時在線 + 今日/近 7 天彙總 + 熱門頁面。

憑證來源：website_settings（admin 後台可編）
  - analytics.ga_property_id       GA4 資源 ID（純數字，Data API 用）
  - analytics.ga_service_account_json  服務帳戶 JSON 金鑰（整包貼上）

快取（模組層，避免每次打 Google + 加速 UI）：
  - access token：到期前 60s 內重用
  - realtime：60s / summary：1800s（30 分）

所有 urllib 呼叫是阻塞的 → router 用 asyncio.to_thread 包。
"""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DATA_BASE = "https://analyticsdata.googleapis.com/v1beta/properties"

_REALTIME_TTL = 60
_SUMMARY_TTL = 1800

_token_cache: dict = {}   # client_email -> (access_token, exp_epoch)
_data_cache: dict = {}    # cache_key   -> (payload, exp_epoch)


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _urlopen_json(req, timeout: int) -> dict:
    """urlopen + 解 JSON；HTTPError 時把 Google 的錯誤 body 抽出來（權限/資源不存在
    這類設定錯誤全靠它才看得懂）。"""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            msg = body.get("error", {}).get("message") or body.get("error_description") or str(body)
        except Exception:
            msg = str(e)
        raise RuntimeError(f"GA API {e.code}: {msg}") from e


def parse_credentials(prop_id_raw, sa_json_raw):
    """回 (property_id, sa_dict) 或 raise ValueError（憑證缺/壞）。"""
    prop_id = (prop_id_raw or "").strip()
    if not prop_id.isdigit():
        raise ValueError("GA 資源 ID 需為純數字（GA4 Property ID）")
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
    return prop_id, raw


def _get_access_token(sa: dict) -> str:
    email = sa["client_email"]
    now = time.time()
    cached = _token_cache.get(email)
    if cached and cached[1] - 60 > now:
        return cached[0]

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64url(json.dumps({
        "iss": email, "scope": _SCOPE, "aud": _TOKEN_URL,
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
    tok = _urlopen_json(req, 15)
    at = tok["access_token"]
    _token_cache[email] = (at, now + int(tok.get("expires_in", 3600)))
    return at


def _run(property_id: str, method: str, body: dict, access_token: str) -> dict:
    url = f"{_DATA_BASE}/{property_id}:{method}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    return _urlopen_json(req, 20)


def _int(rows, ri=0, mi=0):
    try:
        return int(rows[ri]["metricValues"][mi]["value"])
    except (IndexError, KeyError, ValueError, TypeError):
        return 0


def realtime(property_id: str, sa: dict) -> dict:
    """即時：現在線上活躍人數 + 熱門頁面 Top 5（快取 60s）。"""
    ck = f"rt:{property_id}"
    now = time.time()
    hit = _data_cache.get(ck)
    if hit and hit[1] > now:
        return hit[0]

    at = _get_access_token(sa)
    total = _run(property_id, "runRealtimeReport",
                 {"metrics": [{"name": "activeUsers"}]}, at)
    by_page = _run(property_id, "runRealtimeReport", {
        "metrics": [{"name": "activeUsers"}],
        "dimensions": [{"name": "unifiedScreenName"}],
        "limit": 5,
    }, at)
    out = {
        "active_users": _int(total.get("rows", [])),
        "top_pages": [
            {"page": r["dimensionValues"][0]["value"], "users": int(r["metricValues"][0]["value"])}
            for r in by_page.get("rows", [])
        ],
    }
    _data_cache[ck] = (out, now + _REALTIME_TTL)
    return out


def summary(property_id: str, sa: dict) -> dict:
    """今日 + 近 7 天彙總、7 天趨勢、熱門頁面（快取 30 分）。"""
    ck = f"sum:{property_id}"
    now = time.time()
    hit = _data_cache.get(ck)
    if hit and hit[1] > now:
        return hit[0]

    at = _get_access_token(sa)
    metrics = [{"name": "totalUsers"}, {"name": "sessions"}, {"name": "screenPageViews"}]

    def _totals(start, end):
        rep = _run(property_id, "runReport",
                   {"dateRanges": [{"startDate": start, "endDate": end}], "metrics": metrics}, at)
        rows = rep.get("rows", [])
        return {"users": _int(rows, 0, 0), "sessions": _int(rows, 0, 1), "views": _int(rows, 0, 2)}

    today = _totals("today", "today")
    week = _totals("7daysAgo", "today")

    trend = _run(property_id, "runReport", {
        "dateRanges": [{"startDate": "6daysAgo", "endDate": "today"}],
        "dimensions": [{"name": "date"}],
        "metrics": [{"name": "totalUsers"}],
        "orderBys": [{"dimension": {"dimensionName": "date"}}],
    }, at)
    trend_rows = [
        {"date": r["dimensionValues"][0]["value"], "users": int(r["metricValues"][0]["value"])}
        for r in trend.get("rows", [])
    ]

    top = _run(property_id, "runReport", {
        "dateRanges": [{"startDate": "7daysAgo", "endDate": "today"}],
        "dimensions": [{"name": "pagePath"}],
        "metrics": [{"name": "screenPageViews"}],
        "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}],
        "limit": 5,
    }, at)
    top_pages = [
        {"path": r["dimensionValues"][0]["value"], "views": int(r["metricValues"][0]["value"])}
        for r in top.get("rows", [])
    ]

    out = {"today": today, "week": week, "trend": trend_rows, "top_pages": top_pages}
    _data_cache[ck] = (out, now + _SUMMARY_TTL)
    return out
