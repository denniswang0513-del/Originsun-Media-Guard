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

# 可選指標目錄（後台儀表板「⚙️ 設定」讓 owner 勾）。kind 決定前端格式化。
METRIC_CATALOG = {
    "totalUsers":              {"label": "訪客",     "kind": "int"},
    "newUsers":                {"label": "新訪客",   "kind": "int"},
    "sessions":                {"label": "工作階段", "kind": "int"},
    "screenPageViews":         {"label": "瀏覽",     "kind": "int"},
    "averageSessionDuration":  {"label": "平均停留", "kind": "duration"},
    "bounceRate":              {"label": "跳出率",   "kind": "pct"},
    "engagementRate":          {"label": "參與率",   "kind": "pct"},
    "eventCount":              {"label": "事件數",   "kind": "int"},
}
_DEFAULT_METRICS = ["totalUsers", "screenPageViews"]
_ALLOWED_WINDOWS = (7, 14, 28, 30)


def normalize_config(cfg) -> dict:
    """把後台存的 dashboard config 正規化（防呆 + 預設）。"""
    cfg = cfg if isinstance(cfg, dict) else {}
    metrics = [m for m in (cfg.get("metrics") or []) if m in METRIC_CATALOG]
    if not metrics:
        metrics = list(_DEFAULT_METRICS)
    win = cfg.get("window_days")
    if win not in _ALLOWED_WINDOWS:
        win = 7
    return {
        "window_days": win,
        "metrics": metrics,
        "top_by": "path" if cfg.get("top_by") == "path" else "title",
        "show_realtime": cfg.get("show_realtime", True) is not False,
        "show_trend": cfg.get("show_trend", True) is not False,
        "show_top_pages": cfg.get("show_top_pages", True) is not False,
    }


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


def _norm_path(p: str) -> str:
    """正規化路徑供對照：去 query、去結尾斜線（'/' 保留）。"""
    p = (p or "").split("?")[0].split("#")[0]
    if len(p) > 1:
        p = p.rstrip("/")
    return p or "/"


def page_titles(property_id: str, sa: dict, days: int = 365) -> dict:
    """回 {正規化路徑: 頁面標題}（近 N 天 GA 有紀錄的所有頁面，含舊站歷史）。
    給轉址管理對照舊站標題用。快取 1 小時（標題幾乎不變）。"""
    ck = f"titles:{property_id}:{days}"
    now = time.time()
    hit = _data_cache.get(ck)
    if hit and hit[1] > now:
        return hit[0]

    at = _get_access_token(sa)
    rep = _run(property_id, "runReport", {
        "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
        "dimensions": [{"name": "pagePath"}, {"name": "pageTitle"}],
        "metrics": [{"name": "screenPageViews"}],
        "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}],
        "limit": 1000,
    }, at)
    # 依瀏覽量遞減 → 同路徑多標題時，第一個（最高流量）勝出
    out: dict = {}
    for r in rep.get("rows", []):
        path = _norm_path(r["dimensionValues"][0]["value"])
        title = r["dimensionValues"][1]["value"]
        if path not in out and title:
            out[path] = title
    _data_cache[ck] = (out, now + 3600)
    return out


def _fnum(rows, ri, mi):
    """metricValue 轉 float（rate/duration 用）。"""
    try:
        return float(rows[ri]["metricValues"][mi]["value"])
    except (IndexError, KeyError, ValueError, TypeError):
        return 0.0


def summary(property_id: str, sa: dict, config=None) -> dict:
    """今日 + 近 N 天彙總（依 config 選指標）、趨勢、熱門頁面（依標題/路徑）。
    快取 30 分，key 含 config 指紋（改設定即換 key、立即反映不必等過期）。"""
    cfg = normalize_config(config)
    win = cfg["window_days"]
    metric_names = cfg["metrics"]
    top_by = cfg["top_by"]
    window_start = f"{win - 1}daysAgo"   # 含今天共 N 天

    ck = ("sum:{}:{}:{}:{}:{}{}{}".format(
        property_id, win, ",".join(metric_names), top_by,
        int(cfg["show_realtime"]), int(cfg["show_trend"]), int(cfg["show_top_pages"])))
    now = time.time()
    hit = _data_cache.get(ck)
    if hit and hit[1] > now:
        return hit[0]

    at = _get_access_token(sa)
    metric_objs = [{"name": n} for n in metric_names]

    def _totals(start, end):
        rep = _run(property_id, "runReport",
                   {"dateRanges": [{"startDate": start, "endDate": end}], "metrics": metric_objs}, at)
        rows = rep.get("rows", [])
        return {n: _fnum(rows, 0, i) for i, n in enumerate(metric_names)}

    today_vals = _totals("today", "today")
    window_vals = _totals(window_start, "today")
    metrics_out = [{
        "name": n, "label": METRIC_CATALOG[n]["label"], "kind": METRIC_CATALOG[n]["kind"],
        "today": today_vals.get(n, 0), "window": window_vals.get(n, 0),
    } for n in metric_names]

    trend = _run(property_id, "runReport", {
        "dateRanges": [{"startDate": window_start, "endDate": "today"}],
        "dimensions": [{"name": "date"}],
        "metrics": [{"name": "totalUsers"}],
        "orderBys": [{"dimension": {"dimensionName": "date"}}],
    }, at)
    trend_rows = [
        {"date": r["dimensionValues"][0]["value"], "users": int(r["metricValues"][0]["value"])}
        for r in trend.get("rows", [])
    ]

    # 熱門頁面：同時抓 pagePath + pageTitle，前端依 top_by 顯示標題或路徑
    top = _run(property_id, "runReport", {
        "dateRanges": [{"startDate": window_start, "endDate": "today"}],
        "dimensions": [{"name": "pagePath"}, {"name": "pageTitle"}],
        "metrics": [{"name": "screenPageViews"}],
        "orderBys": [{"metric": {"metricName": "screenPageViews"}, "desc": True}],
        "limit": 5,
    }, at)
    top_pages = [
        {"path": r["dimensionValues"][0]["value"],
         "title": r["dimensionValues"][1]["value"],
         "views": int(r["metricValues"][0]["value"])}
        for r in top.get("rows", [])
    ]

    out = {"window_days": win, "top_by": top_by, "metrics": metrics_out,
           "trend": trend_rows, "top_pages": top_pages,
           "_show_realtime": cfg["show_realtime"], "_show_trend": cfg["show_trend"],
           "_show_top_pages": cfg["show_top_pages"]}
    _data_cache[ck] = (out, now + _SUMMARY_TTL)
    return out
