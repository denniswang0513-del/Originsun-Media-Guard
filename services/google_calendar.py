"""services/google_calendar.py — 拍攝場次 → Google 日曆（做法 B：服務帳號直接寫進公司共用日曆）。

規劃：docs/SHOOT_CALENDAR_PLAN.md §2。
憑證：settings.json `google_calendar.service_account_json`（可空）→ 空就借 website_settings 的
`analytics.ga_service_account_json`（GA／Search Console 同一個服務帳號）。owner 要把公司日曆分享給
那個服務帳號 email（權限「變更活動」），再把日曆 ID 存進 settings.json `google_calendar.calendar_id`。

這裡全是**阻塞** I/O（urllib），呼叫端一律 `asyncio.to_thread`。任何失敗回 (None, 錯誤訊息) 不 raise：
同步失敗不能擋住登記拍攝，錯誤記在場次上讓人重試。
事件 JSON 由 core.shoot_logic.event_body 組，這裡只負責送。
"""
from __future__ import annotations

import re
import json
import urllib.parse
import urllib.request

from services.google_oauth import get_access_token, parse_service_account, urlopen_json

SCOPE = "https://www.googleapis.com/auth/calendar.events"
_BASE = "https://www.googleapis.com/calendar/v3/calendars/"
_API = "Google 日曆"


def load_sa_from_settings() -> dict | None:
    """settings.json 那份服務帳號（沒貼就 None）。"""
    from config import load_settings
    raw = (load_settings().get("google_calendar") or {}).get("service_account_json") or ""
    if not str(raw).strip():
        return None
    return parse_service_account(raw)


def calendar_id() -> str:
    from config import load_settings
    return str((load_settings().get("google_calendar") or {}).get("calendar_id") or "").strip()


_CFG_CACHE: dict = {"at": 0.0, "val": None}
_CFG_TTL = 60.0


async def load_config() -> tuple[dict | None, str, str]:
    """回 (sa, calendar_id, error)。sa 來源：settings.json → website_settings 的 GA 服務帳號。
    60 秒內共用同一份（每次寫場次都撈整張 website_settings＋解析 SA 是白費）；改設定 60 秒後生效。"""
    import time as _t
    if _CFG_CACHE["val"] is not None and _t.time() - _CFG_CACHE["at"] < _CFG_TTL:
        return _CFG_CACHE["val"]
    val = await _load_config_uncached()
    if val[0] is not None:                 # 沒憑證的錯誤不快取（貼上設定要馬上生效）
        _CFG_CACHE.update(at=_t.time(), val=val)
    return val


async def _load_config_uncached() -> tuple[dict | None, str, str]:
    try:
        sa = load_sa_from_settings()
    except ValueError as e:
        return None, calendar_id(), str(e)
    if sa is None:
        try:
            from core.db_guard import db_factory_or_503
            from services.website import settings_service
            factory = db_factory_or_503()
            async with factory() as session:
                all_s = await settings_service.get_all_settings(session)
            raw = all_s.get("analytics.ga_service_account_json") or ""
            if str(raw).strip():
                sa = parse_service_account(raw)
        except Exception as e:  # DB 不在、JSON 壞掉 → 就是沒憑證
            return None, calendar_id(), f"服務帳號讀取失敗：{e}"
    if sa is None:
        return None, calendar_id(), "尚未設定服務帳號（settings.json google_calendar.service_account_json，或官網設定的 GA 服務帳號）"
    return sa, calendar_id(), ""


def _request(method: str, url: str, sa: dict, body: dict | None = None) -> dict:
    token = get_access_token(sa, SCOPE)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    return urlopen_json(req, 20, _API)


def _events_url(cal_id: str, event_id: str = "") -> str:
    url = _BASE + urllib.parse.quote(cal_id, safe="") + "/events"
    return url + ("/" + urllib.parse.quote(event_id, safe="") if event_id else "")


def upsert_event(sa: dict, cal_id: str, event_id: str | None, body: dict) -> tuple[str | None, str]:
    """有 event_id 就 PATCH，沒有（或 PATCH 說 404：事件被人在日曆刪掉）就 INSERT。回 (event_id, error)。"""
    try:
        if event_id:
            try:
                got = _request("PATCH", _events_url(cal_id, event_id), sa, body)
                return got.get("id") or event_id, ""
            except RuntimeError as e:
                if " 404" not in str(e) and " 410" not in str(e):
                    raise
        # 事件 id 用我們的 shoot id（uuid hex 是合法的 base32hex）→ insert 冪等：上次 insert 成功但本地 commit 失敗，
        # 再來一次 Google 回 409，就改 PATCH 同一個 id，不會長第二個事件
        key = str(((body.get("extendedProperties") or {}).get("private") or {}).get("originsun_shoot_id") or "")
        if key and re.fullmatch(r"[a-v0-9]{5,1024}", key):
            body = {**body, "id": key}
            try:
                got = _request("POST", _events_url(cal_id), sa, body)
                return got.get("id") or key, ""
            except RuntimeError as e:
                if " 409" not in str(e):
                    raise
                got = _request("PATCH", _events_url(cal_id, key), sa, {k: v for k, v in body.items() if k != "id"})
                return got.get("id") or key, ""
        got = _request("POST", _events_url(cal_id), sa, body)
        return got.get("id"), ""
    except Exception as e:
        return None, str(e)


def delete_event(sa: dict, cal_id: str, event_id: str) -> tuple[bool, str]:
    """刪事件；已經不在（404／410）也算成功。"""
    if not event_id:
        return True, ""
    try:
        _request("DELETE", _events_url(cal_id, event_id), sa)
        return True, ""
    except RuntimeError as e:
        if " 404" in str(e) or " 410" in str(e):
            return True, ""
        return False, str(e)
    except Exception as e:
        return False, str(e)

def test_connection(sa: dict, cal_id: str) -> tuple[bool, str, str]:
    """對日曆列一筆事件（calendar.events scope 能做的最小動作）；回 (ok, message, calendar_summary)。"""
    if not cal_id:
        return False, "尚未填日曆 ID", ""
    try:
        got = _request("GET", _events_url(cal_id) + "?maxResults=1", sa)
        return True, "連線成功", got.get("summary") or ""
    except Exception as e:
        msg = str(e)
        if "404" in msg or "notFound" in msg:
            msg += "（日曆 ID 不對，或還沒把日曆分享給服務帳號）"
        return False, msg, ""
