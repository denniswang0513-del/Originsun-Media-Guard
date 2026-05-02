"""services/website/seo_runner.py
---
AI SEO 自動生成 runner：audit → 對每件 needs_ai_review 作品 GET draft →
透過 `claude --print` headless 模式呼叫 Claude（吃使用者 Max 訂閱額度，無需 API key）
→ 解析 JSON → PATCH 回 website_project_seo 表。

可從以下三處觸發：
1. core/scheduler.py 排程（cron 每日凌晨 3 點預設）
2. POST /api/website/admin/seo/run（admin 立即執行全部）
3. POST /api/website/admin/seo/projects/{id}/run（單筆，showcase-edit「立即跑」按鈕）

設定（存 website_settings k-v 表）：
    seo.ai_runner.enabled        bool   啟用排程（手動觸發不受此影響）
    seo.ai_runner.cron           str    croniter 字串，預設 "0 3 * * *"
    seo.ai_runner.batch_size     int    單次最多處理 N 筆，預設 10
    seo.ai_runner.last_run_at    epoch  最後執行時間
    seo.ai_runner.last_run_summary json {processed, skipped, errors, works}

連續 3 次失敗 → 寫 ai_review_notes = "AI 連續失敗 3 次 (yyyy-mm-dd)" 提示 admin 介入。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from . import seo_service
from .settings_service import get_all_settings, update_settings

logger = logging.getLogger(__name__)

# 同 process 鎖避免多處同時觸發
_run_lock = asyncio.Lock()

# 失敗計數（in-memory，process 重啟歸零；3 次累計就標 notes）
_failure_counts: dict[str, int] = {}
_FAILURE_THRESHOLD = 3

# subprocess timeout — 給 LLM 充裕回應時間，30 個作品 batch 不應整個 batch timeout
# 實測單筆 ~25s，留 6x buffer 給網路抖動 / Claude 排隊
_CLAUDE_TIMEOUT_SEC = 180

_PROMPT_TEMPLATE = """你是繁體中文網站的 SEO 內容生成助手，專門替「源日影像」（影像製作公司）作品集頁面生內容。

任務：根據下方作品 context，回傳一份 JSON SEO 內容。**只回 JSON，不要 markdown code fence 或解釋文字。**

JSON 必須含這 6 個欄位（鍵名固定）：
{{
  "seo_title": "string，60-100 字，含作品名 + 客戶 + 影像製作關鍵字",
  "seo_description": "string，80-160 字，繁中，描述影片內容 + 製作脈絡，給搜尋引擎摘要顯示",
  "keywords": ["string", ...]   // 5-10 個關鍵字，繁中為主，含客戶名/作品類型/相關主題
  "narrative_long": "string，250-450 字，給 LLM/AI 爬蟲讀的詳細介紹，描述作品脈絡、製作團隊、客戶訴求",
  "key_facts": [{{"label":"客戶","value":"X"}}, ...]   // 5-7 個事實，常見 label：客戶 / 類型 / 拍攝年份 / 導演 / 主演 / 影片用途
  "faqs": [{{"q":"...","a":"..."}}, ...]   // 3 個 Q&A，問外界可能搜尋的問題、答以中肯訊息
}}

寫作原則：
- 全部繁體中文（除人名/英文片名）
- 不要過度行銷語氣（不寫「最棒的」「卓越的」）
- key_facts 必須從 context 撈得到的事實，不能編造
- faqs 答案要是 1-2 句話、訊息密度高
- 不要寫「源日影像專業團隊精心製作...」這類空話

作品 context：
{context}
"""


def _build_prompt(draft: dict) -> str:
    """從 draft endpoint 回傳組 LLM prompt context。"""
    parts = [
        f"作品名稱：{draft.get('title') or '(無)'}",
        f"客戶：{draft.get('client') or '(未填)'}",
        f"年份：{draft.get('year') or '(未填)'}",
        f"YouTube ID：{draft.get('youtube_id') or '(無影片)'}",
    ]
    desc = (draft.get("description") or "").strip()
    if desc:
        parts.append(f"\n描述：\n{desc}")
    credits_text = (draft.get("credits_text") or "").strip()
    credits = draft.get("credits") or []
    if credits_text:
        parts.append(f"\nCredits（純文字）：\n{credits_text}")
    elif credits:
        parts.append(f"\nCredits（block JSON）：\n{json.dumps(credits, ensure_ascii=False, indent=2)}")
    return _PROMPT_TEMPLATE.format(context="\n".join(parts))


async def _call_claude(prompt: str) -> tuple[Optional[str], str]:
    """subprocess call `claude --print`，回 (stdout, error_detail)。

    成功 → (stdout, "")；失敗 → (None, 中文診斷訊息)。把錯誤理由帶回給呼叫端，
    讓 admin 從 toast 直接看到「不在 PATH / timeout / exit=N stderr=...」而非通用訊息。
    """
    claude_exe = shutil.which("claude")
    if not claude_exe:
        msg = "claude CLI 不在 PATH（uvicorn 程序看不到 claude.exe — 重啟服務或把 WinGet 路徑加進系統 PATH）"
        logger.error("[seo_runner] %s", msg)
        return None, msg
    try:
        proc = await asyncio.create_subprocess_exec(
            claude_exe, "--print",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except NotImplementedError:
        # Selector loop on Windows 不支援 subprocess — uvicorn 預設用 Proactor，
        # 但若被改了會走到這裡。
        msg = "asyncio 事件迴圈不支援 subprocess（Windows Selector loop？）"
        logger.error("[seo_runner] %s", msg)
        return None, msg
    except OSError as e:
        msg = f"啟動 claude.exe 失敗：{e}"
        logger.error("[seo_runner] %s", msg)
        return None, msg
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")),
            timeout=_CLAUDE_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        msg = f"claude --print 超時（{_CLAUDE_TIMEOUT_SEC}s 未回應）"
        logger.warning("[seo_runner] %s", msg)
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return None, msg
    if proc.returncode != 0:
        err_text = err[:300].decode("utf-8", errors="replace").strip()
        msg = f"claude 結束碼={proc.returncode}；stderr={err_text!r}"
        logger.warning("[seo_runner] %s", msg)
        return None, msg
    text = out.decode("utf-8", errors="replace")
    if not text.strip():
        err_text = err[:300].decode("utf-8", errors="replace").strip()
        msg = f"claude --print 回傳空字串（stderr={err_text!r}）"
        logger.warning("[seo_runner] %s", msg)
        return None, msg
    return text, ""


def _parse_response(text: str) -> Optional[dict]:
    """從 Claude 回應抽 JSON、驗證必要欄位。失敗回 None。

    對 markdown code fence 容錯：```json ... ``` 也接受。
    """
    if not text:
        return None
    s = text.strip()
    # 拿掉 markdown fence
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)
    if fence:
        s = fence.group(1)
    else:
        # 嘗試從第一個 { 到最後一個 } — 容錯前後贅字
        first, last = s.find("{"), s.rfind("}")
        if first != -1 and last != -1 and last > first:
            s = s[first:last + 1]
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        logger.warning("[seo_runner] JSON 解析失敗，前 200 字：%s", text[:200])
        return None
    if not isinstance(data, dict):
        return None
    # 必要欄位檢查
    required = {"seo_title", "seo_description", "keywords", "narrative_long", "key_facts", "faqs"}
    if not required.issubset(data.keys()):
        logger.warning("[seo_runner] JSON 缺欄位：missing=%s",
                       required - set(data.keys()))
        return None
    # 形狀粗略檢查（list 是 list、字串是字串）
    if not isinstance(data["keywords"], list) or not isinstance(data["key_facts"], list) or not isinstance(data["faqs"], list):
        return None
    # key_facts entries 必須含 label/value
    for f in data["key_facts"]:
        if not isinstance(f, dict) or not f.get("label") or not f.get("value"):
            logger.warning("[seo_runner] key_facts entry 結構錯：%s", f)
            return None
    for q in data["faqs"]:
        if not isinstance(q, dict) or not q.get("q") or not q.get("a"):
            logger.warning("[seo_runner] faqs entry 結構錯：%s", q)
            return None
    return {
        "seo_title": str(data["seo_title"])[:120],
        "seo_description": str(data["seo_description"])[:500],
        "keywords": [str(k) for k in data["keywords"] if k][:15],
        "narrative_long": str(data["narrative_long"]),
        "key_facts": [{"label": str(f["label"])[:80], "value": str(f["value"])[:300]}
                      for f in data["key_facts"]],
        "faqs": [{"q": str(q["q"])[:300], "a": str(q["a"])[:2000]}
                 for q in data["faqs"]],
    }


async def _relay_run_to_master(
    relay_url: str,
    *,
    target_project_id: Optional[str],
    batch_size: int,
) -> dict:
    """NAS website-api 容器把 SEO runner 請求轉給 master:8000，
    由 master 的 claude.exe 真的跑。用 X-Internal-Key (= JWT secret)
    內部認證（master/NAS 共用同一個 secret）。

    單筆執行（target_project_id 非空）和整批執行走不同 endpoint。
    pipeline 整批最久要跑 batch_size × 180s + buffer，timeout 給夠。
    """
    import httpx
    from core.auth import _get_secret
    if target_project_id:
        url = f"{relay_url.rstrip('/')}/api/website/admin/internal/seo/projects/{target_project_id}/run"
    else:
        url = f"{relay_url.rstrip('/')}/api/website/admin/internal/seo/run?batch_size={batch_size}"
    # 整批 batch_size=10 × 180s = 1800s 上限，加 buffer 給網路抖動
    timeout = 60.0 if target_project_id else float(batch_size) * 200.0 + 60.0
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers={"X-Internal-Key": _get_secret()})
            if r.status_code == 200:
                return r.json()
            return {
                "processed": 0, "skipped": 0, "errors": 1, "works": [],
                "error": f"master relay 失敗 (HTTP {r.status_code}): {r.text[:200]}",
            }
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        return {
            "processed": 0, "skipped": 0, "errors": 1, "works": [],
            "error": f"master 離線或超時：{e}",
        }
    except Exception as e:
        return {
            "processed": 0, "skipped": 0, "errors": 1, "works": [],
            "error": f"relay 錯誤：{e}",
        }


async def _process_one(session: AsyncSession, project_id: str) -> tuple[str, str]:
    """處理單一作品。回傳 (status, detail)：status ∈ {ok, parse_error, llm_error, no_draft}"""
    draft = await seo_service.get_project_seo_draft_context(session, project_id)
    if not draft:
        return ("no_draft", "作品不存在或未公開")
    prompt = _build_prompt(draft)
    logger.info("[seo_runner] %s: prompt %d chars", project_id, len(prompt))

    raw, call_err = await _call_claude(prompt)
    if not raw:
        return ("llm_error", call_err or "claude --print 無回應或失敗")

    parsed = _parse_response(raw)
    if not parsed:
        return ("parse_error", f"JSON 解析失敗（前 80 字：{raw[:80]!r}）")

    await seo_service.update_project_seo(session, project_id, parsed, by="ai-runner")
    return ("ok", "PATCH 完成")


def _bump_failure(project_id: str) -> int:
    _failure_counts[project_id] = _failure_counts.get(project_id, 0) + 1
    return _failure_counts[project_id]


def _reset_failure(project_id: str) -> None:
    _failure_counts.pop(project_id, None)


async def _mark_persistent_failure(session: AsyncSession, project_id: str) -> None:
    """連續失敗達門檻 → 寫 ai_review_notes 給 admin 看。"""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    note = f"AI 連續 {_FAILURE_THRESHOLD} 次失敗（{today}）— 請 admin 檢查 prompt 或人工填寫"
    await seo_service.update_project_seo(
        session, project_id, {"ai_review_notes": note}, by="ai-runner",
    )


async def run_pipeline(
    session: AsyncSession,
    *,
    target_project_id: Optional[str] = None,
    batch_size: int = 10,
    dry_run: bool = False,
) -> dict:
    """跑 SEO 自動生成 pipeline。

    Args:
        session: DB session
        target_project_id: 指定單一作品（showcase-edit「立即跑此作品」用）；None = 整個 audit
        batch_size: 單次最多處理 N 筆（防止把 Max 額度燒爆）
        dry_run: True 時不實際呼 claude，只回會處理哪些作品

    Returns:
        {processed: N, skipped: N, errors: N, works: [{project_id, status, detail}]}
    """
    # NAS website-api 容器跑不了 `claude` CLI（image 是 python:3.11-slim，
    # 也沒登入 Anthropic）— 透過 env `MASTER_RELAY_URL` 把整個 pipeline forward
    # 給 master:8000，由 master 的 claude.exe 跑、結果回傳給 admin。
    # master 本身沒設這個 env，會走本機路徑。
    relay = os.environ.get("MASTER_RELAY_URL", "").strip()
    if relay and not dry_run:
        return await _relay_run_to_master(
            relay, target_project_id=target_project_id, batch_size=batch_size,
        )

    # 用 timeout=0 的 acquire 確認不會有兩個 caller 同時 pass pre-check 進入 runner
    try:
        await asyncio.wait_for(_run_lock.acquire(), timeout=0.001)
    except asyncio.TimeoutError:
        return {"status": "busy", "processed": 0, "skipped": 0, "errors": 0, "works": [],
                "error": "另一輪 AI runner 正在跑，請稍後再試"}

    try:
        if target_project_id:
            targets = [target_project_id]
        else:
            audit = await seo_service.list_seo_audit(session)
            targets = [
                it["project_id"] for it in audit
                if it.get("needs_ai_review")
            ][:batch_size]

        if dry_run:
            return {
                "dry_run": True,
                "processed": 0, "skipped": 0, "errors": 0,
                "works": [{"project_id": t, "status": "would_process"} for t in targets],
            }

        processed = errors = 0
        works: list[dict] = []
        for pid in targets:
            try:
                status, detail = await _process_one(session, pid)
            except Exception as e:
                logger.exception("[seo_runner] %s: unhandled error", pid)
                status, detail = ("llm_error", f"未捕例外：{e}")

            if status == "ok":
                processed += 1
                _reset_failure(pid)
            else:
                errors += 1
                count = _bump_failure(pid)
                if count >= _FAILURE_THRESHOLD:
                    try:
                        await _mark_persistent_failure(session, pid)
                        _reset_failure(pid)
                        detail += f" → 已標 ai_review_notes（連續 {count} 次失敗）"
                    except Exception as e:
                        logger.warning("[seo_runner] mark_persistent_failure 失敗：%s", e)
            works.append({"project_id": pid, "status": status, "detail": detail})

        # 持久化 last_run（只在非單筆觸發時記錄整批 summary）
        summary = {"processed": processed, "errors": errors, "works": works}
        if target_project_id is None:
            try:
                await update_settings(session, {
                    "seo.ai_runner.last_run_at": time.time(),
                    "seo.ai_runner.last_run_summary": summary,
                })
            except Exception as e:
                logger.warning("[seo_runner] 寫 last_run_* 失敗：%s", e)

        return {"processed": processed, "skipped": 0, "errors": errors, "works": works}
    finally:
        _run_lock.release()


async def get_runner_settings(session: AsyncSession) -> dict:
    """讀 ai_runner 排程設定（給 admin UI 顯示用）。"""
    s = await get_all_settings(session)
    return {
        "enabled": s.get("seo.ai_runner.enabled") is True,
        "cron": str(s.get("seo.ai_runner.cron") or "0 3 * * *"),
        "batch_size": int(s.get("seo.ai_runner.batch_size") or 10),
        "last_run_at": s.get("seo.ai_runner.last_run_at"),
        "last_run_summary": s.get("seo.ai_runner.last_run_summary"),
    }


async def update_runner_settings(session: AsyncSession, payload: dict, *, by: Optional[str] = None) -> dict:
    """admin 更新排程設定（只接受 enabled / cron / batch_size 三鍵）。

    Raises ValueError if cron 字串非法（讓 router 轉成 422）。
    """
    ALLOWED = {"enabled", "cron", "batch_size"}
    to_write = {f"seo.ai_runner.{k}": v for k, v in payload.items() if k in ALLOWED}
    # 驗證 cron — 非法字串排程 loop 會靜默 no-op，admin 拿不到回饋
    if "cron" in payload and payload["cron"]:
        try:
            from croniter import croniter
            croniter(str(payload["cron"]))
        except Exception as e:
            raise ValueError(f"cron 字串不合法：{payload['cron']!r}（{e}）")
    if to_write:
        await update_settings(session, to_write, updated_by=by)
    return await get_runner_settings(session)


# ══════════════════════════════════════════════════════════
# Cron 排程背景 loop
# ══════════════════════════════════════════════════════════
# 每 60 秒檢查一次 settings：enabled 且 cron 到期 → run_pipeline。
# 用 last_run_at + croniter.get_next 算下一個觸發點，避免雙觸發。

_scheduler_task: Optional[asyncio.Task] = None


# cron 字串以「主機本地時區」解讀（master 在台灣 → 0 3 * * * = 凌晨 3 點）
# 用 naive datetime 給 croniter，避免 UTC/Local 換算錯誤
async def _scheduler_loop() -> None:
    """每 60 秒掃 settings 看是否該跑 pipeline。"""
    try:
        from croniter import croniter
    except ImportError:
        logger.warning("[seo_runner] croniter 未安裝，排程 loop 不啟動")
        return

    from db.session import get_session_factory
    await asyncio.sleep(45)  # 等服務 stable + DB ready

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
                    # naive 本地時區 datetime（cron 字串 "0 3 * * *" = 主機本地 03:00）
                    base_dt = datetime.fromtimestamp(last_at) if last_at else datetime.now()
                    try:
                        ci = croniter(settings["cron"], base_dt)
                        next_due = ci.get_next(datetime)
                    except (ValueError, KeyError, TypeError) as e:
                        logger.warning("[seo_runner] cron 格式錯誤 %r: %s", settings["cron"], e)
                        await asyncio.sleep(60)
                        continue
                    now = datetime.now()
                    if now >= next_due:
                        logger.info("[seo_runner] cron 觸發（next_due=%s, now=%s）— run_pipeline",
                                    next_due.isoformat(), now.isoformat())
                        result = await run_pipeline(
                            session, batch_size=settings["batch_size"],
                        )
                        if result.get("processed", 0) > 0:
                            try:
                                from . import rebuild_service
                                await rebuild_service.mark_dirty()
                            except Exception as e:
                                logger.warning("[seo_runner] mark_dirty 失敗：%s", e)
        except Exception:
            logger.exception("[seo_runner] scheduler loop 異常")
        await asyncio.sleep(60)


def start_scheduler_task() -> None:
    """main.py startup 呼叫一次 — 啟動背景 cron loop。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    # 從 async startup hook 呼叫 — asyncio.create_task 直接在 running loop 上排程
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("[seo_runner] 排程 loop 已啟動")
