# -*- coding: utf-8 -*-
"""services/knowledge_claude.py — 知識庫叫 claude CLI 的那一條線（串流版，通用）。

抄自 `routers/crm/quotes._call_claude_stream`（規劃正本 docs/KNOWLEDGE_BASE_PLAN.md §3.2）：
那支綁著 `_chat_partial[quotation_id]` 與報價助理自己的閘，不改它；這裡把「寫到哪裡」
改成回呼（`on_partial`／`on_stage`），編譯與討論兩條路共用。

🔴 自己的閘 `_KNOWLEDGE_GATE`（Semaphore(1)）：編譯一本書 10–20 分鐘，跟報價助理的
`_QUOTE_CHAT_GATE` 或 SEO 的閘共用會把別人卡死；一次只編一本也剛好是我們要的。

回覆是**純文字**（不像報價那支要從半截 JSON 撈 reply），所以 partial 就是 delta 串起來。
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Callable, Optional

#: 一次只跑一件知識庫的 AI 工作（見檔頭）
_KNOWLEDGE_GATE = asyncio.Semaphore(1)
#: 串流回呼多久才推一次 partial。前端一秒讀一次，再密也沒人看得到；不節流的話一輪上千個 delta 每個都 join 全文。
_PARTIAL_PUSH_EVERY_SEC = 0.25
#: claude 失敗時往回帶多少 stderr（夠看出原因，又不會把整頁 log 灌進 UI）
_ERR_DETAIL_CHARS = 300


def claude_available() -> bool:
    """這台有沒有 claude CLI（NAS 容器沒有；端點先用它講實話回 503）。"""
    from services.website.seo_runner import _resolve_claude_exe
    return bool(_resolve_claude_exe())


async def call_claude(prompt: str, *, model: str, cwd: str, allowed_tools: str = "",
                      timeout_sec: float, on_partial: Optional[Callable[[str], None]] = None,
                      on_stage: Optional[Callable[[str], None]] = None) -> tuple:
    """串流叫一次 claude：回 `(完整文字, 錯誤字串)`；失敗時文字是 None。

    - `model`：一律先過 `core.quote_chat.pick_model` 的白名單再傳進來（它會變成 `--model` 參數）。
    - `cwd`：這本書的資料夾（讓它 Read `chapters/...` 相對路徑就中）；不給就用暫存目錄。
    - `allowed_tools`：預設**沒有工具**（`--tools ""`）—— 編譯三個 pass 全部靠 stdin 的文字，不需要任何工具。
      討論才給 "Read"（按需翻 chapters/），並配 plan mode 把寫入全部擋掉。使用者打的字會進提示，不限工具的話
      提示注入就能叫它去 Bash。
      🔴 為什麼編譯不能給 Read＋plan mode：2026-09-18 實測，claude 看到「產四個檔」＋手上有工具＋plan mode，
      就把任務當成「要寫檔」→ 寫一份計畫、回「請確認我再建檔」，四個檔一個都沒輸出（ch02／ch03 也被這樣污染）。
    - `on_partial(text)`：串到目前為止的全文（節流到 4 Hz）；`on_stage("queued"|"running")`：卡在閘門／真的在跑。
    """
    import tempfile

    from core.subproc import run_stream
    from services.website.seo_runner import _resolve_claude_exe

    exe = _resolve_claude_exe()
    if not exe:
        return None, "找不到 claude CLI（請確認已安裝並 claude 登入）"

    chunks: list = []
    final_box: list = []          # result 那一行（等同 --print 的最終文字）
    last_push = [0.0]             # 上次推 partial 的時間

    def _on_line(raw: bytes) -> None:
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except (ValueError, UnicodeDecodeError):
            return                                    # 不是 JSON 的雜訊行，跳過
        kind = d.get("type")
        if kind == "result":
            # 讀的時候就撈起來：收尾再把整包 stdout（幾百 KB、上千行）解析一遍是白工
            if isinstance(d.get("result"), str):
                final_box.append(d["result"])
            return
        if kind != "stream_event":
            return
        ev = d.get("event") or {}
        if ev.get("type") != "content_block_delta":
            return
        chunks.append(((ev.get("delta") or {}).get("text")) or "")
        if on_partial is None:
            return
        now = time.monotonic()
        if now - last_push[0] < _PARTIAL_PUSH_EVERY_SEC:
            return
        last_push[0] = now
        on_partial("".join(chunks))

    args = [exe, "--print", "--model", model]
    if allowed_tools:
        # 有工具才進 plan mode（唯讀）；--tools 限縮內建集合、--allowedTools 免逐次問
        args += ["--permission-mode", "plan", "--tools", allowed_tools, "--allowedTools", allowed_tools]
    else:
        args += ["--tools", ""]                  # 沒有工具＝它只能把答案寫在回覆裡
    args += ["--output-format", "stream-json", "--include-partial-messages", "--verbose"]

    if on_stage:
        on_stage("queued")                       # 閘門滿了就會真的卡在下一行
    async with _KNOWLEDGE_GATE:
        if on_stage:
            on_stage("running")
        rc, out, err = await run_stream(
            args,
            on_line=_on_line,
            input_bytes=prompt.encode("utf-8"),
            cwd=cwd or tempfile.gettempdir(),
            timeout=timeout_sec,
        )

    if rc != 0:
        detail = (err or b"")[:_ERR_DETAIL_CHARS].decode("utf-8", "replace").strip()
        return None, f"claude 結束碼={rc}；{detail}"

    # 收尾優先用 result 那一行（讀的時候就撈好了）；沒有就把 delta 串起來
    return ((final_box[-1] if final_box else "") or "".join(chunks)), ""
