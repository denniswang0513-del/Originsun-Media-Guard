"""services/website/social_runner.py
---
社群編輯自動化（Phase N-soc 階段一）runner —— 骨架抄 seo_runner / post_seo_runner
（第三個 AI runner）：每日掃新內容（作品/文章/公益案例）→ 無新內容走常青輪播 →
每選題 × 每啟用平台用 `claude --print` 產一篇平台文稿 → 寫入 website_social_posts
（status=draft）→ Google Chat 推「今日社群任務」摘要 → 編輯在後台「📣 社群工作台」審核。

複用 seo_runner 的通用件：_call_claude（吃 Max 訂閱額度，免 API key）、_resolve_claude_exe。
素材查詢重用既有 service：project_service / post_service / initiative_service / seo_service。

三個觸發點（同 seo_runner）：
1. core 排程 loop（social.runner.cron，預設每日 09:00；只在有 claude 的 master 起）
2. POST /api/website/admin/social/run（admin 立即執行，背景跑）
3. run_pipeline(session, force_topic="work:<id>")（指定選題手動測，不動水位/不受頻率上限）

設定（website_settings k-v，缺 key 用 _DEFAULTS；owner 2026-07-07 拍板值）：
    social.enabled              bool   true
    social.platforms            list   ["facebook","instagram","threads"]
    social.slots                list   ["12:00","19:00"]（發佈時段，階段二發佈器用，先存）
    social.daily_cap            int    1（每平台每日產稿上限，status != rejected 計數）
    social.weekly_cap           int    4（每平台近 7 天上限）
    social.evergreen_gap_days   int    90（常青重推：同一作品 N 天內不重複）
    social.autopublish          bool   false（階段二 kill switch；階段一僅存）
    social.brand_voice          str    品牌調性補充（空 = 不注入）
    social.tpl.facebook/.instagram/.threads   各平台語氣模板（prompt 開頭段）
    social.runner.cron          str    "0 9 * * *"
    social.runner.last_run_at / last_run_summary / running   runner 狀態
    social.last_scan_at         epoch  新內容掃描水位（有產出才推進；全失敗不推進 → 下輪重試）

NAS website-api 容器跑不了 claude → MASTER_RELAY_URL forward 給 master:8000
（POST /api/website/admin/internal/social/run，X-Internal-Key = JWT secret）。
連續 3 次 claude 失敗 → notifier `ai_runner_failed` 模板（kind='社群文稿'）告警。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models_website import WebsiteInitiative, WebsiteSocialPost
from . import initiative_service, post_service, project_service
from .seo_runner import _call_claude, _resolve_claude_exe
from .settings_service import get_all_settings, update_settings

logger = logging.getLogger(__name__)

# 同 process 鎖避免多處同時觸發（cron loop / admin 立即執行）
_run_lock = asyncio.Lock()

# 連續 claude 失敗計數（in-memory；達門檻推播 ai_runner_failed 後歸零）。
# 與 seo_runner 的 per-project 計數不同：社群選題是一次性的（失敗的選題留在
# 水位前、下輪重試），所以用「全域連續失敗」抓的是 claude CLI 本身掛掉
# （Max 到期/登出/CLI 壞）—— 一輪 3 平台全失敗就會觸發告警。
_consecutive_failures = 0
_FAILURE_THRESHOLD = 3

PLATFORMS = ("facebook", "instagram", "threads")

# ══════════════════════════════════════════════════════════
# 預設值（owner 2026-07-07 拍板 — 缺 key 時的 code fallback，同 seo_runner 慣例）
# ══════════════════════════════════════════════════════════

_TPL_FACEBOOK = (
    "你是「源日影像」（影像製作公司）的社群編輯，為 Facebook 粉絲專頁寫一則貼文。\n"
    "風格：敘事長文，2-3 個自然段。用說故事的口吻帶出這件作品/文章的脈絡與亮點，\n"
    "讓沒看過的人想點進去。段落開頭可用 1-2 個 emoji 點綴，不要洗版。\n"
    "不要條列式、不要過度行銷用語（禁用「最強」「頂尖」「業界第一」這類空話）。\n"
    "結尾另起一行，直接附上官網連結（完整 URL）。"
)

_TPL_INSTAGRAM = (
    "你是「源日影像」（影像製作公司）的社群編輯，為 Instagram 帳號寫一則貼文文案。\n"
    "風格：精煉短文（3-5 行內），視覺化、有畫面感的語言；行與行之間可空行留白。\n"
    "內文最後寫一句「完整作品連結在 bio」（Instagram 內文連結點不了，不要貼 URL）。\n"
    "結尾空一行後放 5-8 個相關 hashtag（繁中/英文混搭，第一個固定 #源日影像）。"
)

_TPL_THREADS = (
    "你是「源日影像」（影像製作公司）的社群編輯，為 Threads 寫一則貼文。\n"
    "風格：口語、輕鬆，像跟同好聊天。1-2 句話的鉤子（勾起好奇或共鳴），\n"
    "然後直接附上官網連結（完整 URL）。不用 hashtag，emoji 最多 1 個。"
)

_DEFAULTS: dict[str, Any] = {
    "social.enabled": True,
    "social.platforms": ["facebook", "instagram", "threads"],
    "social.slots": ["12:00", "19:00"],
    "social.daily_cap": 1,
    "social.weekly_cap": 4,
    "social.evergreen_gap_days": 90,
    "social.autopublish": False,
    "social.brand_voice": "",
    "social.tpl.facebook": _TPL_FACEBOOK,
    "social.tpl.instagram": _TPL_INSTAGRAM,
    "social.tpl.threads": _TPL_THREADS,
    "social.runner.cron": "0 9 * * *",
}


def _setting(s: dict, key: str) -> Any:
    """DB 值優先（含明確存 false/0），缺 key 或存 null 才用 _DEFAULTS。"""
    v = s.get(key)
    if key in s and v is not None:
        return v
    return _DEFAULTS.get(key)


def _cfg_from(s: dict) -> dict:
    """settings dict → 結構化 runner 設定（給 pipeline / admin UI 共用）。"""
    return {
        "enabled": _setting(s, "social.enabled") is True,
        "platforms": [p for p in (_setting(s, "social.platforms") or []) if p in PLATFORMS],
        "slots": list(_setting(s, "social.slots") or []),
        "daily_cap": int(_setting(s, "social.daily_cap") or 1),
        "weekly_cap": int(_setting(s, "social.weekly_cap") or 4),
        "evergreen_gap_days": int(_setting(s, "social.evergreen_gap_days") or 90),
        "autopublish": _setting(s, "social.autopublish") is True,
        "brand_voice": str(_setting(s, "social.brand_voice") or ""),
        "tpl": {p: str(_setting(s, f"social.tpl.{p}") or "") for p in PLATFORMS},
        "cron": str(_setting(s, "social.runner.cron") or "0 9 * * *"),
        "last_run_at": s.get("social.runner.last_run_at"),
        "last_run_summary": s.get("social.runner.last_run_summary"),
        "last_scan_at": s.get("social.last_scan_at"),
        "running": s.get("social.runner.running") is True,
    }


async def get_social_settings(session: AsyncSession) -> dict:
    """讀 social.* 設定 + runner 狀態（admin「📣 社群工作台」設定卡用）。"""
    return _cfg_from(await get_all_settings(session))


# payload key → website_settings key（PUT /admin/social/settings 白名單）
_ALLOWED_SETTINGS = {
    "enabled": "social.enabled",
    "platforms": "social.platforms",
    "slots": "social.slots",
    "daily_cap": "social.daily_cap",
    "weekly_cap": "social.weekly_cap",
    "evergreen_gap_days": "social.evergreen_gap_days",
    "autopublish": "social.autopublish",
    "brand_voice": "social.brand_voice",
    "tpl_facebook": "social.tpl.facebook",
    "tpl_instagram": "social.tpl.instagram",
    "tpl_threads": "social.tpl.threads",
    "cron": "social.runner.cron",
}


async def update_social_settings(
    session: AsyncSession, payload: dict, *, by: Optional[str] = None,
) -> dict:
    """admin 更新設定（只接受 _ALLOWED_SETTINGS 白名單鍵）。

    Raises ValueError if cron 非法 / platforms 含未知平台（router 轉 422）。
    """
    to_write = {_ALLOWED_SETTINGS[k]: v for k, v in payload.items() if k in _ALLOWED_SETTINGS}
    if "cron" in payload and payload["cron"]:
        cron_str = str(payload["cron"])
        # ⚠ NAS 容器沒裝 croniter：ImportError 退化成 5 欄位結構檢查（同 seo_runner）
        try:
            from croniter import croniter
            croniter(cron_str)
        except ImportError:
            if len(cron_str.split()) != 5:
                raise ValueError(f"cron 需 5 個欄位（分 時 日 月 週）：{cron_str!r}")
        except Exception as e:
            raise ValueError(f"cron 字串不合法：{cron_str!r}（{e}）")
    if "platforms" in payload:
        plats = payload["platforms"]
        if not isinstance(plats, list) or any(p not in PLATFORMS for p in plats):
            raise ValueError(f"platforms 只接受 {list(PLATFORMS)} 的子集合")
    if to_write:
        await update_settings(session, to_write, updated_by=by)
    return await get_social_settings(session)


# ══════════════════════════════════════════════════════════
# 佇列 CRUD（給 routers/website/admin_social.py 用）
# ══════════════════════════════════════════════════════════

def _post_to_dict(p: WebsiteSocialPost) -> dict:
    return {
        "id": p.id,
        "source_type": p.source_type,
        "source_id": p.source_id,
        "platform": p.platform,
        "content": p.content,
        "media_url": p.media_url,
        "status": p.status,
        "scheduled_at": p.scheduled_at.isoformat() if p.scheduled_at else None,
        "published_url": p.published_url,
        "error_detail": p.error_detail,
        "run_id": p.run_id,
        "reviewed_by": p.reviewed_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


async def list_queue(session: AsyncSession, status: Optional[str] = None) -> list[dict]:
    """社群文稿佇列（新→舊）。status 篩選可選（draft/approved/published/rejected）。"""
    stmt = select(WebsiteSocialPost)
    if status:
        stmt = stmt.where(WebsiteSocialPost.status == status)
    stmt = stmt.order_by(WebsiteSocialPost.created_at.desc(), WebsiteSocialPost.id.desc())
    return [_post_to_dict(p) for p in (await session.execute(stmt)).scalars()]


_EDITABLE_FIELDS = {"content", "media_url", "scheduled_at"}


async def update_queue_post(session: AsyncSession, post_id: str, data: dict) -> Optional[dict]:
    """編輯改稿（content / media_url / scheduled_at）。找不到回 None。"""
    obj = await session.get(WebsiteSocialPost, post_id)
    if not obj:
        return None
    for k, v in data.items():
        if k in _EDITABLE_FIELDS:
            setattr(obj, k, v)
    await session.commit()
    await session.refresh(obj)
    return _post_to_dict(obj)


async def set_queue_status(
    session: AsyncSession, post_id: str, status: str,
    *, reviewed_by: Optional[str] = None, published_url: Optional[str] = None,
) -> Optional[dict]:
    """審核狀態流轉：approve/reject 記 reviewed_by；published 回寫 published_url。"""
    obj = await session.get(WebsiteSocialPost, post_id)
    if not obj:
        return None
    obj.status = status
    if reviewed_by is not None:
        obj.reviewed_by = reviewed_by
    if published_url is not None:
        obj.published_url = published_url
    await session.commit()
    await session.refresh(obj)
    return _post_to_dict(obj)


# ══════════════════════════════════════════════════════════
# 選題（新內容掃描 → 常青輪播 fallback）
# ══════════════════════════════════════════════════════════

def _epoch(v: Any) -> float:
    """datetime / ISO 字串 / None → epoch float（None/解析失敗 → 0）。"""
    if not v:
        return 0.0
    if isinstance(v, datetime):
        dt = v
    else:
        try:
            dt = datetime.fromisoformat(str(v))
        except ValueError:
            return 0.0
    if dt.tzinfo is None:
        dt = dt.astimezone()  # naive 視為本地時間
    return dt.timestamp()


def _abs_url(site_base: str, u: str) -> str:
    if u and u.startswith("/"):
        return site_base + u
    return u or ""


def _topic_from_work(w: dict, *, source_type: str, ts: float) -> dict:
    slug = str(w.get("slug") or "")
    return {
        "source_type": source_type,          # 'work' 或 'evergreen'（常青重推）
        "source_id": str(w.get("id")),
        "title": str(w.get("title") or ""),
        "description": str(w.get("description") or ""),
        "seo_description": "",               # 生成前由 _enrich_work_seo 補（website_project_seo）
        "url_path": f"/works/{slug}" if slug else "/portfolio",
        "image_url": str(w.get("cover_url") or ""),
        "extra": {"客戶": w.get("client") or "", "年份": w.get("year") or ""},
        "ts": ts,
    }


def _topic_from_post(p: dict, ts: float) -> dict:
    return {
        "source_type": "post",
        "source_id": str(p.get("id")),
        "title": str(p.get("title") or ""),
        "description": str(p.get("excerpt") or ""),
        "seo_description": str(p.get("seo_description") or ""),
        "url_path": f"/news/{p.get('slug')}",
        "image_url": str(p.get("og_image_url") or p.get("cover_url") or ""),
        "extra": {},
        "ts": ts,
    }


def _topic_from_initiative(it: dict, ts: float) -> dict:
    line = str(it.get("line") or "impact")
    link = str(it.get("link_url") or "") or f"/{line}"
    return {
        "source_type": "initiative",
        "source_id": str(it.get("id")),
        "title": str(it.get("title") or it.get("work_title") or ""),
        "description": str(it.get("summary") or ""),
        "seo_description": "",
        "url_path": link if link.startswith("/") else "",
        "image_url": str(it.get("cover_url") or it.get("work_cover") or ""),
        "extra": {"專案線": "公益合作" if line == "impact" else "創作計畫", "_raw_link": link},
        "ts": ts,
    }


async def _pushed_sources(session: AsyncSession) -> set[tuple[str, str]]:
    """已有任何社群文稿紀錄的 (source_type, source_id) 集合。"""
    rows = await session.execute(
        select(WebsiteSocialPost.source_type, WebsiteSocialPost.source_id).distinct()
    )
    return {(t, i) for t, i in rows}


async def _select_topics(session: AsyncSession, cfg: dict) -> list[dict]:
    """選題：新內容（無紀錄 + 建立/發布時間 > 水位）優先，新→舊；
    無新內容 → 常青輪播挑一件「最近 evergreen_gap_days 內沒推過」的 public 作品。"""
    last_scan = float(cfg.get("last_scan_at") or 0)
    pushed = await _pushed_sources(session)
    # 作品的「已推過」同時看 work 與 evergreen（常青重推也算推過）
    work_pushed_ids = {sid for (st, sid) in pushed if st in ("work", "evergreen")}

    topics: list[dict] = []

    # 1) 新公開作品
    works = await project_service.list_admin_projects(session, include_non_public=False)
    for w in works:
        wid = str(w.get("id"))
        ts = _epoch(w.get("published_at"))
        if wid in work_pushed_ids or ts <= last_scan:
            continue
        topics.append(_topic_from_work(w, source_type="work", ts=ts))

    # 2) 新發布文章（影像專欄）
    posts, _total = await post_service.list_posts(session, visible_only=True, limit=200)
    for p in posts:
        pid = str(p.get("id"))
        ts = _epoch(p.get("published_at"))
        if ("post", pid) in pushed or ts <= last_scan:
            continue
        full = await post_service.get_post(session, int(pid)) or p  # 補 seo_description / og_image
        topics.append(_topic_from_post(full, ts))

    # 3) 新公益/創作案例
    inits = await initiative_service.list_admin(session)
    created_map = {
        row[0]: row[1] for row in
        (await session.execute(select(WebsiteInitiative.id, WebsiteInitiative.created_at))).all()
    }
    for it in inits:
        if not it.get("visible"):
            continue
        iid = str(it.get("id"))
        ts = _epoch(created_map.get(it.get("id")))
        if ("initiative", iid) in pushed or ts <= last_scan:
            continue
        topic = _topic_from_initiative(it, ts)
        if topic["title"]:
            topics.append(topic)

    if topics:
        topics.sort(key=lambda t: t["ts"], reverse=True)  # 最新內容先
        return topics

    # 4) 常青輪播：gap 天內沒推過的 public 作品挑一件（從未推過的優先，其次最久沒推）
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg["evergreen_gap_days"])
    rows = await session.execute(
        select(WebsiteSocialPost.source_id, func.max(WebsiteSocialPost.created_at))
        .where(WebsiteSocialPost.source_type.in_(("work", "evergreen")))
        .group_by(WebsiteSocialPost.source_id)
    )
    last_push = {sid: mx for sid, mx in rows}
    candidates = []
    for w in works:
        lp = last_push.get(str(w.get("id")))
        if lp is None or lp < cutoff:
            candidates.append((lp, w))
    if not candidates:
        return []
    _min_dt = datetime.min.replace(tzinfo=timezone.utc)
    candidates.sort(key=lambda x: (x[0] is not None, x[0] or _min_dt))
    w = candidates[0][1]
    return [_topic_from_work(w, source_type="evergreen", ts=_epoch(w.get("published_at")))]


async def _enrich_work_seo(session: AsyncSession, topic: dict) -> None:
    """作品選題生成前補 SEO 描述素材（website_project_seo；best-effort）。"""
    try:
        from . import seo_service
        ctx = await seo_service.get_project_seo_draft_context(session, topic["source_id"])
        if ctx:
            cur = ctx.get("current_seo") or {}
            topic["seo_description"] = str(cur.get("seo_description") or "")
            if not topic["description"]:
                topic["description"] = str(ctx.get("description") or "")
    except Exception:
        pass


async def _resolve_force_topic(session: AsyncSession, force_topic: str) -> list[dict]:
    """手動指定選題："work:<id>" / "post:<id>" / "initiative:<id>"（無前綴視為 work）。"""
    kind, _, sid = force_topic.partition(":")
    if not sid:
        kind, sid = "work", kind
    kind = kind.strip().lower()
    sid = sid.strip()
    if kind in ("work", "evergreen"):
        works = await project_service.list_admin_projects(session, include_non_public=False)
        for w in works:
            if str(w.get("id")) == sid:
                return [_topic_from_work(w, source_type=kind, ts=_epoch(w.get("published_at")))]
    elif kind == "post":
        try:
            full = await post_service.get_post(session, int(sid))
        except ValueError:
            full = None
        if full:
            return [_topic_from_post(full, _epoch(full.get("published_at")))]
    elif kind == "initiative":
        for it in await initiative_service.list_admin(session):
            if str(it.get("id")) == sid:
                return [_topic_from_initiative(it, 0.0)]
    return []


# ══════════════════════════════════════════════════════════
# 頻率上限守衛
# ══════════════════════════════════════════════════════════

async def _platform_counts(session: AsyncSession, since: datetime) -> dict[str, int]:
    """since 之後各平台已產文稿數（status != rejected — 退回重寫不佔額度）。"""
    rows = await session.execute(
        select(WebsiteSocialPost.platform, func.count())
        .where(WebsiteSocialPost.status != "rejected",
               WebsiteSocialPost.created_at >= since)
        .group_by(WebsiteSocialPost.platform)
    )
    return {p: c for p, c in rows}


# ══════════════════════════════════════════════════════════
# 文稿生成（prompt → claude --print → 純文字）
# ══════════════════════════════════════════════════════════

_COMMON_RULES = (
    "通用規則：\n"
    "- 全部繁體中文（人名/片名/專有名詞除外）\n"
    "- 只能根據下方素材寫，事實不可編造（素材沒提的細節不要腦補）\n"
    "- 直接輸出貼文內容本身：不要 markdown code fence、不要標題行、不要任何解釋"
)


def _build_prompt(topic: dict, platform: str, cfg: dict, site_base: str) -> str:
    url = _abs_url(site_base, topic.get("url_path") or "")
    material = [
        f"標題：{topic['title']}",
        f"描述：{topic['description'] or '(無)'}",
        f"SEO 描述：{topic['seo_description'] or '(無)'}",
        f"官網連結：{url or '(無)'}",
        f"配圖（OG 圖）：{_abs_url(site_base, topic.get('image_url') or '') or '(無)'}",
    ]
    for label, val in (topic.get("extra") or {}).items():
        if val and not label.startswith("_"):
            material.append(f"{label}：{val}")
    parts = [cfg["tpl"].get(platform, "").strip()]
    brand = cfg["brand_voice"].strip()
    if brand:
        parts.append(f"品牌調性（寫作時遵守）：\n{brand}")
    parts.append(_COMMON_RULES)
    parts.append("素材：\n" + "\n".join(material))
    return "\n\n".join(p for p in parts if p)


def _clean_output(raw: str) -> str:
    """claude 輸出清理：去頭尾空白 + 剝掉整段包住的 code fence（容錯）。"""
    s = (raw or "").strip()
    if s.startswith("```") and s.endswith("```"):
        s = s[3:-3].strip()
        if s.lower().startswith(("text", "markdown")):  # ```text\n... 的語言標籤
            s = s.split("\n", 1)[1].strip() if "\n" in s else ""
    return s


async def _generate_one(topic: dict, platform: str, cfg: dict, site_base: str) -> tuple[Optional[str], str]:
    """單篇文稿。成功 → (content, "")；失敗 → (None, 中文診斷)。"""
    prompt = _build_prompt(topic, platform, cfg, site_base)
    logger.info("[social_runner] %s/%s (%s): prompt %d chars",
                topic["source_type"], topic["source_id"], platform, len(prompt))
    raw, call_err = await _call_claude(prompt)
    if not raw:
        return None, call_err or "claude --print 無回應或失敗"
    content = _clean_output(raw)
    if not content:
        return None, "claude 回傳空文稿"
    return content, ""


async def _note_failure(err: str) -> None:
    """連續失敗計數 → 達門檻推播 ai_runner_failed（best-effort）後歸零。"""
    global _consecutive_failures
    _consecutive_failures += 1
    if _consecutive_failures >= _FAILURE_THRESHOLD:
        try:
            from notifier import notify_tab_async  # type: ignore
            await notify_tab_async(
                "ai_runner_failed", kind="社群文稿",
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

async def _relay_run_to_master(relay_url: str) -> dict:
    """NAS website-api → master:8000 的「啟動」relay（master 背景跑、立即回）。"""
    import httpx
    from core.auth import _get_secret
    url = f"{relay_url.rstrip('/')}/api/website/admin/internal/social/run"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, headers={"X-Internal-Key": _get_secret()})
            if r.status_code == 200:
                return r.json()
            return {"status": "error", "error": f"master relay 失敗 (HTTP {r.status_code}): {r.text[:200]}"}
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        return {"status": "error", "error": f"master 離線或超時：{e}"}
    except Exception as e:
        return {"status": "error", "error": f"relay 錯誤：{e}"}


async def run_pipeline(session: AsyncSession, force_topic: Optional[str] = None) -> dict:
    """跑一輪社群文稿 pipeline。

    Args:
        session: DB session
        force_topic: "work:<id>" / "post:<id>" / "initiative:<id>" — 指定選題手動測；
                     跳過新內容掃描與頻率上限、不推進水位（給之後手動測 claude 產文用）。

    Returns:
        {generated, errors, items: [{source_type, source_id, platform, status, title, detail}]}
    """
    # NAS 容器沒 claude → forward 給 master（master 沒設 MASTER_RELAY_URL，走本機路徑）
    relay = os.environ.get("MASTER_RELAY_URL", "").strip()
    if relay:
        return await _relay_run_to_master(relay)

    settings_all = await get_all_settings(session)
    cfg = _cfg_from(settings_all)
    if not cfg["enabled"]:
        return {"status": "disabled", "generated": 0, "errors": 0, "items": []}

    try:
        await asyncio.wait_for(_run_lock.acquire(), timeout=0.001)
    except asyncio.TimeoutError:
        return {"status": "busy", "generated": 0, "errors": 0, "items": [],
                "error": "另一輪社群 runner 正在跑，請稍後再試"}

    try:
        run_id = uuid.uuid4().hex
        site_base = (str(settings_all.get("seo.site_url") or "") or "https://originsun-studio.com").rstrip("/")

        if force_topic:
            topics = await _resolve_force_topic(session, force_topic)
            if not topics:
                return {"generated": 0, "errors": 1, "items": [],
                        "error": f"force_topic 找不到來源：{force_topic!r}"}
        else:
            topics = await _select_topics(session, cfg)

        if not topics:
            # 沒新內容也沒常青候選 → 水位照推進（沒有東西會漏掉），記 summary 讓 UI 有回饋
            summary = {"generated": 0, "errors": 0, "items": [], "note": "無新內容且無常青候選"}
            await update_settings(session, {
                "social.last_scan_at": time.time(),
                "social.runner.last_run_at": time.time(),
                "social.runner.last_run_summary": summary,
            })
            return summary

        # 頻率上限：當日（本地日界）/ 近 7 天，per platform 計 status != rejected
        now_local = datetime.now().astimezone()
        today_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = now_local - timedelta(days=7)
        day_counts = await _platform_counts(session, today_start)
        week_counts = await _platform_counts(session, week_start)

        generated = errors = 0
        items: list[dict] = []
        for topic in topics:
            avail = [
                p for p in cfg["platforms"]
                if force_topic or (day_counts.get(p, 0) < cfg["daily_cap"]
                                   and week_counts.get(p, 0) < cfg["weekly_cap"])
            ]
            if not avail:
                break  # 全平台額度用完 — 剩餘選題留在水位前，之後的 run 再消化
            if topic["source_type"] in ("work", "evergreen") and not topic.get("seo_description"):
                await _enrich_work_seo(session, topic)
            for platform in avail:
                content, err = await _generate_one(topic, platform, cfg, site_base)
                base_item = {"source_type": topic["source_type"], "source_id": topic["source_id"],
                             "platform": platform, "title": topic["title"]}
                if content is None:
                    errors += 1
                    items.append({**base_item, "status": "error", "detail": err})
                    await _note_failure(err)
                    continue
                _reset_failures()
                session.add(WebsiteSocialPost(
                    id=uuid.uuid4().hex,
                    source_type=topic["source_type"],
                    source_id=topic["source_id"],
                    platform=platform,
                    content=content,
                    media_url=_abs_url(site_base, topic.get("image_url") or "") or None,
                    status="draft",
                    run_id=run_id,
                ))
                await session.commit()
                day_counts[platform] = day_counts.get(platform, 0) + 1
                week_counts[platform] = week_counts.get(platform, 0) + 1
                generated += 1
                items.append({**base_item, "status": "draft"})

        summary = {"generated": generated, "errors": errors, "items": items}
        patch: dict[str, Any] = {
            "social.runner.last_run_at": time.time(),
            "social.runner.last_run_summary": summary,
        }
        # 水位：有產出才推進；全失敗（claude 掛）不推進 → 同批選題下輪重試。
        # force_topic 是手動測，不動水位。
        if force_topic is None and generated > 0:
            patch["social.last_scan_at"] = time.time()
        await update_settings(session, patch)

        if generated > 0:
            titles = "\n".join(
                f"• {it['title']}（{it['platform']}）"
                for it in items if it.get("status") == "draft"
            )
            try:
                from notifier import notify_tab_async  # type: ignore
                await notify_tab_async("social_daily", count=generated, titles=titles)
            except Exception:
                pass
        return summary
    finally:
        _run_lock.release()


# ══════════════════════════════════════════════════════════
# 「立即執行」= 非同步背景跑（避開 cloudflared/nginx 長請求逾時，同 seo_runner）
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
            await update_settings(s, {"social.runner.running": True})
    except Exception:
        logger.warning("[social_runner] 設 running 旗標失敗")
    try:
        async with factory() as s:
            await run_pipeline(s)
    except Exception:
        logger.exception("[social_runner] _bg_runner 異常")
    try:
        async with factory() as s:
            await update_settings(s, {"social.runner.running": False})
    except Exception:
        pass


def start_run_bg() -> bool:
    """背景啟動一輪 run（立即回）。False = 已有一輪在跑（含 cron 正佔鎖）。"""
    global _run_task
    if is_run_running() or _run_lock.locked():
        return False
    _run_task = asyncio.create_task(_bg_runner())
    return True


async def trigger_run(session: AsyncSession) -> dict:
    """admin「立即執行」觸發點：一律立即回 {started}/{busy}/{error}。
    NAS 端 forward 給 master 背景跑；master/本機直接背景跑。"""
    relay = os.environ.get("MASTER_RELAY_URL", "").strip()
    if relay:
        return await _relay_run_to_master(relay)
    if not start_run_bg():
        return {"status": "busy"}
    return {"status": "started"}


# ══════════════════════════════════════════════════════════
# Cron 排程背景 loop（只在有 claude 的 master 起，同 seo_runner gate）
# ══════════════════════════════════════════════════════════

_scheduler_task: Optional[asyncio.Task] = None


async def _scheduler_loop() -> None:
    """每 60 秒掃 settings 看 cron 是否到期 → run_pipeline。"""
    try:
        from croniter import croniter
    except ImportError:
        logger.warning("[social_runner] croniter 未安裝，排程 loop 不啟動")
        return
    # ⚠ 只在「有 claude 的機器」（= master）跑（gate 理由見 seo_runner._scheduler_loop）
    if _resolve_claude_exe() is None:
        logger.info("[social_runner] 本機無 claude CLI — 社群文稿排程 loop 不啟動（只在 master 跑）")
        return

    from db.session import get_session_factory
    await asyncio.sleep(55)  # 錯開 seo(45)/post_seo(50) 的啟動檢查

    # 開機一次性：清殘留 running 旗標（上一輪被 OTA/crash 中途殺掉時 DB 會卡 True）
    try:
        _f = get_session_factory()
        if _f:
            async with _f() as _s:
                await update_settings(_s, {"social.runner.running": False})
    except Exception:
        pass

    while True:
        try:
            factory = get_session_factory()
            if factory is None:
                await asyncio.sleep(60)
                continue
            async with factory() as session:
                cfg = await get_social_settings(session)
                if cfg["enabled"] and cfg["cron"]:
                    last_at = float(cfg["last_run_at"] or 0)
                    if last_at <= 0:
                        # 首次啟用：把 last_run_at 播種成現在、等下一個 cron 整點才跑。
                        # 不學 post_seo_runner 的「首次立即跑」— social.enabled 預設
                        # true，部署/重啟當下就燒 claude 額度不可接受；也不能每輪都
                        # 拿 now 當基準（next_due 永遠在未來 → cron 永不觸發）。
                        await update_settings(session, {"social.runner.last_run_at": time.time()})
                        await asyncio.sleep(60)
                        continue
                    # naive 本地時區 datetime（"0 9 * * *" = 主機本地 09:00）
                    base_dt = datetime.fromtimestamp(last_at)
                    try:
                        next_due = croniter(cfg["cron"], base_dt).get_next(datetime)
                    except (ValueError, KeyError, TypeError) as e:
                        logger.warning("[social_runner] cron 格式錯誤 %r: %s", cfg["cron"], e)
                        await asyncio.sleep(60)
                        continue
                    if datetime.now() >= next_due:
                        logger.info("[social_runner] cron 觸發（next_due=%s）— run_pipeline",
                                    next_due.isoformat())
                        await run_pipeline(session)
        except Exception:
            logger.exception("[social_runner] scheduler loop 異常")
        await asyncio.sleep(60)


def start_scheduler_task() -> None:
    """main.py startup 呼叫一次 — 啟動背景 cron loop。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("[social_runner] 社群文稿排程 loop 已啟動")
