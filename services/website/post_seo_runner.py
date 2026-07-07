"""services/website/post_seo_runner.py
---
文章版 AI SEO runner（對齊作品集 seo_runner.py）。

替「影像專欄」文章用 `claude --print` headless 生成 SEO：
    seo_title / seo_description / excerpt（空才補）/ faqs（3 題 → FAQPage rich result）

複用 seo_runner 的通用件：_call_claude（吃 Max 訂閱額度，免 API key）、_resolve_claude_exe、
名字 scrub。文章專屬：audit（缺 SEO 的 published 文章）、內文萃取、prompt、寫回 post_service。

三個觸發點（同作品集）：
1. 編輯器內「🤖 AI 生成 SEO」單篇按鈕 → POST /seo/posts/{id}/generate（回傳給編輯器填，不自動存）
2. POST /seo/posts/runner/run（admin「立即執行」整批，背景跑）
3. core 排程 loop（seo.post_ai_runner.cron，每日預設）

設定（website_settings k-v）：
    seo.post_ai_runner.enabled / cron / batch_size / last_run_at / last_run_summary / running / progress

NAS website-api 容器跑不了 claude → 透過 MASTER_RELAY_URL forward 給 master:8000。
排程 loop 只在有 claude 的 master 起（fleet/NAS gate 掉），避免搶 last_run_at。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import post_service
from .seo_runner import (
    _call_claude, _resolve_claude_exe, _has_excluded_name, _scrub_sentences,
    _build_exclusion_clause,
)
from .settings_service import get_all_settings, update_settings
from db.models_website import WebsitePost

logger = logging.getLogger(__name__)

_run_lock = asyncio.Lock()
_failure_counts: dict[int, int] = {}
_FAILURE_THRESHOLD = 3

_PROMPT_TEMPLATE = """你是繁體中文部落格的 SEO 助手，替「源日影像」（影像製作公司）的「影像專欄」文章生成 SEO 內容。

任務：根據下方文章內容，回傳一份 JSON。**只回 JSON，不要 markdown code fence 或解釋文字。**

JSON 必須含這 4 個鍵（鍵名固定）：
{{
  "seo_title": "string，30-60 字，精準描述文章主題、含核心搜尋關鍵字（可比原標題更利於搜尋，但別偏離主題）",
  "seo_description": "string，80-160 字繁中，吸引點擊的搜尋結果摘要，涵蓋主題與重點",
  "excerpt": "string，50-100 字繁中，人類可讀的文章摘要（給文章列表卡片顯示）",
  "faqs": [{{"q":"...","a":"..."}}]   // 正好 3 題：問讀者可能在 Google 搜尋的問題，答 1-2 句、訊息密度高
}}

寫作原則：
- 全部繁體中文（除專有名詞/英文）
- 不要過度行銷語氣（不寫「最棒的」「卓越的」「專業團隊精心打造」這類空話）
- faqs 的問題與答案必須能從文章內容支撐，不可編造資訊
- seo_description / excerpt 要有訊息量，不要只是換句話說標題
{exclusion_clause}
文章標題：{title}
文章分類：{category}

文章內容：
{body}
"""


# ══════════════════════════════════════════════════════════
# 內文 → 純文字 / prompt
# ══════════════════════════════════════════════════════════

def _body_to_text(body: list, limit: int = 6000) -> str:
    """PostBlock[] → 純文字（給 LLM 讀）。圖片/影片只留 caption。"""
    parts: list[str] = []
    for b in body or []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "heading":
            txt = (b.get("text") or "").strip()
            if txt:
                parts.append(f"## {txt}")
        elif t in ("paragraph", "quote"):
            txt = (b.get("text") or "").strip()
            if txt:
                parts.append(txt)
        elif t == "list":
            for it in (b.get("items") or []):
                if (it or "").strip():
                    parts.append(f"- {it}")
        elif t in ("image", "video"):
            cap = (b.get("caption") or "").strip()
            if cap:
                parts.append(f"（圖說：{cap}）")
    text = "\n".join(parts)
    return text[:limit]


async def _post_draft_context(session: AsyncSession, post_id: int) -> Optional[dict]:
    p = await session.get(WebsitePost, post_id)
    if not p:
        return None
    cat_slugs = (await post_service._load_category_slugs_for_posts(session, [p.id])).get(p.id, [])
    cats = await post_service.list_categories(session)
    label_by_slug = {c.get("slug"): c.get("label_zh") for c in cats}
    cat_labels = "、".join(label_by_slug.get(s, s) for s in cat_slugs) or "（未分類）"
    body_text = _scrub_sentences(_body_to_text(p.body or []))
    return {"title": p.title or "", "category": cat_labels, "body_text": body_text,
            "excerpt": (p.excerpt or "").strip()}


def _build_prompt(draft: dict) -> str:
    return _PROMPT_TEMPLATE.format(
        title=draft.get("title") or "(無標題)",
        category=draft.get("category") or "（未分類）",
        body=draft.get("body_text") or "(無內文)",
        exclusion_clause=_build_exclusion_clause(),
    )


def _parse_response(text: str) -> Optional[dict]:
    """抽 JSON、驗證 4 欄位、scrub 禁名、clamp 長度。失敗回 None。"""
    if not text:
        return None
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)
    if fence:
        s = fence.group(1)
    else:
        first, last = s.find("{"), s.rfind("}")
        if first != -1 and last != -1 and last > first:
            s = s[first:last + 1]
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        logger.warning("[post_seo_runner] JSON 解析失敗，前 200 字：%s", text[:200])
        return None
    if not isinstance(data, dict):
        return None
    required = {"seo_title", "seo_description", "excerpt", "faqs"}
    if not required.issubset(data.keys()) or not isinstance(data["faqs"], list):
        logger.warning("[post_seo_runner] JSON 缺欄位 / faqs 非 list：keys=%s", list(data.keys()))
        return None
    out_faqs = [
        {"q": str(q["q"])[:300], "a": str(q["a"])[:2000]}
        for q in data["faqs"]
        if isinstance(q, dict) and q.get("q") and q.get("a")
        and not _has_excluded_name(str(q.get("q"))) and not _has_excluded_name(str(q.get("a")))
    ][:5]
    return {
        "seo_title": _scrub_sentences(str(data["seo_title"]))[:200].strip(),
        "seo_description": _scrub_sentences(str(data["seo_description"]))[:300].strip(),
        "excerpt": _scrub_sentences(str(data["excerpt"]))[:500].strip(),
        "faqs": out_faqs,
    }


# ══════════════════════════════════════════════════════════
# Audit / 寫回
# ══════════════════════════════════════════════════════════

async def list_post_seo_audit(session: AsyncSession) -> list[dict]:
    """所有文章 + completeness(0-4) + needs_ai flag（published 且缺 seo_title/desc/faqs）。"""
    from sqlalchemy import asc
    rows = (await session.execute(
        select(WebsitePost).order_by(asc(WebsitePost.sort_order), asc(WebsitePost.id))
    )).scalars()
    out: list[dict] = []
    for p in rows:
        has_title = bool((p.seo_title or "").strip())
        has_desc = bool((p.seo_description or "").strip())
        has_excerpt = bool((p.excerpt or "").strip())
        has_faqs = bool(p.faqs)
        score = sum([has_title, has_desc, has_excerpt, has_faqs])
        needs_ai = (p.status == "published") and not (has_title and has_desc and has_faqs)
        out.append({
            "post_id": p.id, "slug": p.slug, "title": p.title, "status": p.status,
            "completeness": score, "needs_ai": needs_ai,
            "has_seo_title": has_title, "has_seo_desc": has_desc,
            "has_excerpt": has_excerpt, "has_faqs": has_faqs,
        })
    return out


async def _writeback(session: AsyncSession, post_id: int, parsed: dict,
                     *, fill_excerpt: bool = True) -> None:
    """寫 seo_title / seo_description / faqs；excerpt 只在原本為空時補（不蓋作者寫的）。"""
    data: dict = {
        "seo_title": parsed["seo_title"],
        "seo_description": parsed["seo_description"],
        "faqs": parsed.get("faqs") or [],
    }
    if fill_excerpt and parsed.get("excerpt"):
        p = await session.get(WebsitePost, post_id)
        if p and not (p.excerpt or "").strip():
            data["excerpt"] = parsed["excerpt"]
    await post_service.update_post(session, post_id, data)


# ══════════════════════════════════════════════════════════
# 生成（單篇，不存）/ relay
# ══════════════════════════════════════════════════════════

async def _generate_local(session: AsyncSession, post_id: int) -> dict:
    draft = await _post_draft_context(session, post_id)
    if not draft:
        return {"ok": False, "error": "文章不存在"}
    raw, call_err = await _call_claude(_build_prompt(draft))
    if not raw:
        return {"ok": False, "error": call_err or "claude --print 無回應"}
    parsed = _parse_response(raw)
    if not parsed:
        return {"ok": False, "error": f"JSON 解析失敗（前 80 字：{raw[:80]!r}）"}
    return {"ok": True, **parsed}


async def _relay_post(url: str, timeout: float) -> dict:
    """POST 到 master internal endpoint（實作收斂至 _runner_util.relay_post）。"""
    from ._runner_util import relay_post
    return await relay_post(url, timeout, on_error={
        "ok": False, "processed": 0, "skipped": 0, "errors": 1, "works": [],
    })


async def generate_for_post(session: AsyncSession, post_id: int) -> dict:
    """單篇生成（編輯器按鈕用）：回 {ok, seo_title, seo_description, excerpt, faqs}，不存 DB。"""
    relay = os.environ.get("MASTER_RELAY_URL", "").strip()
    if relay:
        url = f"{relay.rstrip('/')}/api/website/admin/internal/seo/posts/{post_id}/generate"
        return await _relay_post(url, timeout=90.0)
    return await _generate_local(session, post_id)


# ══════════════════════════════════════════════════════════
# 整批 pipeline（存 + 自動）
# ══════════════════════════════════════════════════════════

async def run_pipeline(session: AsyncSession, *, target_post_id: Optional[int] = None,
                       batch_size: int = 10, dry_run: bool = False) -> dict:
    relay = os.environ.get("MASTER_RELAY_URL", "").strip()
    if relay and not dry_run:
        if target_post_id is not None:
            url = f"{relay.rstrip('/')}/api/website/admin/internal/seo/posts/{target_post_id}/run"
            return await _relay_post(url, timeout=120.0)
        url = f"{relay.rstrip('/')}/api/website/admin/internal/seo/posts/run?batch_size={batch_size}"
        return await _relay_post(url, timeout=float(batch_size) * 200.0 + 60.0)

    if target_post_id is not None:
        targets = [target_post_id]
    else:
        audit = await list_post_seo_audit(session)
        pending = [it for it in audit if it.get("needs_ai")]
        pending.sort(key=lambda it: it.get("completeness") or 0)
        targets = [it["post_id"] for it in pending][:batch_size]

    if dry_run:
        return {"dry_run": True, "processed": 0, "skipped": 0, "errors": 0,
                "works": [{"post_id": t, "status": "would_process"} for t in targets]}

    try:
        await asyncio.wait_for(_run_lock.acquire(), timeout=0.001)
    except asyncio.TimeoutError:
        return {"status": "busy", "processed": 0, "skipped": 0, "errors": 0, "works": [],
                "error": "另一輪文章 AI runner 正在跑，請稍後再試"}

    try:
        processed = errors = 0
        works: list[dict] = []
        for pid in targets:
            try:
                r = await _generate_local(session, pid)
                if r.get("ok"):
                    await _writeback(session, pid, r, fill_excerpt=True)
                    processed += 1
                    _failure_counts.pop(pid, None)
                    works.append({"post_id": pid, "status": "ok", "detail": "已寫入 SEO + FAQ"})
                else:
                    errors += 1
                    _failure_counts[pid] = _failure_counts.get(pid, 0) + 1
                    works.append({"post_id": pid, "status": "error", "detail": r.get("error", "")})
            except Exception as e:
                logger.exception("[post_seo_runner] %s 未捕例外", pid)
                errors += 1
                works.append({"post_id": pid, "status": "error", "detail": f"未捕例外：{e}"})
            if target_post_id is None:
                try:
                    await update_settings(session, {"seo.post_ai_runner.progress": f"{len(works)}/{len(targets)}"})
                except Exception:
                    pass

        if target_post_id is None:
            try:
                await update_settings(session, {
                    "seo.post_ai_runner.last_run_at": time.time(),
                    "seo.post_ai_runner.last_run_summary": {"processed": processed, "errors": errors, "works": works},
                })
            except Exception as e:
                logger.warning("[post_seo_runner] 寫 last_run_* 失敗：%s", e)

        return {"processed": processed, "skipped": 0, "errors": errors, "works": works}
    finally:
        _run_lock.release()


# ── 整批 = 非同步背景跑（避開 cloudflared/nginx 逾時）──
_batch_task: Optional[asyncio.Task] = None


def is_batch_running() -> bool:
    return _batch_task is not None and not _batch_task.done()


async def _batch_runner(batch_size: int) -> None:
    from db.session import get_session_factory
    factory = get_session_factory()
    if factory is None:
        return
    try:
        async with factory() as s:
            await update_settings(s, {"seo.post_ai_runner.running": True,
                                      "seo.post_ai_runner.progress": f"0/{batch_size}"})
    except Exception:
        logger.warning("[post_seo_runner] 設 running 旗標失敗")
    result = None
    try:
        async with factory() as s:
            result = await run_pipeline(s, batch_size=batch_size)
    except Exception:
        logger.exception("[post_seo_runner] _batch_runner 異常")
    processed = (result or {}).get("processed", 0)
    busy = (result or {}).get("status") == "busy"
    try:
        async with factory() as s:
            patch = {"seo.post_ai_runner.running": False}
            if result is None or busy:
                patch["seo.post_ai_runner.last_run_at"] = time.time()
                patch["seo.post_ai_runner.last_run_summary"] = {
                    "processed": 0, "errors": 0, "works": [],
                    "note": "另一輪文章 AI runner 正在跑，已略過" if busy else "背景執行異常",
                }
            await update_settings(s, patch)
    except Exception:
        pass
    if processed > 0:
        try:
            from . import rebuild_service
            await rebuild_service.mark_dirty()
        except Exception as e:
            logger.warning("[post_seo_runner] mark_dirty 失敗：%s", e)


def start_batch_bg(batch_size: int) -> bool:
    global _batch_task
    if is_batch_running() or _run_lock.locked():
        return False
    _batch_task = asyncio.create_task(_batch_runner(batch_size))
    return True


async def trigger_batch(session: AsyncSession, batch_size: int) -> dict:
    """「立即執行」整批非同步觸發：立即回 {started}/{busy}/{error}。NAS forward 給 master。"""
    relay = os.environ.get("MASTER_RELAY_URL", "").strip()
    if relay:
        url = f"{relay.rstrip('/')}/api/website/admin/internal/seo/posts/run?batch_size={batch_size}"
        r = await _relay_post(url, timeout=15.0)
        return r if r.get("status") else ({"status": "started"} if r.get("ok", True) and "error" not in r else {"status": "error", "error": r.get("error")})
    if not start_batch_bg(batch_size):
        return {"status": "busy"}
    return {"status": "started"}


# ══════════════════════════════════════════════════════════
# 設定
# ══════════════════════════════════════════════════════════

async def get_runner_settings(session: AsyncSession) -> dict:
    s = await get_all_settings(session)
    return {
        "enabled": s.get("seo.post_ai_runner.enabled") is True,
        "cron": str(s.get("seo.post_ai_runner.cron") or "30 3 * * *"),
        "batch_size": int(s.get("seo.post_ai_runner.batch_size") or 10),
        "last_run_at": s.get("seo.post_ai_runner.last_run_at"),
        "last_run_summary": s.get("seo.post_ai_runner.last_run_summary"),
        "running": s.get("seo.post_ai_runner.running") is True,
        "progress": str(s.get("seo.post_ai_runner.progress") or ""),
    }


async def update_runner_settings(session: AsyncSession, payload: dict, *, by: Optional[str] = None) -> dict:
    ALLOWED = {"enabled", "cron", "batch_size"}
    to_write = {f"seo.post_ai_runner.{k}": v for k, v in payload.items() if k in ALLOWED}
    if "cron" in payload and payload["cron"]:
        from ._runner_util import validate_cron
        validate_cron(str(payload["cron"]))
    if to_write:
        await update_settings(session, to_write, updated_by=by)
    return await get_runner_settings(session)


# ══════════════════════════════════════════════════════════
# Cron 排程 loop（只在有 claude 的 master 起）
# ══════════════════════════════════════════════════════════

_scheduler_task: Optional[asyncio.Task] = None


async def _scheduler_loop() -> None:
    try:
        from croniter import croniter
    except ImportError:
        logger.warning("[post_seo_runner] croniter 未安裝，排程 loop 不啟動")
        return
    if _resolve_claude_exe() is None:
        logger.info("[post_seo_runner] 本機無 claude CLI — 文章 AI SEO 排程不啟動（只在 master 跑）")
        return

    from db.session import get_session_factory
    await asyncio.sleep(50)
    try:
        _f = get_session_factory()
        if _f:
            async with _f() as _s:
                await update_settings(_s, {"seo.post_ai_runner.running": False})
    except Exception:
        pass

    while True:
        try:
            factory = get_session_factory()
            if factory is None:
                await asyncio.sleep(60)
                continue
            async with factory() as session:
                settings = await get_runner_settings(session)
                if settings["enabled"] and settings["cron"]:
                    last_at = float(settings["last_run_at"] or 0)
                    if last_at <= 0:
                        # 首次啟用 → 立即跑一輪（不等下一個 cron 整點，避免「啟用了卻
                        # 看起來沒動」），run_pipeline 會寫 last_run_at，之後照 cron。
                        due = True
                    else:
                        try:
                            next_due = croniter(settings["cron"],
                                                datetime.fromtimestamp(last_at)).get_next(datetime)
                        except (ValueError, KeyError, TypeError) as e:
                            logger.warning("[post_seo_runner] cron 格式錯 %r: %s", settings["cron"], e)
                            await asyncio.sleep(60)
                            continue
                        due = datetime.now() >= next_due
                    if due:
                        logger.info("[post_seo_runner] 觸發 — run_pipeline")
                        result = await run_pipeline(session, batch_size=settings["batch_size"])
                        if result.get("processed", 0) > 0:
                            try:
                                from . import rebuild_service
                                await rebuild_service.mark_dirty()
                            except Exception as e:
                                logger.warning("[post_seo_runner] mark_dirty 失敗：%s", e)
        except Exception:
            logger.exception("[post_seo_runner] scheduler loop 異常")
        await asyncio.sleep(60)


def start_scheduler_task() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("[post_seo_runner] 文章 SEO 排程 loop 已啟動")
