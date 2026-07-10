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

import json
import time
import urllib.request

from services.google_oauth import get_access_token, parse_service_account, urlopen_json

_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
_DATA_BASE = "https://analyticsdata.googleapis.com/v1beta/properties"

_REALTIME_TTL = 60
_SUMMARY_TTL = 1800

_data_cache: dict = {}    # cache_key -> (payload, exp_epoch)

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


def parse_credentials(prop_id_raw, sa_json_raw):
    """回 (property_id, sa_dict) 或 raise ValueError（憑證缺/壞）。"""
    prop_id = (prop_id_raw or "").strip()
    if not prop_id.isdigit():
        raise ValueError("GA 資源 ID 需為純數字（GA4 Property ID）")
    return prop_id, parse_service_account(sa_json_raw)


def _run(property_id: str, method: str, body: dict, access_token: str) -> dict:
    url = f"{_DATA_BASE}/{property_id}:{method}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    return urlopen_json(req, 20, "GA")


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

    at = get_access_token(sa, _SCOPE)
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

    at = get_access_token(sa, _SCOPE)
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

    at = get_access_token(sa, _SCOPE)
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
