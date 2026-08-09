"""core/bg_task.py — 有守衛的 fire-and-forget 背景工作。

`asyncio.create_task()` 之後不留參考的話，**event loop 只持弱參考** —— GC 隨時
可以把還沒跑完的 task 收掉，而且完全沒有痕跡。這個模組把 repo 裡各處已經各自
解過的兩件事收成一份：

  1. 強參考直到完成（`_pending`）
  2. 例外一定被接住並交給呼叫端善後（`on_error`）—— 不接的話那筆工作的 DB
     狀態會永遠停在 `pending`，UI 上就是一個轉不完的圈

原型是 `routers/website/admin_watchdog._fire`；那支跟 watchdog 沒有任何關係，
純機械件，所以搬到 core 讓大家用。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

_pending: set = set()


def fire(coro: Awaitable, *, label: str = "",
         on_error: Optional[Callable[[BaseException], Awaitable]] = None) -> None:
    """背景跑一個 coroutine，不等它。例外交給 `on_error`（也 await）。"""
    async def _guarded():
        try:
            await coro
        except Exception as e:                       # noqa: BLE001 — 這裡就是最後一道
            logger.exception("[bg] %s 失敗：%s", label or "task", e)
            if on_error:
                try:
                    await on_error(e)
                except Exception:                    # noqa: BLE001
                    logger.exception("[bg] %s 的 on_error 也失敗", label or "task")

    task = asyncio.create_task(_guarded())
    _pending.add(task)
    task.add_done_callback(_pending.discard)
