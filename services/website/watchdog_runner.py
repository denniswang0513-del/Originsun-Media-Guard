"""services/website/watchdog_runner.py — 站點守衛（Site Watchdog）

兩層獨立檢查，回答兩個不同的問題：

1. **probe（多 UA 篡改探測）** — 「我的網站現在對每一種訪客都正常嗎？」
   不需任何憑證。用 7 種 UA（桌機 / iPhone / Android / Googlebot 手機 / Googlebot 桌機 /
   Lighthouse / 空 UA）打對外站，斷言全部 200、無外部重導、canonical 指向自己；
   另驗 http→https、apex→www、robots.txt、sitemap。

   為什麼需要這層：2026-07-09 發現 Cloudflare 被植入惡意重導（UA 含 `Android` → 301
   到 momen.tm）。它只打特定 UA —— 用桌機瀏覽器看網站一切正常，Search Console 的資料
   又延遲數天，所以「站長自己完全不知道」。**唯一能當場抓到的方法，就是拿多種 UA 去問
   同一個網址、比對答案是否一致。** 這層每次 cron 都跑，異常立刻寄信。

2. **index（Search Console 收錄檢查）** — 「Google 到底有沒有收錄我？」
   走 URL Inspection API（需服務帳戶被授權 GSC）。每天掃 sitemap 網址，記錄收錄判定與
   上次檢索時間。首頁掉出索引、或長期未被檢索（爬蟲被攔的早期訊號）→ 告警。

master gate：`core.topology.is_master_machine()`（DB 非 `*_dev` + `master_server` 指向本機）。
否則整個機隊（共用 mediaguard 庫）會重複探測 + 重複轟炸信箱。

settings（website_settings k-v；dev / 生產分庫，enabled 開關天然隔離）：
    watchdog.enabled        bool   false（⚠ 預設關，owner 手動開）
    watchdog.cron           str    "0 */6 * * *"    篡改探測，每 6 小時
    watchdog.index_cron     str    "25 8 * * *"     收錄掃描，每日 08:25（錯開 intel 08:30）
    watchdog.stale_days     int    10               首頁幾天沒被檢索就告警
    watchdog.alert_email    str    ""               空 → fallback notify.email_to
    watchdog.site_url       str    ""               空 → fallback seo.site_url → 常數
    watchdog.gsc_site_url   str    ""               GSC 資源網址（需與 property 完全一致）
    watchdog.last_probe     json   最近一次探測結果（儀表板讀這個）
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from core.topology import is_master_machine
from db.session import get_session_factory
from services.website._runner_util import cron_due, validate_cron
from services.website.settings_service import get_all_settings, update_settings

try:
    from croniter import croniter  # type: ignore
except ImportError:  # pragma: no cover — master 一定裝得到；沒有就不排程
    croniter = None

logger = logging.getLogger(__name__)

_FALLBACK_SITE = "https://www.originsun-studio.com"

_scheduler_task: Optional[asyncio.Task] = None
_run_lock = asyncio.Lock()          # 同 process 鎖（cron loop / admin 立即執行 不重疊）

_DEFAULTS: dict = {
    "watchdog.enabled": False,
    "watchdog.cron": "0 */6 * * *",
    "watchdog.index_cron": "25 8 * * *",
    "watchdog.stale_days": 10,
    "watchdog.alert_email": "",
    "watchdog.site_url": "",
    "watchdog.gsc_site_url": "",
    "watchdog.last_run_at": 0,
    "watchdog.last_index_run_at": 0,
    # 冷卻時間戳「依 kind 分開存」{kind: epoch}。用單一全域時間戳是危險的 ——
    # 一個無害的 gsc_error(warn) 會在 24h 內把真正的 probe_redirect(critical) 一起消音。
    "watchdog.alert_cooldowns": {},
    "watchdog.last_probe": None,
}

# 探測用 UA。android_chrome / googlebot_smartphone / lighthouse_mobile 三支都含
# "Android" —— 正是 2026-07 那條惡意規則的觸發條件。缺任何一支都可能漏抓同類攻擊。
_UAS: dict[str, str] = {
    "desktop_chrome": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "iphone_safari": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "android_chrome": (
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
    ),
    "googlebot_smartphone": (
        "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 "
        "(compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    ),
    "googlebot_desktop": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "lighthouse_mobile": (
        "Mozilla/5.0 (Linux; Android 11; moto g power) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 Chrome-Lighthouse"
    ),
    "empty_ua": "",
}

_TIMEOUT = 15
_BODY_CAP = 16384          # 只讀前 16KB（canonical / title 都在 <head>）
_ALERT_COOLDOWN = 86400    # 持續失敗時最多每 24h 寄一次信


# ── settings ────────────────────────────────────────────────

def _cfg_from(settings: dict) -> dict:
    cfg = {k.split(".", 1)[1]: settings.get(k, v) for k, v in _DEFAULTS.items()}
    cfg["site_url"] = (cfg["site_url"] or settings.get("seo.site_url") or _FALLBACK_SITE).rstrip("/")
    if not cfg["gsc_site_url"]:
        cfg["gsc_site_url"] = cfg["site_url"] + "/"
    try:
        cfg["stale_days"] = int(cfg["stale_days"] or 10)
    except (TypeError, ValueError):
        cfg["stale_days"] = 10
    return cfg


async def get_watchdog_settings(session: AsyncSession) -> dict:
    """讀 watchdog.* 設定 + 憑證是否備妥（儀表板狀態列用）。"""
    settings = await get_all_settings(session)
    cfg = _cfg_from(settings)
    cfg["gsc_configured"] = _load_sa(settings) is not None
    cfg["is_master"] = _is_master()
    return cfg


_ALLOWED_SETTINGS = {
    "enabled": "watchdog.enabled",
    "cron": "watchdog.cron",
    "index_cron": "watchdog.index_cron",
    "stale_days": "watchdog.stale_days",
    "alert_email": "watchdog.alert_email",
    "site_url": "watchdog.site_url",
    "gsc_site_url": "watchdog.gsc_site_url",
}


async def update_watchdog_settings(
    session: AsyncSession, payload: dict, *, by: Optional[str] = None,
) -> dict:
    """admin 更新設定（白名單鍵）。cron 非法 → ValueError（router 轉 422）。"""
    values: dict = {}
    for pk, sk in _ALLOWED_SETTINGS.items():
        if pk not in payload:
            continue
        v = payload[pk]
        if pk in ("cron", "index_cron"):
            validate_cron(str(v))
        if pk == "stale_days":
            v = max(1, int(v))
        if pk == "enabled":
            v = bool(v)
        values[sk] = v
    if values:
        await update_settings(session, values, updated_by=by)
    return await get_watchdog_settings(session)


def _is_master() -> bool:
    """單一權威判準見 core/topology —— 不要再用「某個目錄存在」這種打包副作用來判身分。"""
    return is_master_machine()


def _load_sa(settings: dict) -> Optional[dict]:
    """複用 GA 的服務帳戶金鑰（同一把，只是 scope 不同）。壞掉/沒填 → None。"""
    raw = settings.get("analytics.ga_service_account_json")
    if not raw:
        return None
    try:
        sa = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return sa if sa.get("client_email") and sa.get("private_key") else None


# ── HTTP 探測（同步；由 to_thread 呼叫）────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟隨重導 —— 我們要親眼看到 3xx 和 Location，那正是攻擊的痕跡。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _fetch(url: str, ua: str) -> dict:
    """回 {status, location, body, error}。任何例外都吞掉轉成 error 欄位。

    opener 每次自建 —— 探測是並行的，urllib 的 OpenerDirector 不保證執行緒安全，
    而且這裡本來就不需要連線重用（刻意用不同 UA 各問一次）。
    """
    req = urllib.request.Request(url)
    # 空 UA 時 urllib 仍會塞預設 Python-urllib/x.y；顯式清成空字串才是真的「空 UA」。
    req.add_header("User-Agent", ua or "")
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=_TIMEOUT) as r:
            return {"status": r.status, "location": "",
                    "body": r.read(_BODY_CAP).decode("utf-8", "replace"), "error": None}
    except urllib.error.HTTPError as e:
        # 3xx 也會走這裡（因為 _NoRedirect）—— 這正是我們要的
        return {"status": e.code, "location": e.headers.get("Location", "") or "",
                "body": "", "error": None}
    except Exception as e:  # noqa: BLE001 — 網路層什麼都可能炸，一律轉成 error
        return {"status": 0, "location": "", "body": "", "error": f"{type(e).__name__}: {e}"}


def _host(u: str) -> str:
    try:
        return (urlparse(u).hostname or "").lower()
    except ValueError:
        return ""


def _check(name: str, url: str, expect: str, ok: bool, got: str, detail: str = "") -> dict:
    return {"name": name, "url": url, "expect": expect, "ok": ok, "got": got, "detail": detail}


def _run_probe_sync(site_url: str) -> dict:
    """完整探測。回 {ok, failures, checks:[...]}。純同步、絕不 raise。

    11 個請求彼此獨立，一律**並行**發出：站點真的出事時每個都會跑滿 _TIMEOUT，
    串行的最壞牆鐘時間是 11×15s = 165 秒 —— 而那正是最需要立刻拿到答案的時刻。
    並行後最壞 ≈ 單一最慢請求（~15s）。抓完才做純函式評估，輸出順序固定。
    """
    www = _host(site_url)
    apex = www[4:] if www.startswith("www.") else www
    own = {www, apex}
    ua_d = _UAS["desktop_chrome"]
    checks: list[dict] = []

    # ── 一次把所有請求排出去 ──
    jobs: list[tuple[str, str, str]] = [
        (f"homepage__{n}", site_url + "/", ua) for n, ua in _UAS.items()
    ]
    jobs.append(("http_to_https", "http://" + www + "/", ua_d))
    if apex != www:
        jobs.append(("apex_to_www", "https://" + apex + "/", ua_d))
    jobs.append(("robots_txt", site_url + "/robots.txt", ua_d))
    jobs.append(("sitemap", site_url + "/sitemap-index.xml", ua_d))

    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        results = dict(zip(
            [j[0] for j in jobs],
            pool.map(lambda j: _fetch(j[1], j[2]), jobs),
        ))

    # 1) 每種 UA 打首頁 → 必須 200 且不重導
    for ua_name in _UAS:
        nm = f"homepage__{ua_name}"
        r = results[nm]
        if r["error"]:
            checks.append(_check(nm, site_url + "/", "200 無重導", False, "連線失敗", r["error"]))
            continue
        if 300 <= r["status"] < 400:
            tgt = r["location"]
            external = _host(tgt) not in own
            checks.append(_check(
                nm, site_url + "/", "200 無重導", False, f'{r["status"]} -> {tgt}',
                "🔴 重導到外部主機（疑似遭植入）" if external else "非預期的內部重導"))
            continue
        if r["status"] != 200:
            checks.append(_check(nm, site_url + "/", "200 無重導", False, str(r["status"])))
            continue
        # 200 → 驗內容沒被掉包
        body = r["body"]
        m = re.search(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', body)
        if not m:
            checks.append(_check(nm, site_url + "/", "200 無重導", False, "200 但缺 canonical",
                                 "頁面內容可能被替換"))
        elif _host(m.group(1)) not in own:
            checks.append(_check(nm, site_url + "/", "200 無重導", False,
                                 f"canonical 指向 {m.group(1)}", "🔴 canonical 被改到外部網域"))
        elif not re.search(r"<title>\s*\S", body):
            checks.append(_check(nm, site_url + "/", "200 無重導", False, "200 但 <title> 空白"))
        else:
            checks.append(_check(nm, site_url + "/", "200 無重導", True, "200"))

    # 2) http → https（強制加密）
    r = results["http_to_https"]
    ok = 300 <= r["status"] < 400 and r["location"].startswith("https://" + www)
    checks.append(_check("http_to_https", "http://" + www + "/", "301 → https",
                         ok, r["error"] or f'{r["status"]} -> {r["location"]}'))

    # 3) apex → www（單一標準網址）
    if apex != www:
        r = results["apex_to_www"]
        ok = 300 <= r["status"] < 400 and _host(r["location"]) == www
        checks.append(_check("apex_to_www", "https://" + apex + "/", "301 → www",
                             ok, r["error"] or f'{r["status"]} -> {r["location"]}'))

    # 4) robots.txt 仍放行且指向 sitemap
    r = results["robots_txt"]
    ok = r["status"] == 200 and "Sitemap:" in r["body"]
    checks.append(_check("robots_txt", site_url + "/robots.txt", "200 且含 Sitemap:",
                         ok, r["error"] or str(r["status"])))
    if ok and re.search(r"^\s*Disallow:\s*/\s*$", r["body"], re.M):
        checks.append(_check("robots_not_blocking", site_url + "/robots.txt",
                             "未全站 Disallow", False, "Disallow: /",
                             "🔴 robots.txt 全站封鎖爬蟲"))

    # 5) sitemap 可取得
    r = results["sitemap"]
    ok = r["status"] == 200 and "<sitemapindex" in r["body"]
    checks.append(_check("sitemap", site_url + "/sitemap-index.xml", "200 且為 sitemapindex",
                         ok, r["error"] or str(r["status"])))

    failures = sum(1 for c in checks if not c["ok"])
    # 不存 ran_at —— 顯示用的時間戳是 watchdog.last_run_at（run_probe 寫），存兩份會漂
    return {"ok": failures == 0, "failures": failures, "checks": checks, "site_url": site_url}


# ── 告警 ────────────────────────────────────────────────────

async def _record_event(session: AsyncSession, level: str, kind: str,
                        title: str, detail: dict) -> None:
    from db.models_website.watchdog import WebsiteWatchdogEvent
    session.add(WebsiteWatchdogEvent(
        id=uuid.uuid4().hex[:32], level=level, kind=kind, title=title[:255], detail=detail))
    await session.commit()


def _send_mail_sync(settings: dict, to_list: list[str], subject: str, body: str) -> None:
    from services.website.notify_service import _smtp_send
    _smtp_send(settings, to_list, subject, body)


async def _alert(session: AsyncSession, settings: dict, level: str, kind: str,
                 title: str, detail: dict, *, respect_cooldown: bool = True) -> None:
    """寫事件 + 寄信。持續失敗時受 24h cooldown 限制，避免灌爆信箱。"""
    await _record_event(session, level, kind, title, detail)

    if level == "info" and kind != "recovered":
        return

    cooldowns = dict(settings.get("watchdog.alert_cooldowns") or {})
    if respect_cooldown:
        last = float(cooldowns.get(kind) or 0)
        if time.time() - last < _ALERT_COOLDOWN:
            logger.info("[watchdog] %s 冷卻中，略過寄信：%s", kind, title)
            return

    cfg = _cfg_from(settings)
    from services.website.notify_service import _parse_recipients
    to_list = _parse_recipients(cfg["alert_email"] or settings.get("notify.email_to") or "")
    if not to_list:
        logger.warning("[watchdog] 無收件人，告警未寄出：%s", title)
        return

    icon = {"critical": "🔴", "warn": "🟠", "info": "🟢"}.get(level, "")
    lines = [title, "", f"時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for c in detail.get("checks", []):
        if not c.get("ok"):
            lines.append(f"✗ {c['name']}")
            lines.append(f"    網址：{c['url']}")
            lines.append(f"    預期：{c['expect']}")
            lines.append(f"    實際：{c['got']}")
            if c.get("detail"):
                lines.append(f"    說明：{c['detail']}")
            lines.append("")
    for k in ("summary", "note"):
        if detail.get(k):
            lines += [str(detail[k]), ""]
    lines.append("— Originsun 官網站點守衛")

    try:
        await asyncio.to_thread(
            _send_mail_sync, settings, to_list,
            f"{icon} [官網守衛] {title}", "\n".join(lines))
        cooldowns[kind] = time.time()
        await update_settings(session, {"watchdog.alert_cooldowns": cooldowns})
        logger.info("[watchdog] 告警已寄出 → %s", ", ".join(to_list))
    except Exception as e:  # noqa: BLE001 — 寄信失敗不能拖垮探測
        logger.error("[watchdog] 告警寄信失敗：%s", e)


# ── probe 主流程 ────────────────────────────────────────────

async def run_probe(session: AsyncSession, *, trigger: str = "cron") -> dict:
    """跑一次多 UA 篡改探測，寫 last_probe，必要時告警。"""
    settings = await get_all_settings(session)
    cfg = _cfg_from(settings)
    prev = settings.get("watchdog.last_probe") or {}
    prev_ok = bool(prev.get("ok")) if prev else None

    result = await asyncio.to_thread(_run_probe_sync, cfg["site_url"])
    await update_settings(session, {
        "watchdog.last_probe": result,
        "watchdog.last_run_at": time.time(),
    })
    logger.info("[watchdog] probe(%s) ok=%s failures=%d", trigger, result["ok"], result["failures"])

    external = [c for c in result["checks"]
                if not c["ok"] and "外部" in (c.get("detail") or "")]

    if not result["ok"]:
        level = "critical" if external else "warn"
        title = (f"偵測到外部重導（疑似遭植入）— {len(external)} 項" if external
                 else f"站點檢查失敗 — {result['failures']} 項未通過")
        # 由正常轉異常 → 立刻寄（不受 cooldown）；持續異常 → 受 24h cooldown
        await _alert(session, settings, level, "probe_redirect" if external else "probe_status",
                     title, result, respect_cooldown=(prev_ok is False))
    elif prev_ok is False:
        await _alert(session, settings, "info", "recovered",
                     "站點已恢復正常", {"summary": f"{len(result['checks'])} 項檢查全部通過"},
                     respect_cooldown=False)

    return result


# ── Search Console 收錄檢查 ─────────────────────────────────

def _sitemap_urls_sync(site_url: str, cap: int = 200) -> list[str]:
    """完整讀 sitemap（不能用 _fetch —— 它為了探測只讀前 16KB）。失敗 → 至少檢查首頁。"""
    home = site_url + "/"
    try:
        req = urllib.request.Request(site_url + "/sitemap-0.xml",
                                     headers={"User-Agent": _UAS["desktop_chrome"]})
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=_TIMEOUT) as resp:
            xml = resp.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return [home]
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    return ([home] + [u for u in urls if u != home])[:cap]   # 首頁永遠排第一


async def run_index_check(session: AsyncSession, *, trigger: str = "cron") -> dict:
    """掃 sitemap 網址的 Google 收錄狀態，upsert 進 website_index_status。"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from db.models_website.watchdog import WebsiteIndexStatus
    from services import gsc_service

    settings = await get_all_settings(session)
    cfg = _cfg_from(settings)
    sa = _load_sa(settings)
    if sa is None:
        return {"ok": False, "error": "not_configured"}

    # 先用一次呼叫驗授權。沒授權就別掃 195 個網址 —— 每個都會 403，白燒 quota，
    # 還會讓「全數失敗」看起來像網站出事，掩蓋真正的訊號。
    cred = await asyncio.to_thread(gsc_service.check_credentials, cfg["gsc_site_url"], sa)
    if not cred["ok"]:
        # 剛在 GCP 啟用 API 的頭幾分鐘，Google 的授權狀態擴散不一致 —— 同一分鐘內
        # 有的節點通、有的回 403（實測過）。重試一次再判定，免得把暫時性錯誤當成
        # 「沒授權」而中止整輪並誤發告警。
        await asyncio.sleep(20)
        cred = await asyncio.to_thread(gsc_service.check_credentials, cfg["gsc_site_url"], sa)
    if not cred["ok"]:
        await _alert(session, settings, "warn", "gsc_error",
                     "Search Console 未授權，收錄檢查已略過",
                     {"note": cred["error"] or "",
                      "summary": "請在 GCP 啟用 Search Console API，並把服務帳戶加入"
                                 "Search Console 的使用者（權限：完整）。"})
        return {"ok": False, "error": cred["error"]}

    urls = await asyncio.to_thread(_sitemap_urls_sync, cfg["site_url"])
    home = cfg["site_url"] + "/"
    indexed = not_indexed = errors = 0
    home_row: dict = {}

    for i, u in enumerate(urls):
        row = await asyncio.to_thread(gsc_service.inspect_url, cfg["gsc_site_url"], u, sa)
        if row.get("error"):
            errors += 1
        elif (row.get("verdict") or "").upper() == "PASS":
            indexed += 1
        else:
            not_indexed += 1
        if u == home:
            home_row = row

        ins = pg_insert(WebsiteIndexStatus).values(
            url=u, verdict=row.get("verdict"), coverage_state=row.get("coverage_state"),
            indexing_state=row.get("indexing_state"), robots_txt_state=row.get("robots_txt_state"),
            page_fetch_state=row.get("page_fetch_state"), google_canonical=row.get("google_canonical"),
            user_canonical=row.get("user_canonical"), last_crawl_at=row.get("last_crawl_at"),
            error=row.get("error"), checked_at=datetime.now(timezone.utc),
        )
        # set_ 必須取 excluded（= 這次要插入的值）；取 Model 欄位等於把欄位設成自己 → 永不更新
        await session.execute(ins.on_conflict_do_update(
            index_elements=["url"],
            set_={c: getattr(ins.excluded, c) for c in (
                "verdict", "coverage_state", "indexing_state", "robots_txt_state",
                "page_fetch_state", "google_canonical", "user_canonical",
                "last_crawl_at", "error", "checked_at")},
        ))
        # 分批 commit：195 個網址要跑好幾分鐘，只在最後 commit 一次的話中途完全看不到
        # 進度，而且任何中斷都會讓整輪白做。
        if (i + 1) % 20 == 0:
            await session.commit()
            logger.info("[watchdog] index 進度 %d/%d", i + 1, len(urls))
        await asyncio.sleep(0.15)      # GSC 限 600 req/min

    await session.commit()
    await update_settings(session, {"watchdog.last_index_run_at": time.time()})

    # ── 告警：首頁掉出索引 / 長期未被檢索（爬蟲被攔的早期訊號）──
    if errors and errors == len(urls):
        await _alert(session, settings, "warn", "gsc_error", "Search Console 檢查全數失敗",
                     {"note": "可能是服務帳戶未被授權，或資源網址填錯。"})
    elif home_row:
        verdict = (home_row.get("verdict") or "").upper()
        if verdict and verdict != "PASS":
            await _alert(session, settings, "critical", "index_missing",
                         f"首頁未被 Google 收錄（{verdict}）",
                         {"note": f"coverage: {home_row.get('coverage_state')}"})
        lc = home_row.get("last_crawl_at")
        if lc:
            days = (datetime.now(timezone.utc) - lc).days
            if days >= cfg["stale_days"]:
                await _alert(session, settings, "warn", "index_stale",
                             f"首頁已 {days} 天未被 Google 檢索",
                             {"note": "正常情況下數天內會回訪。爬蟲可能被 CDN/防火牆層攔截。"})

    logger.info("[watchdog] index(%s) total=%d indexed=%d not=%d err=%d",
                trigger, len(urls), indexed, not_indexed, errors)
    return {"ok": True, "total": len(urls), "indexed": indexed,
            "not_indexed": not_indexed, "errors": errors}


# ── cron loop ───────────────────────────────────────────────

async def _scheduler_loop() -> None:
    while True:
        try:
            factory = get_session_factory()
            if factory is None:
                await asyncio.sleep(60)
                continue
            async with factory() as session:
                settings = await get_all_settings(session)
                cfg = _cfg_from(settings)
                if not cfg["enabled"]:
                    await asyncio.sleep(60)
                    continue

                now = time.time()
                # 首次啟用：兩個 cron 各自播種時間戳，等下一個整點才跑（避免重啟即觸發）。
                # 播種必須在 _due 判斷「之外」—— 放進 if _due(...) 裡的話，last=0 會讓
                # _due 永遠 false，那個分支永遠進不去，該排程等於死掉。
                seed = {}
                if not float(cfg["last_run_at"] or 0):
                    seed["watchdog.last_run_at"] = now
                if not float(cfg["last_index_run_at"] or 0):
                    seed["watchdog.last_index_run_at"] = now
                if seed:
                    await update_settings(session, seed)
                    await asyncio.sleep(60)
                    continue

                async with _run_lock:
                    if cron_due(cfg["cron"], float(cfg["last_run_at"])):
                        await run_probe(session, trigger="cron")
                    if (_load_sa(settings) is not None
                            and cron_due(cfg["index_cron"], float(cfg["last_index_run_at"]))):
                        await run_index_check(session, trigger="cron")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — loop 絕不能死
            logger.error("[watchdog] scheduler loop 例外：%s", e)
        await asyncio.sleep(60)


def start_scheduler_task() -> None:
    """main.py startup 呼叫一次。只在 master（有 website/ 目錄）啟動。"""
    global _scheduler_task
    if not _is_master():
        logger.info("[watchdog] 非 master（無 website/ 目錄），不啟動排程")
        return
    if croniter is None:
        logger.warning("[watchdog] croniter 未安裝，排程停用")
        return
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("[watchdog] 站點守衛排程 loop 已啟動")
