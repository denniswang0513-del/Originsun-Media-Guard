"""services/website/rebuild_service.py
---
觸發 Astro rebuild + 推送 dist/ 到 NAS + Notion sync 狀態查詢。

架構：rebuild 是 long-running（npm ci + build + scp 推 NAS，30-60 秒），因此用
asyncio.create_task 在背景執行，endpoint 立即回傳「已排入」；狀態存在 module-level
_status dict + JSON 檔（last_success_at + pending_count 持久化跨重啟）。

**部署模型（精簡版 Phase M）**：
  master Windows: npm run build → website/dist/
                ↘ scp -r dist/ → NAS /share/.../Originsun_Web/Website/dist/
  NAS nginx serves dist/ over cloudflared tunnel
  → master 關機不影響對外網站，最多停在「上次 build 的版本」

**單一 worker 限制**：`_REBUILD_STATUS` 是 process-local。若 uvicorn 以 `--workers N`
啟動，每個 worker 有獨立狀態——rebuild 觸發於 A、查詢可能落在 B 會看到 idle。
master 端 main.py 預設 1 worker，無此問題。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Literal, Optional, TypedDict

from core.schemas_website import (
    NotionCategorySummary, NotionPostSummary, NotionSyncResult,
    NotionSyncSkipped, SyncType,
)

from . import notion_service

logger = logging.getLogger(__name__)

RebuildState = Literal["idle", "running", "success", "error"]


class RebuildStatus(TypedDict):
    state: RebuildState
    started_at: float | None
    finished_at: float | None
    output_tail: str
    error: str | None


_REBUILD_STATUS: RebuildStatus = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "output_tail": "",
    "error": None,
}

# ── 持久化（DB-backed singleton row）──
# 為什麼不用檔案：master + NAS website-api 都會呼叫 mark_dirty / 讀 status，
# 用檔案會雙寫不同步。改用 DB 一張單列表 (website_rebuild_state) 確保所有
# instance 看到同一個值。schema 由 db/migrations_website.py 建立。
_PERSIST_FIELDS = {"last_success_at": None, "pending_count": 0}

# ── Auto-rebuild debounce ──
# 任何公開內容變動 → mark_dirty() → 排定 60 秒後自動 rebuild。
# 期間若再有變動，timer reset（最後一個編輯結束後 60 秒才發布）。
# In-memory only — 若 master 重啟，pending_count 仍在但 timer 沒了；
# 使用者按「立即重建」即可消化。
_DEBOUNCE_DELAY_SEC = 60
_debounce_task: Optional[asyncio.Task] = None
_debounce_fires_at: Optional[float] = None  # epoch sec, for UI countdown


async def _load_meta() -> dict:
    """讀 website_rebuild_state singleton row。DB 不可用時回 default 值。"""
    try:
        from db.session import get_session_factory
        from sqlalchemy import text
        factory = get_session_factory()
        if not factory:
            return dict(_PERSIST_FIELDS)
        async with factory() as session:
            row = (await session.execute(
                text("SELECT last_success_at, pending_count FROM website_rebuild_state WHERE id = 1")
            )).first()
            if not row:
                return dict(_PERSIST_FIELDS)
            return {"last_success_at": row[0], "pending_count": row[1] or 0}
    except Exception as e:
        logger.warning("[rebuild] _load_meta DB read failed: %s", e)
        return dict(_PERSIST_FIELDS)


async def _save_meta(*, last_success_at: Optional[float] = None,
                     pending_count: Optional[int] = None) -> None:
    """更新 singleton row。只動傳入的欄位，其他保持現值。"""
    try:
        from db.session import get_session_factory
        from sqlalchemy import text
        factory = get_session_factory()
        if not factory:
            return
        sets, params = [], {}
        if last_success_at is not None:
            sets.append("last_success_at = :ls")
            params["ls"] = last_success_at
        if pending_count is not None:
            sets.append("pending_count = :pc")
            params["pc"] = pending_count
        if not sets:
            return
        async with factory() as session:
            await session.execute(
                text(f"UPDATE website_rebuild_state SET {', '.join(sets)} WHERE id = 1"),
                params,
            )
            await session.commit()
    except Exception as e:
        logger.warning("[rebuild] _save_meta DB write failed: %s", e)


async def mark_dirty() -> int:
    """公開內容變動時呼叫一次 — counter +1，且 reset 60 秒 debounce。

    回傳新 pending_count。DB UPDATE atomic 不需 asyncio.Lock。
    """
    global _debounce_task, _debounce_fires_at
    try:
        from db.session import get_session_factory
        from sqlalchemy import text
        factory = get_session_factory()
        if not factory:
            return 0
        async with factory() as session:
            row = (await session.execute(
                text("UPDATE website_rebuild_state SET pending_count = pending_count + 1 "
                     "WHERE id = 1 RETURNING pending_count")
            )).first()
            await session.commit()
            new_count = int(row[0]) if row else 0
    except Exception as e:
        logger.warning("[rebuild] mark_dirty failed: %s", e)
        new_count = 0

    # Reset debounce timer — 取消舊的、排新的
    if _debounce_task and not _debounce_task.done():
        _debounce_task.cancel()
    _debounce_fires_at = time.time() + _DEBOUNCE_DELAY_SEC
    _debounce_task = asyncio.create_task(_auto_rebuild_after_delay())
    return new_count


async def _auto_rebuild_after_delay() -> None:
    """sleep N 秒後若還有 pending → 觸發 rebuild。被 cancel 就什麼都不做。"""
    global _debounce_fires_at
    try:
        await asyncio.sleep(_DEBOUNCE_DELAY_SEC)
        meta = await _load_meta()
        if int(meta.get("pending_count", 0)) > 0:
            logger.info("[rebuild] debounce fired → trigger_rebuild")
            await trigger_rebuild()
    except asyncio.CancelledError:
        pass  # 被 reset 的正常路徑
    finally:
        _debounce_fires_at = None


# ── NAS 部署目標（hardcode 即可，未來要動的機率低；要改放 settings.json 也行）──
_NAS_HOST = "admin@192.168.1.132"
_NAS_DIST_DIR = "/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/dist"
# uploads/ 是 dist/ 的 sibling（docker-compose 掛 ../uploads:/app/uploads），
# NAS website-api serve /uploads → 對外 nginx `location /uploads/` 反代它。
_NAS_UPLOADS_DIR = "/share/CACHEDEV1_DATA/Container/AI_Workspace/Originsun_Web/Website/uploads"
_SSH_KEY_PATH = str(Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".ssh" / "id_originsun_nas")


def _website_dir() -> Path:
    """定位 website/ 目錄（repo root 下）。"""
    # __file__ = .../services/website/rebuild_service.py
    return Path(__file__).resolve().parent.parent.parent / "website"


def _default_status() -> dict:
    return dict(_REBUILD_STATUS)


async def get_rebuild_status() -> dict:
    """回傳當前 build/deploy 狀態 + 持久化「上次成功時間」「待發布變動數」+ debounce 倒數。

    DB-backed meta：master + NAS website-api 看同一個 row。debounce timer 是
    in-memory 各自一份，但 UI 端只看 pending_count + last_success_at 不受影響。
    """
    s = dict(_REBUILD_STATUS)
    s.update(await _load_meta())
    s["auto_rebuild_fires_at"] = _debounce_fires_at
    return s


async def trigger_rebuild() -> dict:
    """排入一個背景 build task。

    NAS website-api 容器跑不了 npm（image 無 node）— 透過 env `MASTER_RELAY_URL`
    把 trigger 轉給 master:8000，由 master 執行 npm build + scp NAS。NAS 端
    僅做 HTTP forward，不更新自己的 _REBUILD_STATUS（會造成假狀態）。
    若 master 離線 → 回 502 友善訊息，pending_count 維持，等 master 上線重試。
    """
    relay = os.environ.get("MASTER_RELAY_URL", "").strip()
    if relay:
        return await _relay_to_master(relay)

    if _REBUILD_STATUS["state"] == "running":
        return {"queued": False, "reason": "another build in progress",
                **_default_status()}

    website = _website_dir()
    if not website.exists():
        return {"queued": False, "reason": f"website/ not found at {website}",
                **_default_status()}

    _REBUILD_STATUS.update({
        "state": "running",
        "started_at": time.time(),
        "finished_at": None,
        "output_tail": "",
        "error": None,
    })

    # add_done_callback 攔 task 任何 unhandled exception → 寫進 _REBUILD_STATUS
    # 並落 stderr，避免「state 卡 running、無 traceback」黑洞。
    task = asyncio.create_task(_run_build(website))

    def _on_done(t: asyncio.Task) -> None:
        if t.cancelled():
            logger.warning("[rebuild] task cancelled")
            _REBUILD_STATUS.update({
                "state": "error", "finished_at": time.time(),
                "error": "task cancelled",
            })
            return
        exc = t.exception()
        if exc is not None:
            logger.exception("[rebuild] task crashed", exc_info=exc)
            _REBUILD_STATUS.update({
                "state": "error", "finished_at": time.time(),
                "error": f"{type(exc).__name__}: {exc}",
            })

    task.add_done_callback(_on_done)
    logger.info("[rebuild] background task scheduled (cwd=%s)", website)
    return {"queued": True, **_default_status()}


async def _relay_to_master(relay_url: str) -> dict:
    """NAS website-api 把 rebuild trigger 轉給 master:8000。

    用 X-Internal-Key (= JWT secret) 而非使用者 token — 內部 service-to-service。
    """
    import httpx
    from core.auth import _get_secret
    url = f"{relay_url.rstrip('/')}/api/website/admin/internal/rebuild"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(url, headers={"X-Internal-Key": _get_secret()})
            if r.status_code == 200:
                return r.json()
            return {"queued": False, "reason": f"master relay failed (HTTP {r.status_code})"}
    except (httpx.TimeoutException, httpx.ConnectError):
        return {"queued": False,
                "reason": "master 離線或無回應 — pending 變動會等 master 上線後再發布"}
    except Exception as e:
        return {"queued": False, "reason": f"relay 錯誤: {e}"}


def _resolve_exe(name: str) -> str:
    """解析可執行檔絕對路徑（Windows 上 npm 實際是 npm.cmd，create_subprocess_exec
    不會自動加副檔名 → 用 shutil.which 撈完整路徑）。"""
    return shutil.which(name) or name


_SUBPROCESS_TIMEOUTS = {
    "build": 600,    # npm build：理論 ~5s，給 10 分鐘 buffer 涵蓋 cold cache
    "scp": 120,      # scp dist/ 到 NAS：通常 < 30s
    "ssh-clean": 30, # ssh rm -rf：應該 < 5s
}


async def _run_subprocess(
    label: str, *args: str,
    cwd: Optional[str] = None,
    extra_env: Optional[dict] = None,
) -> tuple[int, str]:
    """執行子程序，回傳 (returncode, stdout_tail)。stderr 合併到 stdout。

    第一個 arg 會走 shutil.which 解析，避免 Windows 上 .cmd / .bat / .exe
    extension 問題（WinError 2）。

    Timeout 防呆：每個 label 有預設秒數，超時 SIGKILL 子 process 並回 rc=-1。
    避免 build/scp 永久 hang 卡住 _REBUILD_STATUS["state"]="running"。
    """
    resolved = (_resolve_exe(args[0]),) + args[1:]
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    timeout = _SUBPROCESS_TIMEOUTS.get(label, 300)
    # Selector-loop-safe: subprocess.run in a thread (asyncio subprocess is
    # unavailable on Windows SelectorEventLoop). stderr merged into stdout.
    from core.subproc import run_capture
    rc, out, err = await run_capture(
        list(resolved), cwd=cwd, env=env, timeout=timeout, merge_stderr=True,
    )
    output = (out or b"").decode("utf-8", errors="replace")
    if rc == -1:
        reason = (err or b"").decode("utf-8", errors="replace") or "unknown error"
        logger.error("[rebuild:%s] %s", label, reason)
        return -1, reason
    tail = "\n".join(output.splitlines()[-30:])
    logger.info("[rebuild:%s] exit=%d", label, rc)
    return rc, tail


async def _read_setting(key: str) -> str:
    """讀 website_settings 一個 key 的字串值。沒設或 DB 不通 → 空字串。"""
    try:
        from db.session import get_session_factory
        from sqlalchemy import text
        factory = get_session_factory()
        if not factory:
            return ""
        async with factory() as session:
            row = (await session.execute(
                text("SELECT value FROM website_settings WHERE key = :k"),
                {"k": key},
            )).first()
            if not row or row[0] is None:
                return ""
            v = row[0]
            # value 是 JSONB — 字串 key 用 strip quotes，也可能是 dict / number
            if isinstance(v, str):
                return v
            return str(v) if v else ""
    except Exception as e:
        logger.warning("[rebuild] _read_setting(%s) failed: %s", key, e)
        return ""


async def _push_dist_to_nas(dist_dir: Path) -> tuple[int, str]:
    """scp -r dist/ 到 NAS。回傳 (returncode, log_tail)。

    用 scp -r 而非 rsync 因為 master Windows 內建 OpenSSH 沒 rsync。
    scp 沒 --delete → stale 檔殘留問題：/works/{slug}/ 這類 path-based
    頁面若 slug 改變或作品退掉 public，舊 dir 會留在 NAS。
    解法：scp 之前先 ssh 清掉 path-based 子目錄（works / news），
    其他 (_astro/、index.html 等) 由 scp 直接覆蓋即可。
    """
    if not dist_dir.exists():
        return 1, f"dist/ not found at {dist_dir}"

    # Step 1: clean stale path-based dirs on NAS（不能整個 dist 砍 — 會有
    # 短暫 502；只清 path-based 那些）
    cleanup_rc, cleanup_tail = await _run_subprocess(
        "ssh-clean",
        "ssh", "-i", _SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
        _NAS_HOST,
        f"rm -rf {_NAS_DIST_DIR}/works {_NAS_DIST_DIR}/news 2>/dev/null; true",
    )
    # Step 2: scp 整個 dist（_astro hash 檔不刪、works/news 重建）
    push_rc, push_tail = await _run_subprocess(
        "scp",
        "scp", "-i", _SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
        "-r", str(dist_dir) + "/.", f"{_NAS_HOST}:{_NAS_DIST_DIR}/",
    )
    return push_rc, f"=== ssh-clean ===\n{cleanup_tail}\n=== scp ===\n{push_tail}"


async def _push_uploads_to_nas(uploads_dir: Path) -> tuple[int, str]:
    """scp uploads/projects/ 到 NAS uploads/。回傳 (returncode, log_tail)。

    作品 showcase/cover/featured 圖由 master api_crm 寫在 master 本地 uploads/projects/，
    但對外 nginx `location /uploads/` 反代的是 NAS website-api 容器 volume（NAS uploads/）。
    不同步的話，公開頁引用的 /uploads/projects/... 圖對外 404（首頁輪播精選圖、作品
    showcase 都吃這個）。brand/team/posts 走 NAS website-api 上傳，本來就在 NAS，不在此列——
    只推 projects/，避免用 master（可能較舊/不全）覆蓋 NAS 管理的那些目錄。
    projects/ 還不存在（尚無作品圖）→ 視為成功 no-op。
    """
    projects_dir = uploads_dir / "projects"
    if not projects_dir.exists():
        return 0, "(no uploads/projects to sync)"
    mk_rc, mk_tail = await _run_subprocess(
        "ssh-mkdir",
        "ssh", "-i", _SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
        _NAS_HOST, f"mkdir -p {_NAS_UPLOADS_DIR}",
    )
    # scp -r projects/ → NAS uploads/（合併，不刪 NAS 既有；uuid 檔名不會撞）
    push_rc, push_tail = await _run_subprocess(
        "scp-uploads",
        "scp", "-i", _SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
        "-r", str(projects_dir), f"{_NAS_HOST}:{_NAS_UPLOADS_DIR}/",
    )
    return push_rc, f"=== ssh-mkdir ===\n{mk_tail}\n=== scp-uploads ===\n{push_tail}"


async def _run_build(website_dir: Path) -> None:
    """背景執行 npm run build → scp dist/ 到 NAS。結果寫 _REBUILD_STATUS。

    流程：
      1. npm run build（master 在地）
      2. scp -r dist/ → NAS（讓對外網站立即看到新版）
      3. 成功才重置 pending_count
    任一步失敗 → state=error。stdout tail 拼接兩階段方便 debug。

    每階段 logger.info 落 uvicorn_out.log，異常用 logger.exception 拿 traceback。
    """
    logger.info("[rebuild] _run_build entered (cwd=%s)", website_dir)

    async def _fail(error: str, output_tail: str | None = None,
                    notify_detail: str | None = None) -> None:
        """狀態轉 error + 主動推播一次做完 — 新增失敗分支不會漏掉告警。

        只在 master 實際執行 build 的路徑會走到（NAS 容器只 relay 不 build）；
        notifier 不可用或推播失敗都靜默，不能影響狀態機。
        """
        st = {"state": "error", "finished_at": time.time(), "error": error}
        if output_tail is not None:
            st["output_tail"] = output_tail
        _REBUILD_STATUS.update(st)
        try:
            from notifier import notify_tab_async  # type: ignore  # 容器可能沒帶 notifier
            await notify_tab_async("rebuild_failed", error=(notify_detail or error)[:300])
        except Exception:
            pass

    try:
        # Astro build env：
        #   - WEBSITE_API_BASE：build 時 fetch /api/website/*（用 master 自己的）
        #   - PUBLIC_TURNSTILE_SITE_KEY：Astro 把它注進客戶端 JS（聯絡表單 widget）
        build_env = {"WEBSITE_API_BASE": "http://localhost:8000"}
        ts_key = await _read_setting("turnstile.site_key")
        if ts_key:
            build_env["PUBLIC_TURNSTILE_SITE_KEY"] = ts_key

        logger.info("[rebuild] launching `npm run build` in %s", website_dir)
        rc, build_tail = await _run_subprocess(
            "build", "npm", "run", "build",
            cwd=str(website_dir),
            extra_env=build_env,
        )
        logger.info("[rebuild] npm exit=%d, tail=%s", rc, build_tail[-300:] if build_tail else "(empty)")
        if rc != 0:
            await _fail(f"build exit={rc}", build_tail,
                        f"npm build exit={rc}\n{(build_tail or '')[-200:]}")
            return

        # Push 階段
        logger.info("[rebuild] launching scp to NAS")
        push_rc, push_tail = await _push_dist_to_nas(website_dir / "dist")
        logger.info("[rebuild] scp exit=%d, tail=%s", push_rc, push_tail[-300:] if push_tail else "(empty)")
        merged_tail = f"=== build OK ===\n{build_tail}\n=== scp ===\n{push_tail}"
        if push_rc != 0:
            await _fail(f"build OK but NAS push failed (scp exit={push_rc})", merged_tail,
                        f"build OK 但推送 NAS 失敗（scp exit={push_rc}）— 對外網站停在舊版")
            return

        # uploads/projects/ 同步（best-effort）：作品圖在 master 本地，需推到 NAS 才對外可見。
        # 失敗不擋 rebuild（dist 已上、YouTube 縮圖仍是 fallback），但落 warning 方便查。
        try:
            up_rc, up_tail = await _push_uploads_to_nas(website_dir.parent / "uploads")
            logger.info("[rebuild] uploads sync exit=%d, tail=%s", up_rc, up_tail[-200:] if up_tail else "(empty)")
            merged_tail += f"\n=== uploads sync (rc={up_rc}) ===\n{up_tail}"
            if up_rc != 0:
                logger.warning("[rebuild] uploads/projects sync failed (rc=%d) — 作品圖可能對外 404", up_rc)
        except Exception as _e_up:
            logger.warning("[rebuild] uploads sync raised: %s", _e_up)
            merged_tail += f"\n=== uploads sync EXCEPTION ===\n{_e_up}"

        # 全程成功 → 持久化（DB 一行 UPDATE atomic）+ 重置 pending counter
        await _save_meta(last_success_at=time.time(), pending_count=0)
        _REBUILD_STATUS.update({
            "state": "success", "finished_at": time.time(),
            "output_tail": merged_tail, "error": None,
        })
        logger.info("[rebuild] success, pending_count reset")

        # N-now 上架驗收：新公開作品的對外頁實測 200 才算閉環（best-effort，不擋 rebuild）
        try:
            await _verify_published_works()
        except Exception as _e_vf:
            logger.warning("[rebuild] publish verification skipped: %s", _e_vf)
    except Exception as e:
        logger.exception("[rebuild] _run_build raised")
        await _fail(f"{type(e).__name__}: {e}")


_PUBLIC_SITE_BASE = "https://www.originsun-studio.com"


async def _verify_published_works() -> None:
    """N-now 上架驗收（藍圖 N-now）：public=True 且尚未驗證的專案，逐一 GET 對外
    作品頁；200 → 蓋 website_verified_at 章 + 推播。結案看板據此顯示「已上線 ✓」
    vs「驗證中」。單次上限 20 件防暴走；失敗的下次 rebuild 再試。"""
    import asyncio
    import urllib.request
    from datetime import datetime

    from sqlalchemy import select
    from db.models import CrmProject
    from db.session import get_session_factory

    factory = get_session_factory()
    if not factory:
        return

    def _http_ok(url: str) -> bool:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OMG-publish-verify/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status == 200
        except Exception:
            return False

    async with factory() as session:
        rows = (await session.execute(
            select(CrmProject)
            .where(CrmProject.public.is_(True), CrmProject.website_verified_at.is_(None))
            .limit(20)
        )).scalars().all()
        if not rows:
            return
        from services.website.project_service import _slug_or_fallback
        # 20 件各 15s timeout 的 HTTP GET 彼此獨立 → 平行（原本序列最壞 20×15s≈5 分）
        urls = [f"{_PUBLIC_SITE_BASE}/works/{_slug_or_fallback(p)}" for p in rows]
        oks = await asyncio.gather(*[asyncio.to_thread(_http_ok, u) for u in urls])
        verified = []
        for p, url, ok in zip(rows, urls, oks):
            if ok:
                p.website_verified_at = datetime.now()
                verified.append(p.public_title or p.name or "")
            else:
                logger.warning("[rebuild] 上架驗證未過（%s）— 下次 rebuild 重試", url)
        if verified:
            await session.commit()
            logger.info("[rebuild] 上架驗證 ✓ %d 件: %s", len(verified), "、".join(verified[:5]))
            try:
                from notifier import notify_tab_async
                await notify_tab_async("works_published",
                                       count=len(verified), titles="、".join(verified[:5]))
            except Exception:
                pass


async def get_notion_status(settings: dict) -> dict:
    """檢查 Notion 連線狀態（是否設定 token + Database ID）。"""
    token = settings.get("notion.token", "")
    db_id = settings.get("notion.database_id", "")
    return {
        "connected": bool(token and db_id),
        "has_token": bool(token),
        "has_database_id": bool(db_id),
    }


# ══════════════════════════════════════════════════════════
# Notion sync（Phase M-E-8）
# ══════════════════════════════════════════════════════════

_NOTION_SETTINGS_MISSING_MSG = (
    "Notion token 或 database_id 未設定（請到「⚙️ 網站設定」填入 "
    "notion.token 與 notion.database_id）"
)


def _posts_json_path() -> Path:
    return _website_dir() / "src" / "content" / "posts.json"


def _categories_json_path() -> Path:
    return _website_dir() / "src" / "content" / "categories.json"


def _media_root() -> Path:
    return _website_dir() / "public" / "notion-media"


def _to_summary(
    result: dict,
    sync_type: SyncType,
    *,
    posts_path: Optional[str] = None,
    cats_path: Optional[str] = None,
    rebuild_queued: bool = False,
) -> NotionSyncResult:
    """Unified factory: notion_service result dict → NotionSyncResult pydantic."""
    return NotionSyncResult(
        ok=result["ok"],
        sync_type=sync_type,
        posts_count=len(result["posts"]),
        categories_count=len(result["categories"]),
        posts=[NotionPostSummary.model_validate(p) for p in result["posts"]],
        categories=[NotionCategorySummary.model_validate(c) for c in result["categories"]],
        skipped=[NotionSyncSkipped.model_validate(s) for s in result["skipped"]],
        warnings=result["warnings"],
        duration_ms=result["duration_ms"],
        error=result["error"],
        posts_json_path=posts_path,
        categories_json_path=cats_path,
        rebuild_queued=rebuild_queued,
    )


def _missing_settings(sync_type: SyncType) -> NotionSyncResult:
    return NotionSyncResult(
        ok=False, sync_type=sync_type, error=_NOTION_SETTINGS_MISSING_MSG,
    )


async def preview_notion_sync(settings: dict) -> NotionSyncResult:
    """Dry-run：撈 Notion 但不寫檔、不下載圖片、不觸發 rebuild。"""
    token = settings.get("notion.token", "")
    db_id = settings.get("notion.database_id", "")
    if not (token and db_id):
        return _missing_settings("preview")

    result = await notion_service.run_full_sync(
        token=token, db_id=db_id, media_root=_media_root(), dry_run=True,
    )
    return _to_summary(result, "preview")


def _dump_json(path: Path, data) -> None:
    """Blocking JSON writer — caller uses asyncio.to_thread."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


async def trigger_notion_sync(settings: dict, do_rebuild: bool = True) -> NotionSyncResult:
    """撈 Notion → 寫 posts.json + categories.json → 選擇性觸發 Astro rebuild。"""
    token = settings.get("notion.token", "")
    db_id = settings.get("notion.database_id", "")
    if not (token and db_id):
        return _missing_settings("sync")

    result = await notion_service.run_full_sync(
        token=token, db_id=db_id, media_root=_media_root(), dry_run=False,
    )
    if not result["ok"]:
        return _to_summary(result, "sync")

    posts_path = _posts_json_path()
    cats_path = _categories_json_path()
    try:
        posts_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.gather(
            asyncio.to_thread(_dump_json, posts_path, result["posts"]),
            asyncio.to_thread(_dump_json, cats_path, result["categories"]),
        )
        logger.info(
            "[notion-sync] wrote %d posts + %d categories to %s",
            len(result["posts"]), len(result["categories"]), posts_path.parent,
        )
    except Exception as e:
        logger.exception("[notion-sync] failed to write JSON")
        return _to_summary(
            {**result, "ok": False, "error": f"JSON 寫檔失敗：{e}"}, "sync",
        )

    rebuild_queued = False
    if do_rebuild:
        rb = await trigger_rebuild()
        rebuild_queued = bool(rb.get("queued"))
        if not rebuild_queued:
            result["warnings"].append(
                f"Astro rebuild 未排入（{rb.get('reason', 'unknown')}），"
                f"JSON 已更新但網站需手動 rebuild"
            )

    return _to_summary(
        result, "sync",
        posts_path=str(posts_path),
        cats_path=str(cats_path),
        rebuild_queued=rebuild_queued,
    )
