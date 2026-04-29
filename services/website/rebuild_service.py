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

# ── 持久化欄位（跨主控端重啟仍要記得「上次發布時間」+「未發布變動數」）──
# 寫進 services/website/.rebuild_meta.json，main.py startup 不需處理。
_PERSIST_FIELDS = {
    "last_success_at": None,   # float epoch sec, last successful rebuild+deploy
    "pending_count": 0,         # int, public-affecting CRUD since last success
}
_meta_lock = asyncio.Lock()

# ── Auto-rebuild debounce ──
# 任何公開內容變動 → mark_dirty() → 排定 60 秒後自動 rebuild。
# 期間若再有變動，timer reset（最後一個編輯結束後 60 秒才發布）。
# In-memory only — 若 master 重啟，pending_count 仍在但 timer 沒了；
# 使用者按「立即重建」即可消化。
_DEBOUNCE_DELAY_SEC = 60
_debounce_task: Optional[asyncio.Task] = None
_debounce_fires_at: Optional[float] = None  # epoch sec, for UI countdown


def _meta_path() -> Path:
    return Path(__file__).resolve().parent / ".rebuild_meta.json"


def _load_meta() -> dict:
    p = _meta_path()
    if not p.exists():
        return dict(_PERSIST_FIELDS)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return {**_PERSIST_FIELDS, **d}
    except Exception:
        return dict(_PERSIST_FIELDS)


def _save_meta(d: dict) -> None:
    p = _meta_path()
    try:
        # 只寫已知欄位，避免外部塞髒資料
        clean = {k: d.get(k, v) for k, v in _PERSIST_FIELDS.items()}
        p.write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning("[rebuild] meta save failed: %s", e)


async def mark_dirty() -> int:
    """公開內容變動時呼叫一次 — counter +1，且 reset 60 秒 debounce。

    回傳新 pending_count。多處 CRUD 同時呼叫安全（asyncio.Lock）。
    """
    global _debounce_task, _debounce_fires_at
    async with _meta_lock:
        meta = _load_meta()
        meta["pending_count"] = int(meta.get("pending_count", 0)) + 1
        _save_meta(meta)
        new_count = meta["pending_count"]

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
        # 期間可能被手動 rebuild 消化掉了 → 檢查一下
        meta = _load_meta()
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
_SSH_KEY_PATH = str(Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".ssh" / "id_originsun_nas")


def _website_dir() -> Path:
    """定位 website/ 目錄（repo root 下）。"""
    # __file__ = .../services/website/rebuild_service.py
    return Path(__file__).resolve().parent.parent.parent / "website"


def _default_status() -> dict:
    return dict(_REBUILD_STATUS)


def get_rebuild_status() -> dict:
    """回傳當前 build/deploy 狀態 + 持久化「上次成功時間」「待發布變動數」+ debounce 倒數。"""
    s = dict(_REBUILD_STATUS)
    s.update(_load_meta())
    s["auto_rebuild_fires_at"] = _debounce_fires_at
    return s


async def trigger_rebuild() -> dict:
    """排入一個背景 build task。若已在跑就回 'running'。"""
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

    asyncio.create_task(_run_build(website))
    return {"queued": True, **_default_status()}


def _resolve_exe(name: str) -> str:
    """解析可執行檔絕對路徑（Windows 上 npm 實際是 npm.cmd，create_subprocess_exec
    不會自動加副檔名 → 用 shutil.which 撈完整路徑）。"""
    return shutil.which(name) or name


async def _run_subprocess(
    label: str, *args: str,
    cwd: Optional[str] = None,
    extra_env: Optional[dict] = None,
) -> tuple[int, str]:
    """執行子程序，回傳 (returncode, stdout_tail)。stderr 合併到 stdout。

    第一個 arg 會走 shutil.which 解析，避免 Windows 上 .cmd / .bat / .exe
    extension 問題（WinError 2）。
    """
    resolved = (_resolve_exe(args[0]),) + args[1:]
    env = {**os.environ}
    if extra_env:
        env.update(extra_env)
    proc = await asyncio.create_subprocess_exec(
        *resolved,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    out_bytes, _ = await proc.communicate()
    output = out_bytes.decode("utf-8", errors="replace") if out_bytes else ""
    tail = "\n".join(output.splitlines()[-30:])
    logger.info("[rebuild:%s] exit=%d", label, proc.returncode)
    return proc.returncode, tail


async def _push_dist_to_nas(dist_dir: Path) -> tuple[int, str]:
    """scp -r dist/ 到 NAS。回傳 (returncode, log_tail)。

    用 scp -r 而非 rsync 因為 master Windows 內建 OpenSSH 沒 rsync。
    Trade-off：scp 不刪 NAS 端 stale 檔（無 --delete），但 Astro 內容大多是
    content-hashed 檔名（_astro/*.css 等），舊檔留著無害。/works/{slug}/
    這種 path-based 頁面若刪除作品會殘留 — 由前端發布按鈕的「全清」選項處理（未來）。
    """
    if not dist_dir.exists():
        return 1, f"dist/ not found at {dist_dir}"
    return await _run_subprocess(
        "scp",
        "scp", "-i", _SSH_KEY_PATH, "-o", "StrictHostKeyChecking=no",
        "-r", str(dist_dir) + "/.", f"{_NAS_HOST}:{_NAS_DIST_DIR}/",
    )


async def _run_build(website_dir: Path) -> None:
    """背景執行 npm run build → scp dist/ 到 NAS。結果寫 _REBUILD_STATUS。

    流程：
      1. npm run build（master 在地）
      2. scp -r dist/ → NAS（讓對外網站立即看到新版）
      3. 成功才重置 pending_count
    任一步失敗 → state=error。stdout tail 拼接兩階段方便 debug。
    """
    try:
        # Astro build 期間 fetch master 自身的 /api/website/*。預設 8001 是規劃中的
        # NAS website-api container；現階段資料源在 master:8000（[DEV BRIDGE] 設定）。
        rc, build_tail = await _run_subprocess(
            "build", "npm", "run", "build",
            cwd=str(website_dir),
            extra_env={"WEBSITE_API_BASE": "http://localhost:8000"},
        )
        if rc != 0:
            _REBUILD_STATUS.update({
                "state": "error", "finished_at": time.time(),
                "output_tail": build_tail, "error": f"build exit={rc}",
            })
            return

        # Push 階段
        push_rc, push_tail = await _push_dist_to_nas(website_dir / "dist")
        merged_tail = f"=== build OK ===\n{build_tail}\n=== scp ===\n{push_tail}"
        if push_rc != 0:
            _REBUILD_STATUS.update({
                "state": "error", "finished_at": time.time(),
                "output_tail": merged_tail,
                "error": f"build OK but NAS push failed (scp exit={push_rc})",
            })
            return

        # 全程成功 → 持久化 + 重置 pending counter
        async with _meta_lock:
            _save_meta({
                "last_success_at": time.time(),
                "pending_count": 0,
            })
        _REBUILD_STATUS.update({
            "state": "success", "finished_at": time.time(),
            "output_tail": merged_tail, "error": None,
        })
    except Exception as e:
        _REBUILD_STATUS.update({
            "state": "error", "finished_at": time.time(),
            "error": str(e),
        })
        logger.error("[rebuild] failed: %s", e)


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
