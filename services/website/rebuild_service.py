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
import logging
import os
import time
from pathlib import Path
from typing import Literal, TypedDict

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


async def trigger_notion_sync() -> dict:
    """觸發 Notion 文章同步（M-E 部落格階段實作 Notion 撈文邏輯）。

    目前版本只觸發一次 rebuild，因為 Notion 在 build time 被 Astro 撈。
    """
    status = await trigger_rebuild()
    return {**status, "sync_type": "notion-via-rebuild"}
