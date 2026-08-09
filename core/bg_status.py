"""core/bg_status.py — 背景工作的狀態在讀取端自癒。

背景工作（跑 claude 的那些）先把列標成 `pending` 才起 task。伺服器一重啟
（OTA 發布、master 重開）那個 task 就沒了，而 DB 還停在 `pending` —— UI 上是
一個永遠轉不完的圈，唯一出路是人再按一次。

不加任何基建就能修：**在讀取端推導**。超過 claude 的 timeout 還停在 pending
就是死了。同一招也順手蓋掉「task 被 GC」與「例外沒被接住」兩種死法。

同一個病在 repo 裡還有兩個各自為政的解法，都是寫入端、都只治得了自己那條路：
`seo_runner._batch_runner` 的 terminal `last_run_at`、`reference_archiver` 撿回
殘留 `downloading` 的 SQL 述詞。要再寫第四個之前先看這裡。

🔴 前提：`updated_at` 必須是**工作真正開始**的時間，不是排進佇列的時間 ——
`_call_claude` 有併發閘，排隊時間不算在 180 秒 timeout 裡。所以兩個寫入端都在
`on_start`（拿到閘之後）補蓋一次時間戳；少了那一下，塞車時健康的工作會被誤判。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# claude 的 timeout 是 180 秒；再給 60 秒讓寫回 DB 完成
STALE_AFTER = timedelta(seconds=240)
STALE_MSG = "背景工作中斷了（伺服器可能重啟過）—— 再按一次就好"


def settle(status: str, updated_at, error: str = "") -> tuple:
    """`(status, error)` → 卡太久的 pending 改判 failed。"""
    if status != "pending" or not updated_at:
        return status, error
    at = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - at > STALE_AFTER:
        return "failed", STALE_MSG
    return status, error
