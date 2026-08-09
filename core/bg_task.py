"""core/bg_task.py — 有守衛的 fire-and-forget 背景工作。

`asyncio.create_task()` 之後不留參考的話，**event loop 只持弱參考** —— GC 隨時
可以把還沒跑完的 task 收掉，而且完全沒有痕跡。這個模組把兩件事收成一份：

  1. 強參考直到完成（`_pending`）
  2. 例外一定被接住並記下來，不會裸奔回 event loop

工作本身的善後（把那一列標成 failed）**不在這裡**：跑到一半被砍、行程重啟、
例外沒被接住 —— 三種死法都留下同一個現場（DB 停在 `pending`），所以在讀取端
用 `core.bg_status.settle` 一次解決，比要求每個呼叫端傳 on_error 誠實。

原型是 `routers/website/admin_watchdog._fire`；那支跟 watchdog 沒有任何關係，
純機械件，所以搬到 core 讓大家用（呼叫端已遷過來）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable

logger = logging.getLogger(__name__)

_pending: set = set()


def fire(coro: Awaitable, *, label: str = "") -> None:
    """背景跑一個 coroutine，不等它。"""
    async def _guarded():
        try:
            await coro
        except Exception as e:                       # noqa: BLE001 — 這裡就是最後一道
            logger.exception("[bg] %s 失敗：%s", label or "task", e)

    task = asyncio.create_task(_guarded())
    _pending.add(task)
    task.add_done_callback(_pending.discard)
