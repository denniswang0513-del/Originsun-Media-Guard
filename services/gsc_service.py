"""
gsc_service.py — Google Search Console URL Inspection API client（站點守衛用）。

複用與 GA 相同的服務帳戶金鑰，只是 scope 換成 Search Console 的 webmasters.readonly；
OAuth / token 快取 / JSON HTTP 全在共用的 services/google_oauth.py（快取 key 含 scope，
兩種 token 各存各的）。零新依賴 —— 本檔只組 request body + 正規化回應。

用途：逐 URL 打 URL Inspection API，撈 Google 對每頁的索引現況（有沒有被收錄、
canonical 有沒有被 Google 改掉、robots / 抓取狀態），交給站點守衛 runner 比對告警。

憑證來源：與 GA 同一份 analytics.ga_service_account_json（服務帳戶要在 Search Console
資源加為「擁有者/完整使用者」，否則 inspect 會回 403）。

所有 urllib 呼叫是阻塞的 → runner 用 asyncio.to_thread 包。
"""

import json
import re
import urllib.request
from datetime import datetime, timezone

from services.google_oauth import get_access_token, urlopen_json

# Search Console 唯讀 scope（與 GA 的 analytics.readonly 不同 → token 快取不共用）
_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
_INSPECT_ENDPOINT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"


def _empty_result(page_url: str) -> dict:
    """error / 缺欄位時回這個形狀（欄位齊全但值全 None），呼叫端才能逐 URL 容錯。"""
    return {
        "url": page_url,
        "verdict": None,
        "coverage_state": None,
        "indexing_state": None,
        "robots_txt_state": None,
        "page_fetch_state": None,
        "google_canonical": None,
        "user_canonical": None,
        "last_crawl_at": None,
        "error": None,
    }


def _parse_rfc3339(s):
    """RFC3339 字串 → timezone-aware datetime；失敗回 None。

    Google 的 lastCrawlTime 通常帶 'Z'（部分 Python 的 fromisoformat 不吃），偶爾給
    奈秒（9 位小數，超過 Python 的微秒上限）→ 都先正規化再 parse。無時區資訊時補 UTC。
    """
    if not s or not isinstance(s, str):
        return None
    txt = s.strip()
    txt = re.sub(r"[Zz]$", "+00:00", txt)            # Z → +00:00
    m = re.search(r"\.(\d+)", txt)                     # 小數秒截到 6 位（微秒）
    if m and len(m.group(1)) > 6:
        txt = txt.replace("." + m.group(1), "." + m.group(1)[:6])
    try:
        dt = datetime.fromisoformat(txt)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def inspect_url(site_url: str, page_url: str, sa: dict) -> dict:
    """對單一 URL 打 URL Inspection API，回傳正規化 dict：
    {url, verdict, coverage_state, indexing_state, robots_txt_state,
     page_fetch_state, google_canonical, user_canonical,
     last_crawl_at (datetime|None), error (str|None)}

    任何 HTTP / 解析錯誤都不 raise —— 回同形狀 dict 但 error 填訊息、其餘 None，
    讓 runner 能逐 URL 容錯（一頁壞不阻斷整批掃描）。
    """
    out = _empty_result(page_url)
    try:
        at = get_access_token(sa, _SCOPE)
        body = json.dumps({
            "inspectionUrl": page_url,
            "siteUrl": site_url,
            "languageCode": "zh-TW",
        }).encode()
        req = urllib.request.Request(
            _INSPECT_ENDPOINT, data=body,
            headers={"Authorization": f"Bearer {at}", "Content-Type": "application/json"},
        )
        resp = urlopen_json(req, 20, "Search Console")
    except Exception as e:
        out["error"] = str(e)
        return out

    idx = (resp.get("inspectionResult") or {}).get("indexStatusResult") or {}
    out.update({
        "verdict": idx.get("verdict"),
        "coverage_state": idx.get("coverageState"),
        "indexing_state": idx.get("indexingState"),
        "robots_txt_state": idx.get("robotsTxtState"),
        "page_fetch_state": idx.get("pageFetchState"),
        "google_canonical": idx.get("googleCanonical"),
        "user_canonical": idx.get("userCanonical"),
        "last_crawl_at": _parse_rfc3339(idx.get("lastCrawlTime")),
    })
    return out


def check_credentials(site_url: str, sa: dict) -> dict:
    """驗證服務帳戶對此 Search Console 資源有沒有讀取權限 → {"ok": bool, "error": str|None}。

    作法：對 site_url 首頁 inspect 一次。服務帳戶沒被加進資源 / scope 沒開 → Google
    回 403 → 反映在 inspect_url 的 error 欄。授權正常則回 ok=True。
    """
    res = inspect_url(site_url, site_url, sa)
    if res.get("error"):
        return {"ok": False, "error": res["error"]}
    return {"ok": True, "error": None}
