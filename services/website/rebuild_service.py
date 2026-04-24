"""services/website/rebuild_service.py
---
觸發 Astro rebuild + Notion sync 狀態查詢。

架構：rebuild 是 long-running（npm ci + build，數十秒），因此用 asyncio.create_task
在背景執行，endpoint 立即回傳「已排入」；狀態存在 module-level _status dict。

**單一 worker 限制**：`_REBUILD_STATUS` 是 process-local。若 uvicorn 以 `--workers N`
啟動，每個 worker 有獨立狀態——rebuild 觸發於 A、查詢可能落在 B 會看到 idle。
NAS website-api container 應以 1 worker 跑（小網站足夠），見 docker-compose.yml。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
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


def _website_dir() -> Path:
    """定位 website/ 目錄（repo root 下）。"""
    # __file__ = .../services/website/rebuild_service.py
    return Path(__file__).resolve().parent.parent.parent / "website"


def _default_status() -> dict:
    return dict(_REBUILD_STATUS)


def get_rebuild_status() -> dict:
    return _default_status()


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


async def _run_build(website_dir: Path) -> None:
    """背景執行 npm run build，結果寫 _REBUILD_STATUS。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "npm", "run", "build",
            cwd=str(website_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**os.environ},
        )
        stdout_bytes, _ = await proc.communicate()
        output = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        tail = "\n".join(output.splitlines()[-30:])
        _REBUILD_STATUS.update({
            "state": "success" if proc.returncode == 0 else "error",
            "finished_at": time.time(),
            "output_tail": tail,
            "error": None if proc.returncode == 0 else f"exit={proc.returncode}",
        })
        logger.info("[rebuild] finished exit=%d", proc.returncode)
    except Exception as e:
        _REBUILD_STATUS.update({
            "state": "error",
            "finished_at": time.time(),
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
