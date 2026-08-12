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
`_call_claude` 有併發閘，排隊時間不算在 180 秒 timeout 裡。寫入端唯一正本
`run_claude_job`（本檔）在 `on_start`（拿到閘之後）補蓋一次時間戳；
少了那一下，塞車時健康的工作會被誤判。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# claude 的 timeout 是 180 秒；再給 60 秒讓寫回 DB 完成。
# 🔴 比這更久的合法工作（whisper 轉錄一小時錄音要幾十分鐘）要包 keepalive()
# —— 心跳間隔由此值推導，改這裡不會讓長工被誤判。
STALE_AFTER = timedelta(seconds=240)
STALE_MSG = "背景工作中斷了（伺服器可能重啟過）—— 再按一次就好"
_BEAT_SEC = STALE_AFTER.total_seconds() / 2


def settle(status: str, updated_at, error: str = "") -> tuple:
    """`(status, error)` → 卡太久的 pending 改判 failed。"""
    if status != "pending" or not updated_at:
        return status, error
    at = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - at > STALE_AFTER:
        return "failed", STALE_MSG
    return status, error


# ── 寫入端（2026-08-11 收斂）───────────────────────────────
# brief_writer / brief_template_digest / quote_analyzer 三個 service 原本各抄
# 一份「partial update + 蓋 updated_at」與「跑 claude → settle 回一列」——
# 第三份出現，照檔頭自己的規則收進來。呼叫端都經 core.bg_task.fire，
# 回傳值沒人接 → 這裡一律回 None。


async def save_job_row(model_cls, row_id: str, *, status=None, error=...,
                       **fields) -> None:
    """背景工作那一列的部分更新。**一個參數都不給＝只蓋 updated_at**
    （開工的時間戳 —— 檔頭前提：塞車時健康的工作不被誤判 stale 全靠它）。
    `error=...` sentinel 區分「不動」與「清成 None」；`fields` 給結果欄
    （content / skeleton …）。列不見了就靜默返回 —— 背景工作沒有人可以報錯。"""
    from core.db_guard import db_factory_or_503
    factory = db_factory_or_503()
    async with factory() as session:
        row = await session.get(model_cls, row_id)
        if not row:
            return
        for k, v in fields.items():
            setattr(row, k, v)
        if status is not None:
            row.status = status
        if error is not ...:
            row.error = error
        row.updated_at = datetime.now(timezone.utc)
        await session.commit()


@contextlib.asynccontextmanager
async def keepalive(model_cls, row_id: str, fields=None):
    """跑得比 STALE_AFTER 久的 pending 工作進這個 with：每半個 STALE_AFTER
    蓋一次 `updated_at`，settle 就不會把還活著的長工改判成 failed。

    `fields` = 每拍呼叫一次、回這一拍要順手寫的欄位（例如進度字）。**內容由
    呼叫端定義** —— 這裡不認得任何一張表的欄位名，否則只有一張表有的欄位
    會變成所有背景工作的共同契約。

    一拍寫失敗（DB 抖一下）只跳過那一拍：心跳死掉的代價是還活著的長工被
    settle 誤判 failed，比漏記一次進度嚴重得多。"""
    async def _beat():
        while True:
            await asyncio.sleep(_BEAT_SEC)
            try:
                await save_job_row(model_cls, row_id, **(fields() if fields else {}))
            except Exception:                    # noqa: BLE001 — 見上
                logger.warning("[keepalive] %s 這一拍沒寫成", row_id, exc_info=True)
    task = asyncio.create_task(_beat())
    try:
        yield
    finally:
        task.cancel()


async def run_claude_job(model_cls, row_id: str, prompt: str, *,
                         field: str = "content", tag: str) -> None:
    """跑一次 claude、把結果 settle 回一列。`on_start` 在拿到併發閘之後
    蓋開工時間戳 —— 這個容易漏的約定收在這裡就只需要對一次。

    （`_call_claude` 住在 services.website.seo_runner 是歷史沉積 —— 10 個
    呼叫端橫跨三個包，真正該搬的是它，見 services/website/_runner_util.py
    檔頭；本函式留在 core 是刻意的：它與 settle 是同一個 pending 生命週期
    的寫入/讀取對。）"""
    from services.website.seo_runner import _call_claude, strip_fence
    out, err = await _call_claude(
        prompt, on_start=lambda: save_job_row(model_cls, row_id))
    content = strip_fence(out).strip() if out else ""
    if not content:
        await save_job_row(model_cls, row_id, status="failed",
                           error=err or "claude 沒有回應")
        return
    await save_job_row(model_cls, row_id, status="ok", error=None,
                       **{field: content})
    logger.info("[%s] %s 完成（%d 字）", tag, row_id, len(content))
