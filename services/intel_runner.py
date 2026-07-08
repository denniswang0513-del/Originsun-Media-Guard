"""services/intel_runner.py
---
產業情報 runner（P-c，docs/PREPROD_PLAN.md C 段）—— 第四個 AI runner，骨架抄
social_runner：白名單來源（intel_sources 表）每日抓 RSS → 關鍵字過濾 → url sha1
去重 → 新項批次丟 `claude --print` 摘要/分類/評分/抽截止日 → 寫 intel_items
（status=new）→ 後台「📡 產業情報」工作台看板（高分標案/補助可一鍵轉提案）。

複用既有共用件：seo_runner._call_claude / _resolve_claude_exe（吃 Max 訂閱額度）、
_runner_util.validate_cron、website_settings k-v（settings_service — dev/生產 DB
分庫，天然隔離 enabled 開關）。

兩個觸發點（同 social_runner，但這是內部工具、router 在 master 的
routers/api_intel.py，無 NAS relay 路徑）：
1. core 排程 loop（intel.cron，預設每日 08:30 錯開社群 09:00；只在有 claude 的 master 起）
2. POST /api/v1/intel/run（admin 立即執行，背景跑）

設定（website_settings k-v，缺 key 用 _DEFAULTS）：
    intel.enabled            bool   false（⚠ 預設關 — dev 與生產都要 owner 手動開，防 claude 額度）
    intel.cron               str    "30 8 * * *"
    intel.last_run_at / last_run_summary / running   runner 狀態

守則（與其他 runner 一致）：白名單外不抓、每源單次上限 50 項、全跑上限 200 項、
只存標題+摘要+原文連結（不轉貼全文，版權）、claude 不可用/失敗降級存原始資料
（category=未分類、score=0）、連續 3 次 claude 呼叫失敗推 ai_runner_failed（kind=intel）。
零新 pip 依賴：RSS 解析用 stdlib xml.etree、HTTP 用 urllib.request（生產跑 python_embed）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import IntelItem, IntelSource
from services.website.seo_runner import _call_claude, _resolve_claude_exe
from services.website.settings_service import get_all_settings, update_settings

logger = logging.getLogger(__name__)

# 同 process 鎖避免多處同時觸發（cron loop / admin 立即執行）
_run_lock = asyncio.Lock()

# 連續 claude「呼叫」失敗計數（in-memory；達門檻推播 ai_runner_failed 後歸零）。
# 只計 CLI 呼叫本身失敗（Max 到期/登出/CLI 壞）；回應了但 JSON 解析失敗走降級
# 不計失敗 — 那是回應品質問題，項目仍以原始資料入庫。
_consecutive_failures = 0
_FAILURE_THRESHOLD = 3

_UA = "OMG-intel/1.0"
_HTTP_TIMEOUT = 15           # 秒
_PER_SOURCE_CAP = 50         # 每源單次抓取上限
_TOTAL_CAP = 200             # 全跑（跨源合計）上限
_CLAUDE_BATCH = 20           # 一次 prompt 最多 20 項，超過分批

CATEGORIES = ("標案", "補助", "產業", "技術", "競品", "未分類")

_DEFAULTS: dict[str, Any] = {
    "intel.enabled": False,   # ⚠ 預設關（owner 手動開，防 claude 額度）
    "intel.cron": "30 8 * * *",
}


def _setting(s: dict, key: str) -> Any:
    """DB 值優先（含明確存 false/0），缺 key 或存 null 才用 _DEFAULTS。"""
    v = s.get(key)
    if key in s and v is not None:
        return v
    return _DEFAULTS.get(key)


def _cfg_from(s: dict) -> dict:
    return {
        "enabled": _setting(s, "intel.enabled") is True,
        "cron": str(_setting(s, "intel.cron") or "30 8 * * *"),
        "last_run_at": s.get("intel.last_run_at"),
        "last_run_summary": s.get("intel.last_run_summary"),
        "running": s.get("intel.runner.running") is True,
    }


async def get_intel_settings(session: AsyncSession) -> dict:
    """讀 intel.* 設定 + runner 狀態（「📡 產業情報」狀態列用）。"""
    return _cfg_from(await get_all_settings(session))


# payload key → website_settings key（PUT /api/v1/intel/settings 白名單）
_ALLOWED_SETTINGS = {
    "enabled": "intel.enabled",
    "cron": "intel.cron",
}


async def update_intel_settings(
    session: AsyncSession, payload: dict, *, by: Optional[str] = None,
) -> dict:
    """admin 更新設定（只接受 _ALLOWED_SETTINGS 白名單鍵）。

    Raises ValueError if cron 非法（router 轉 422）。
    """
    to_write = {_ALLOWED_SETTINGS[k]: v for k, v in payload.items() if k in _ALLOWED_SETTINGS}
    if "cron" in payload and payload["cron"]:
        from services.website._runner_util import validate_cron
        validate_cron(str(payload["cron"]))
    if "enabled" in payload:
        to_write["intel.enabled"] = payload["enabled"] is True
    if to_write:
        await update_settings(session, to_write, updated_by=by)
    return await get_intel_settings(session)


# ══════════════════════════════════════════════════════════
# RSS 抓取與解析（stdlib only — 生產 python_embed 不裝 feedparser/requests）
# ══════════════════════════════════════════════════════════

def _fetch_url(url: str) -> bytes:
    """同步抓取（呼叫端用 asyncio.to_thread 包）。timeout 15s、自訂 UA。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
        return resp.read()


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    """去 HTML tag + 摺疊空白（description 常帶 markup；只存純文字摘要）。"""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", s or "")).strip()


def _local(tag: str) -> str:
    """ElementTree tag → 去 namespace 的小寫 local name。"""
    return tag.rsplit("}", 1)[-1].lower()


def _parse_feed(data: bytes) -> list[dict]:
    """RSS 2.0 <item> / Atom <entry> → [{title, link, description, pub}]。

    stdlib ElementTree、namespace 無關（比對 local name）。解析失敗回 []。
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out: list[dict] = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        title = link = desc = pub = ""
        for child in node:
            t = _local(child.tag)
            txt = (child.text or "").strip()
            if t == "title" and not title:
                title = txt
            elif t == "link" and not link:
                link = txt or (child.get("href") or "").strip()   # Atom 的 link 在 href attr
            elif t in ("description", "summary", "content") and not desc:
                desc = txt
            elif t in ("pubdate", "published", "updated", "date") and not pub:
                pub = txt
        if link:
            out.append({
                "title": _strip_html(title)[:512],
                "link": link[:512],
                "description": _strip_html(desc)[:1500],
                "pub": pub[:64],
            })
    return out


def _match_keywords(entry: dict, keywords: Any) -> bool:
    """title+description 含任一關鍵字即收；keywords 空 = 全收。"""
    kws = [str(k).strip() for k in (keywords or []) if str(k).strip()]
    if not kws:
        return True
    hay = f"{entry['title']} {entry['description']}".lower()
    return any(k.lower() in hay for k in kws)


def _url_hash(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════
# claude 摘要/分類/評分/抽截止日（批次；失敗降級存原始資料）
# ══════════════════════════════════════════════════════════

def _build_prompt(batch: list[dict]) -> str:
    lines = []
    for i, e in enumerate(batch):
        lines.append(f"[{i}] 標題：{e['title'] or '(無標題)'}")
        if e["description"]:
            lines.append(f"    內容：{e['description'][:500]}")
        if e["pub"]:
            lines.append(f"    發布：{e['pub']}")
    return (
        "你是台灣影像製作公司「源日影像」的產業情報分析師。公司業務：形象影片、廣告、"
        "紀錄片、活動紀實、政府影像標案、多媒體內容製作。\n"
        "以下編號條目是今日自動蒐集的產業資訊（標題+摘要）。請針對每一條輸出：\n"
        "- summary：80 字內繁體中文摘要（只根據給定內容，不可編造）\n"
        "- category：只能是「標案 / 補助 / 產業 / 技術 / 競品」其中一個詞\n"
        "- score：0-100 整數 — 對公司「接案商機」的相關性"
        "（影片/影像/宣傳類標案與補助給高分，無關內容給低分）\n"
        "- deadline：內文若有投標/申請截止日 → \"YYYY-MM-DD\"，沒有 → null\n\n"
        "輸出格式：只回一個 JSON 陣列，每項 "
        "{\"idx\": <條目編號>, \"summary\": \"...\", \"category\": \"...\", "
        "\"score\": <int>, \"deadline\": \"YYYY-MM-DD\" 或 null}。"
        "不要 code fence、不要任何解釋文字。\n\n條目：\n" + "\n".join(lines)
    )


def _parse_json_array(text: str) -> Optional[list]:
    """從 claude 回應抽 JSON 陣列（code fence / 前後贅字容錯）。失敗回 None。"""
    if not text:
        return None
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*\])\s*```", s, re.DOTALL)
    if fence:
        s = fence.group(1)
    else:
        first, last = s.find("["), s.rfind("]")
        if first != -1 and last > first:
            s = s[first:last + 1]
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        logger.warning("[intel_runner] JSON 解析失敗，前 200 字：%s", text[:200])
        return None
    return data if isinstance(data, list) else None


def _parse_deadline(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _degraded(e: dict) -> dict:
    """claude 不可用/失敗時的降級標註：存原始 title/description，未分類 0 分。"""
    return {"summary": e["description"][:500], "category": "未分類",
            "score": 0, "deadline": None, "degraded": True}


async def _classify_batch(batch: list[dict]) -> tuple[dict[int, dict], str]:
    """一批（≤20 項）丟 claude。回 ({idx: {summary, category, score, deadline}}, call_err)。

    call_err 非空 = CLI 呼叫本身失敗（計入連續失敗告警）；
    呼叫成功但解析失敗 → 回空 dict + 空 err（呼叫端降級、不計失敗）。
    """
    raw, call_err = await _call_claude(_build_prompt(batch))
    if not raw:
        return {}, call_err or "claude --print 無回應或失敗"
    parsed = _parse_json_array(raw)
    if parsed is None:
        return {}, ""
    out: dict[int, dict] = {}
    for ent in parsed:
        if not isinstance(ent, dict):
            continue
        try:
            idx = int(ent.get("idx"))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(batch):
            continue
        cat = str(ent.get("category") or "").strip()
        try:
            score = max(0, min(100, int(ent.get("score") or 0)))
        except (TypeError, ValueError):
            score = 0
        out[idx] = {
            "summary": str(ent.get("summary") or "").strip()[:500] or batch[idx]["description"][:500],
            "category": cat if cat in CATEGORIES else "未分類",
            "score": score,
            "deadline": _parse_deadline(ent.get("deadline")),
            "degraded": False,
        }
    return out, ""


async def _note_failure(err: str) -> None:
    """連續失敗計數 → 達門檻推播 ai_runner_failed（best-effort）後歸零。"""
    global _consecutive_failures
    _consecutive_failures += 1
    if _consecutive_failures >= _FAILURE_THRESHOLD:
        try:
            from notifier import notify_tab_async  # type: ignore
            await notify_tab_async(
                "ai_runner_failed", kind="intel",
                fails=_consecutive_failures, error=(err or "")[:200])
        except Exception:
            pass
        _consecutive_failures = 0


def _reset_failures() -> None:
    global _consecutive_failures
    _consecutive_failures = 0


# ══════════════════════════════════════════════════════════
# Pipeline 主流程
# ══════════════════════════════════════════════════════════

async def run_pipeline(session: AsyncSession, force: bool = False) -> dict:
    """跑一輪產業情報 pipeline。

    Args:
        session: DB session
        force: True = 略過 enabled 開關（手動測試用；排程與 admin run 都不帶）

    Returns:
        {status, fetched, matched, new, saved, degraded, truncated, errors}
    """
    settings_all = await get_all_settings(session)
    cfg = _cfg_from(settings_all)
    if not cfg["enabled"] and not force:
        return {"status": "disabled", "fetched": 0, "new": 0, "saved": 0}

    try:
        await asyncio.wait_for(_run_lock.acquire(), timeout=0.001)
    except asyncio.TimeoutError:
        return {"status": "busy", "fetched": 0, "new": 0, "saved": 0,
                "error": "另一輪產業情報 runner 正在跑，請稍後再試"}
    try:
        summary = await _run_locked(session)
        await update_settings(session, {
            "intel.last_run_at": time.time(),
            "intel.last_run_summary": summary,
        })
        return summary
    finally:
        _run_lock.release()


async def _run_locked(session: AsyncSession) -> dict:
    now = datetime.now(timezone.utc)
    sources = (await session.execute(
        select(IntelSource).where(IntelSource.enabled == 1)
        .order_by(IntelSource.created_at)
    )).scalars().all()

    errors: list[str] = []
    fetched = matched = truncated = 0
    candidates: list[dict] = []          # {src_id, title, link, description, pub, hash}
    seen_hashes: set[str] = set()        # 本輪內去重（跨源同 URL）

    for src in sources:
        if (src.type or "rss") != "rss":
            continue  # HTML 來源第一版先跳過不做
        try:
            data = await asyncio.to_thread(_fetch_url, src.url)
        except Exception as e:
            errors.append(f"{src.name or src.url}: 抓取失敗 {e}")
            continue
        entries = _parse_feed(data)
        if not entries:
            errors.append(f"{src.name or src.url}: 無法解析出任何條目")
            src.last_fetched_at = now
            continue
        if len(entries) > _PER_SOURCE_CAP:
            logger.info("[intel_runner] %s: %d 項超過每源上限 %d，截斷 %d 項",
                        src.name or src.url, len(entries), _PER_SOURCE_CAP,
                        len(entries) - _PER_SOURCE_CAP)
            truncated += len(entries) - _PER_SOURCE_CAP
            entries = entries[:_PER_SOURCE_CAP]
        fetched += len(entries)
        for e in entries:
            if not _match_keywords(e, src.keywords):
                continue
            matched += 1
            h = _url_hash(e["link"])
            if h in seen_hashes:
                continue
            seen_hashes.add(h)
            if len(candidates) >= _TOTAL_CAP:
                truncated += 1
                continue
            candidates.append({**e, "src_id": src.id, "hash": h})
        src.last_fetched_at = now
    await session.commit()  # last_fetched_at（無新項也要記「掃過了」）

    if truncated:
        logger.info("[intel_runner] 本輪合計截斷 %d 項（每源上限 %d / 全跑上限 %d）",
                    truncated, _PER_SOURCE_CAP, _TOTAL_CAP)

    # DB 既有 url_hash 去重
    new_items = candidates
    if candidates:
        existing = {
            h for (h,) in await session.execute(
                select(IntelItem.url_hash)
                .where(IntelItem.url_hash.in_([c["hash"] for c in candidates])))
        }
        new_items = [c for c in candidates if c["hash"] not in existing]

    if not new_items:
        return {"status": "ok", "sources": len(sources), "fetched": fetched,
                "matched": matched, "new": 0, "saved": 0, "degraded": 0,
                "truncated": truncated, "errors": errors}

    # claude 摘要/分類/評分（批次 ≤20；CLI 不可用 → 整輪降級存原始資料）
    results: dict[int, dict] = {}
    if _resolve_claude_exe() is None:
        logger.warning("[intel_runner] 本機無 claude CLI — 全部降級存原始資料")
        results = {i: _degraded(e) for i, e in enumerate(new_items)}
    else:
        for start in range(0, len(new_items), _CLAUDE_BATCH):
            batch = new_items[start:start + _CLAUDE_BATCH]
            mapping, call_err = await _classify_batch(batch)
            if call_err:
                await _note_failure(call_err)
                errors.append(f"claude 批次失敗（{len(batch)} 項降級）：{call_err[:120]}")
            elif mapping:
                _reset_failures()
            for i in range(len(batch)):
                results[start + i] = mapping.get(i) or _degraded(batch[i])
    degraded = sum(1 for r in results.values() if r["degraded"])

    saved = 0
    for i, e in enumerate(new_items):
        r = results[i]
        session.add(IntelItem(
            id=uuid.uuid4().hex,
            source_id=e["src_id"],
            url=e["link"],
            url_hash=e["hash"],
            title=e["title"][:512],
            summary=r["summary"],
            category=r["category"],
            score=r["score"],
            deadline=r["deadline"],
            status="new",
            fetched_at=now,
        ))
        saved += 1
    await session.commit()

    return {"status": "ok", "sources": len(sources), "fetched": fetched,
            "matched": matched, "new": len(new_items), "saved": saved,
            "degraded": degraded, "truncated": truncated, "errors": errors}


# ══════════════════════════════════════════════════════════
# 「立即執行」= 非同步背景跑（避開長請求逾時，同 social_runner）
# ══════════════════════════════════════════════════════════

_run_task: Optional[asyncio.Task] = None


def is_run_running() -> bool:
    return _run_task is not None and not _run_task.done()


async def _bg_runner() -> None:
    """背景跑一輪：自管 session、設 running 旗標。"""
    from db.session import get_session_factory
    factory = get_session_factory()
    if factory is None:
        return
    try:
        async with factory() as s:
            await update_settings(s, {"intel.runner.running": True})
    except Exception:
        logger.warning("[intel_runner] 設 running 旗標失敗")
    try:
        async with factory() as s:
            await run_pipeline(s)
    except Exception:
        logger.exception("[intel_runner] _bg_runner 異常")
    try:
        async with factory() as s:
            await update_settings(s, {"intel.runner.running": False})
    except Exception:
        pass


def start_run_bg() -> bool:
    """背景啟動一輪 run（立即回）。False = 已有一輪在跑（含 cron 正佔鎖）。"""
    global _run_task
    if is_run_running() or _run_lock.locked():
        return False
    _run_task = asyncio.create_task(_bg_runner())
    return True


async def trigger_run() -> dict:
    """admin「立即抓取」觸發點：立即回 {started}/{busy}（enabled 開關在
    run_pipeline 內把關 — 關著會記 disabled、不燒 claude 額度）。"""
    if not start_run_bg():
        return {"status": "busy"}
    return {"status": "started"}


# ══════════════════════════════════════════════════════════
# Cron 排程背景 loop（只在有 claude 的 master 起，同 social_runner gate）
# ══════════════════════════════════════════════════════════

_scheduler_task: Optional[asyncio.Task] = None


async def _scheduler_loop() -> None:
    """每 60 秒掃 settings 看 cron 是否到期 → run_pipeline。"""
    try:
        from croniter import croniter
    except ImportError:
        logger.warning("[intel_runner] croniter 未安裝，排程 loop 不啟動")
        return
    # ⚠ 只在「有 claude 的機器」（= master）跑：全機隊共用 mediaguard DB，
    # 沒 gate 的話每台 agent 都會搶著抓 RSS 寫同一張表（同 seo/social gate 理由）。
    if _resolve_claude_exe() is None:
        logger.info("[intel_runner] 本機無 claude CLI — 產業情報排程 loop 不啟動（只在 master 跑）")
        return

    from db.session import get_session_factory
    await asyncio.sleep(58)  # 錯開 seo(45)/post_seo(50)/social(55) 的啟動檢查

    # 開機一次性：清殘留 running 旗標（上一輪被 OTA/crash 中途殺掉時 DB 會卡 True）
    try:
        _f = get_session_factory()
        if _f:
            async with _f() as _s:
                await update_settings(_s, {"intel.runner.running": False})
    except Exception:
        pass

    while True:
        try:
            factory = get_session_factory()
            if factory is None:
                await asyncio.sleep(60)
                continue
            async with factory() as session:
                cfg = await get_intel_settings(session)
                if cfg["enabled"] and cfg["cron"]:
                    last_at = float(cfg["last_run_at"] or 0)
                    if last_at <= 0:
                        # 首次啟用：播種 last_run_at=現在、等下一個 cron 整點才跑
                        # （不首次立即跑 — 部署/重啟當下就燒 claude 額度不可接受，
                        # 同 social_runner 的取捨）。
                        await update_settings(session, {"intel.last_run_at": time.time()})
                        await asyncio.sleep(60)
                        continue
                    base_dt = datetime.fromtimestamp(last_at)  # naive 本地時區
                    try:
                        next_due = croniter(cfg["cron"], base_dt).get_next(datetime)
                    except (ValueError, KeyError, TypeError) as e:
                        logger.warning("[intel_runner] cron 格式錯誤 %r: %s", cfg["cron"], e)
                        await asyncio.sleep(60)
                        continue
                    if datetime.now() >= next_due:
                        logger.info("[intel_runner] cron 觸發（next_due=%s）— run_pipeline",
                                    next_due.isoformat())
                        await run_pipeline(session)
        except Exception:
            logger.exception("[intel_runner] scheduler loop 異常")
        await asyncio.sleep(60)


def start_scheduler_task() -> None:
    """main.py startup 呼叫一次 — 啟動背景 cron loop。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("[intel_runner] 產業情報排程 loop 已啟動")
