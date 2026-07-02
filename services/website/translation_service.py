"""services/website/translation_service.py
---
英文翻譯（transcreation）runner —— 對齊 post_seo_runner.py 的架構。

用 `claude --print` headless 把中文內容「在地化改寫」成專業英文（不是逐字直譯），
寫進各實體的 `_en` 欄，並在 website_translation_state 記錄工作流狀態。

涵蓋 3 型別：
- work（crm_projects.public_*）：public_title, public_description
- post（website_posts）：title, excerpt, seo_title, seo_description, body(blocks)
- service（website_services）：title, short_desc, full_desc

🔴 專有名詞不翻：客戶名（public_client）、演職員人名 → 一律 skip（只有手動指定英文）。
    prompt 也要求「品牌/專有名詞保留原樣」。

觸發點（同 AI SEO）：
1. 單一實體「立即翻譯」→ generate_for_entity（回傳建議，不自動存 / 存看 auto_approve）
2. 「立即執行」整批背景 → trigger_batch
3. 排程 loop（translation.runner.cron）— 只在有 claude 的 master 起

NAS website-api 容器跑不了 claude → 透過 MASTER_RELAY_URL forward 給 master:8000。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .seo_runner import _call_claude, _resolve_claude_exe, _scrub_sentences
from .settings_service import get_all_settings, update_settings
from db.models import CrmProject
from db.models_website import WebsitePost, WebsiteService, WebsiteTranslationState

logger = logging.getLogger(__name__)

_run_lock = asyncio.Lock()

# ── 每型別的「要翻譯的簡單文字欄位」（key = 中文欄位名，_en 欄一律 = key+"_en"）──
# public_client / credits 人名不在此 → 不翻（專有名詞）。
_WORK_FIELDS = ["public_title", "public_description"]
_POST_FIELDS = ["title", "excerpt", "seo_title", "seo_description"]
_SERVICE_FIELDS = ["title", "short_desc", "full_desc"]


_PROMPT_TEMPLATE = """You are a professional English copywriter localizing website content for "源日影像 Originsun Studio" — a video production company in Taipei (brand line: "Best Story, Best Production").

This is TRANSCREATION, not literal translation: rewrite the Traditional-Chinese content into natural, professional, marketing-grade English for an international audience. Preserve the meaning and brand positioning, but it must read like it was written by a native English copywriter — never translationese.

Rules:
- KEEP proper nouns exactly as-is: company/brand names, people's names, client names, product/work names. Do not translate or transliterate them.
- Tone: professional, confident, concise. NO empty hype ("the best", "world-class", "cutting-edge", "meticulously crafted").
- Match the source's intent and length roughly; don't pad.
{brand_voice}{glossary}
Return ONLY a JSON object (no markdown code fence, no explanation) whose keys are exactly the requested field names below, each ending in "_en":
{fields_spec}

Source (Traditional Chinese):
{content}
"""

# 每型別的 model / 中文欄位（集中一處，避免散落各 function 重複宣告）。
_ENTITY_MODEL = {"work": CrmProject, "post": WebsitePost, "service": WebsiteService}
_ENTITY_FIELDS = {"work": _WORK_FIELDS, "post": _POST_FIELDS, "service": _SERVICE_FIELDS}


# ══════════════════════════════════════════════════════════
# 收集 / hash
# ══════════════════════════════════════════════════════════

def _block_texts(body: list) -> list[tuple[int, str]]:
    """PostBlock[] → [(index, translatable_text)]。只挑有文字的 block（保留 index 對回）。"""
    out: list[tuple[int, str]] = []
    for i, b in enumerate(body or []):
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t in ("heading", "paragraph", "quote") and (b.get("text") or "").strip():
            out.append((i, b["text"].strip()))
        elif t in ("image", "video") and (b.get("caption") or "").strip():
            out.append((i, b["caption"].strip()))
        elif t == "list":
            for j, it in enumerate(b.get("items") or []):
                if (it or "").strip():
                    out.append((_list_key(i, j), it.strip()))
    return out


def _list_key(block_i: int, item_j: int) -> int:
    """把 list item 的 (block, item) 編成單一 int key（block*1000+item）避免衝突。"""
    return block_i * 1000 + 100000 + item_j


def _collect_zh(entity_type: str, obj) -> dict[str, str]:
    """回傳非空的中文欄位 {欄位名: 中文}（work 的 title 空則用 name）。"""
    fields = _ENTITY_FIELDS[entity_type]
    zh: dict[str, str] = {}
    for f in fields:
        v = (getattr(obj, f, None) or "").strip()
        if not v and entity_type == "work" and f == "public_title":
            v = (getattr(obj, "name", None) or "").strip()
        if v:
            zh[f] = v
    return zh


def _source_hash(entity_type: str, obj) -> str:
    """中文來源雜湊（含 body）→ 中文改過就變 → 偵測過時。"""
    parts = [f"{k}={v}" for k, v in sorted(_collect_zh(entity_type, obj).items())]
    if entity_type == "post":
        parts.append("body=" + "|".join(t for _, t in _block_texts(obj.body or [])))
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()[:64]


async def _entities(session: AsyncSession, entity_type: str) -> list:
    if entity_type == "work":
        stmt = select(CrmProject).where(CrmProject.public.is_(True))
    elif entity_type == "post":
        stmt = select(WebsitePost).where(WebsitePost.status == "published")
    else:
        stmt = select(WebsiteService).where(WebsiteService.visible.is_(True))
    return list((await session.execute(stmt)).scalars())


# ══════════════════════════════════════════════════════════
# Audit（哪些需要翻 / 過時）
# ══════════════════════════════════════════════════════════

async def _state_map(session: AsyncSession) -> dict[tuple[str, str], WebsiteTranslationState]:
    rows = (await session.execute(select(WebsiteTranslationState))).scalars()
    return {(r.entity_type, r.entity_id): r for r in rows}


async def list_audit(session: AsyncSession) -> list[dict]:
    """所有可翻實體 + 狀態（missing / stale / translated / approved）。"""
    states = await _state_map(session)
    out: list[dict] = []
    for etype in ("work", "post", "service"):
        for obj in await _entities(session, etype):
            eid = str(obj.id)
            zh = _collect_zh(etype, obj)
            if not zh and etype != "post":
                continue
            src_hash = _source_hash(etype, obj)
            st = states.get((etype, eid))
            if st is None:
                status = "missing"
            elif st.source_hash != src_hash and st.status in ("translated", "approved"):
                status = "stale"
            else:
                status = st.status
            title = zh.get("public_title") or zh.get("title") or getattr(obj, "name", "") or eid
            out.append({
                "entity_type": etype, "entity_id": eid, "title": title,
                "status": status,
                "needs_ai": status in ("missing", "stale", "pending"),
                "last_translated_at": st.last_translated_at.isoformat() if st and st.last_translated_at else None,
                "reviewed_by": st.reviewed_by if st else None,
            })
    return out


# ══════════════════════════════════════════════════════════
# Prompt / parse
# ══════════════════════════════════════════════════════════

def _extra_clauses(settings: dict) -> tuple[str, str]:
    bv = (settings.get("translation.brand_voice") or "").strip()
    gl = settings.get("translation.glossary") or ""
    brand = f"- Brand voice guidance: {bv}\n" if bv else ""
    if isinstance(gl, dict):
        gl = "; ".join(f"{k}→{v}" for k, v in gl.items())
    gl = str(gl).strip()
    glossary = f"- Glossary (use these fixed English terms): {gl}\n" if gl else ""
    return brand, glossary


def _build_prompt(zh: dict[str, str], body_pairs: list[tuple[int, str]], settings: dict) -> str:
    brand, glossary = _extra_clauses(settings)
    field_lines = [f'- "{k}_en": rewrite of the {k} below' for k in zh]
    content_lines = [f"[{k}]\n{v}" for k, v in zh.items()]
    if body_pairs:
        field_lines.append('- "body_en": a JSON array of {"i": <same index>, "text": "<English rewrite>"} for each numbered body segment below')
        seg = "\n".join(f"#{i}: {t}" for i, t in body_pairs)
        content_lines.append(f"[body segments]\n{seg}")
    return _PROMPT_TEMPLATE.format(
        brand_voice=brand, glossary=glossary,
        fields_spec="\n".join(field_lines),
        content="\n\n".join(content_lines),
    )


def _parse(text: str, zh: dict[str, str], has_body: bool) -> Optional[dict]:
    if not text:
        return None
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)
    if fence:
        s = fence.group(1)
    else:
        a, b = s.find("{"), s.rfind("}")
        if a != -1 and b > a:
            s = s[a:b + 1]
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        logger.warning("[translation] JSON 解析失敗，前 200：%s", text[:200])
        return None
    if not isinstance(data, dict):
        return None
    out: dict = {}
    for k in zh:
        v = data.get(f"{k}_en")
        if isinstance(v, str) and v.strip():
            out[f"{k}_en"] = _scrub_sentences(v.strip())
    if has_body:
        bl = data.get("body_en")
        if isinstance(bl, list):
            out["body_en"] = [
                {"i": int(x["i"]), "text": str(x["text"])}
                for x in bl if isinstance(x, dict) and "i" in x and "text" in x
            ]
    return out or None


def _apply_body_en(zh_body: list, trans: list[dict]) -> list:
    """用翻譯結果重建 body_en：複製 zh body、把 trans 對應 index 的文字換英文、保留結構/圖。"""
    new = [dict(b) if isinstance(b, dict) else b for b in (zh_body or [])]
    for t in trans:
        i, en = t["i"], t["text"]
        if not en:
            continue
        if i >= 100000:  # list item：block*1000+100000+item
            bi, ij = (i - 100000) // 1000, (i - 100000) % 1000
            if bi < len(new) and isinstance(new[bi], dict):
                items = list(new[bi].get("items") or [])
                if ij < len(items):
                    items[ij] = en
                    new[bi]["items"] = items
        elif i < len(new) and isinstance(new[i], dict):
            key = "caption" if new[i].get("type") in ("image", "video") else "text"
            new[i][key] = en
    return new


# ══════════════════════════════════════════════════════════
# 生成（單實體）
# ══════════════════════════════════════════════════════════

async def _get_entity(session: AsyncSession, etype: str, eid: str):
    model = _ENTITY_MODEL[etype]
    key = eid if etype == "work" else int(eid)
    return await session.get(model, key)


async def _generate_local(session: AsyncSession, etype: str, eid: str,
                          settings: Optional[dict] = None) -> dict:
    obj = await _get_entity(session, etype, eid)
    if obj is None:
        return {"ok": False, "error": "實體不存在"}
    zh = _collect_zh(etype, obj)
    body_pairs = _block_texts(obj.body or []) if etype == "post" else []
    if not zh and not body_pairs:
        return {"ok": False, "error": "沒有可翻譯的中文內容"}
    if settings is None:   # 整批時 caller 傳入避免每筆重查整張 settings 表
        settings = await get_all_settings(session)
    raw, err = await _call_claude(_build_prompt(zh, body_pairs, settings))
    if not raw:
        return {"ok": False, "error": err or "claude --print 無回應"}
    parsed = _parse(raw, zh, bool(body_pairs))
    if not parsed:
        return {"ok": False, "error": f"JSON 解析失敗（前 80：{raw[:80]!r}）"}
    return {"ok": True, "fields": parsed, "zh": zh,
            "body_segments": len(body_pairs), "source_hash": _source_hash(etype, obj)}


async def _writeback(session: AsyncSession, etype: str, eid: str, parsed: dict,
                     src_hash: str, *, approve: bool, by: str = "ai") -> None:
    obj = await _get_entity(session, etype, eid)
    if obj is None:
        return
    for k, v in parsed.items():
        if k == "body_en":
            obj.body_en = _apply_body_en(obj.body or [], v)
        else:
            setattr(obj, k, v)   # k 已是 {field}_en
    st = await session.get(WebsiteTranslationState, {"entity_type": etype, "entity_id": eid})
    if st is None:
        st = WebsiteTranslationState(entity_type=etype, entity_id=eid)
        session.add(st)
    st.source_hash = src_hash
    st.status = "approved" if approve else "translated"
    st.last_translated_at = datetime.now()
    st.last_translated_by = by
    if approve:
        st.reviewed_by = by
        st.reviewed_at = datetime.now()
    await session.commit()


async def generate_for_entity(session: AsyncSession, etype: str, eid: str) -> dict:
    """單實體生成（編輯用）：回建議翻譯，不自動存。NAS forward master。"""
    relay = os.environ.get("MASTER_RELAY_URL", "").strip()
    if relay:
        url = f"{relay.rstrip('/')}/api/website/admin/internal/translation/{etype}/{eid}/generate"
        return await _relay(url, timeout=120.0)
    return await _generate_local(session, etype, eid)


# ══════════════════════════════════════════════════════════
# 整批 pipeline
# ══════════════════════════════════════════════════════════

async def run_pipeline(session: AsyncSession, *, batch_size: int = 10, dry_run: bool = False) -> dict:
    relay = os.environ.get("MASTER_RELAY_URL", "").strip()
    if relay and not dry_run:
        url = f"{relay.rstrip('/')}/api/website/admin/internal/translation/run?batch_size={batch_size}"
        return await _relay(url, timeout=float(batch_size) * 200.0 + 60.0)

    audit = await list_audit(session)
    pending = [it for it in audit if it["needs_ai"]][:batch_size]
    if dry_run:
        return {"dry_run": True, "processed": 0, "errors": 0,
                "items": [{"entity_type": it["entity_type"], "entity_id": it["entity_id"],
                           "status": "would_process"} for it in pending]}

    try:
        await asyncio.wait_for(_run_lock.acquire(), timeout=0.001)
    except asyncio.TimeoutError:
        return {"status": "busy", "processed": 0, "errors": 0, "items": [],
                "error": "另一輪翻譯正在跑，請稍後再試"}
    try:
        settings = await get_all_settings(session)
        auto = settings.get("translation.auto_approve") is True
        processed = errors = 0
        items: list[dict] = []
        for it in pending:
            etype, eid = it["entity_type"], it["entity_id"]
            try:
                r = await _generate_local(session, etype, eid, settings)
                if r.get("ok"):
                    await _writeback(session, etype, eid, r["fields"], r["source_hash"], approve=auto)
                    processed += 1
                    items.append({"entity_type": etype, "entity_id": eid, "status": "ok"})
                else:
                    errors += 1
                    items.append({"entity_type": etype, "entity_id": eid, "status": "error",
                                  "detail": r.get("error", "")})
            except Exception as e:
                logger.exception("[translation] %s/%s 未捕例外", etype, eid)
                errors += 1
                items.append({"entity_type": etype, "entity_id": eid, "status": "error", "detail": str(e)})
            try:
                await update_settings(session, {"translation.runner.progress": f"{len(items)}/{len(pending)}"})
            except Exception:
                pass
        try:
            await update_settings(session, {
                "translation.runner.last_run_at": time.time(),
                "translation.runner.last_run_summary": {"processed": processed, "errors": errors, "items": items},
            })
        except Exception as e:
            logger.warning("[translation] 寫 last_run_* 失敗：%s", e)
        return {"processed": processed, "errors": errors, "items": items}
    finally:
        _run_lock.release()


# ── 背景批次 ──
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
            await update_settings(s, {"translation.runner.running": True,
                                      "translation.runner.progress": f"0/{batch_size}"})
    except Exception:
        pass
    result = None
    try:
        async with factory() as s:
            result = await run_pipeline(s, batch_size=batch_size)
    except Exception:
        logger.exception("[translation] _batch_runner 異常")
    try:
        async with factory() as s:
            patch = {"translation.runner.running": False}
            if result is None or result.get("status") == "busy":
                patch["translation.runner.last_run_at"] = time.time()
                patch["translation.runner.last_run_summary"] = {
                    "processed": 0, "errors": 0, "items": [],
                    "note": "另一輪正在跑，已略過" if result and result.get("status") == "busy" else "背景執行異常",
                }
            await update_settings(s, patch)
    except Exception:
        pass
    if (result or {}).get("processed", 0) > 0:
        try:
            from . import rebuild_service
            await rebuild_service.mark_dirty()
        except Exception as e:
            logger.warning("[translation] mark_dirty 失敗：%s", e)


def start_batch_bg(batch_size: int) -> bool:
    global _batch_task
    if is_batch_running() or _run_lock.locked():
        return False
    _batch_task = asyncio.create_task(_batch_runner(batch_size))
    return True


async def trigger_batch(session: AsyncSession, batch_size: int) -> dict:
    relay = os.environ.get("MASTER_RELAY_URL", "").strip()
    if relay:
        url = f"{relay.rstrip('/')}/api/website/admin/internal/translation/run?batch_size={batch_size}"
        r = await _relay(url, timeout=15.0)
        return r if r.get("status") else {"status": "error", "error": r.get("error")}
    if not start_batch_bg(batch_size):
        return {"status": "busy"}
    return {"status": "started"}


async def _relay(url: str, timeout: float) -> dict:
    import httpx
    from core.auth import _get_secret
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers={"X-Internal-Key": _get_secret()})
            if r.status_code == 200:
                return r.json()
            return {"ok": False, "error": f"master relay 失敗 (HTTP {r.status_code}): {r.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": f"master 離線或超時：{e}"}


# ══════════════════════════════════════════════════════════
# 設定 + 排程（對齊 post_seo_runner）
# ══════════════════════════════════════════════════════════

async def get_runner_settings(session: AsyncSession) -> dict:
    s = await get_all_settings(session)
    return {
        "enabled": s.get("translation.runner.enabled") is True,
        "cron": str(s.get("translation.runner.cron") or "0 4 * * *"),
        "batch_size": int(s.get("translation.runner.batch_size") or 10),
        "auto_approve": s.get("translation.auto_approve") is True,
        "brand_voice": str(s.get("translation.brand_voice") or ""),
        "glossary": s.get("translation.glossary") or "",
        "last_run_at": s.get("translation.runner.last_run_at"),
        "last_run_summary": s.get("translation.runner.last_run_summary"),
        "running": s.get("translation.runner.running") is True,
        "progress": str(s.get("translation.runner.progress") or ""),
    }


async def update_runner_settings(session: AsyncSession, payload: dict, *, by: Optional[str] = None) -> dict:
    KEYMAP = {
        "enabled": "translation.runner.enabled", "cron": "translation.runner.cron",
        "batch_size": "translation.runner.batch_size", "auto_approve": "translation.auto_approve",
        "brand_voice": "translation.brand_voice", "glossary": "translation.glossary",
    }
    if payload.get("cron"):
        cron_str = str(payload["cron"])
        try:
            from croniter import croniter
            croniter(cron_str)
        except ImportError:
            if len(cron_str.split()) != 5:
                raise ValueError(f"cron 需 5 欄位：{cron_str!r}")
        except Exception as e:
            raise ValueError(f"cron 不合法：{cron_str!r}（{e}）")
    to_write = {KEYMAP[k]: v for k, v in payload.items() if k in KEYMAP}
    if to_write:
        await update_settings(session, to_write, updated_by=by)
    return await get_runner_settings(session)


_scheduler_task: Optional[asyncio.Task] = None


async def _scheduler_loop() -> None:
    try:
        from croniter import croniter
    except ImportError:
        logger.warning("[translation] croniter 未安裝，排程不啟動")
        return
    if _resolve_claude_exe() is None:
        logger.info("[translation] 本機無 claude — 翻譯排程不啟動（只在 master 跑）")
        return
    from db.session import get_session_factory
    await asyncio.sleep(55)
    while True:
        try:
            factory = get_session_factory()
            if factory is None:
                await asyncio.sleep(60)
                continue
            async with factory() as session:
                st = await get_runner_settings(session)
                if st["enabled"] and st["cron"]:
                    last_at = float(st["last_run_at"] or 0)
                    if last_at <= 0:
                        due = True
                    else:
                        try:
                            next_due = croniter(st["cron"], datetime.fromtimestamp(last_at)).get_next(datetime)
                        except (ValueError, KeyError, TypeError) as e:
                            logger.warning("[translation] cron 錯 %r: %s", st["cron"], e)
                            await asyncio.sleep(60)
                            continue
                        due = datetime.now() >= next_due
                    if due:
                        logger.info("[translation] 觸發 — run_pipeline")
                        r = await run_pipeline(session, batch_size=st["batch_size"])
                        if r.get("processed", 0) > 0:
                            try:
                                from . import rebuild_service
                                await rebuild_service.mark_dirty()
                            except Exception:
                                pass
        except Exception:
            logger.exception("[translation] scheduler loop 異常")
        await asyncio.sleep(60)


def start_scheduler_task() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("[translation] 翻譯排程 loop 已啟動")
