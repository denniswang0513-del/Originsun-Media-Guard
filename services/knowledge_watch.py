# -*- coding: utf-8 -*-
"""services/knowledge_watch.py — 研究助理的每週排程（docs/KNOWLEDGE_BASE_PLAN.md §9.3）。

owner 2026-09-18：「定期依照我書的內容主題幫我做研究助理，持續把一些網路上的研究
（不限制中文，如果是英文或其他語言也可以，附上出處翻譯與摘要給我）」。

一週一次、一本一次、一次最多 8 則；找到的東西進那本書的 `延伸.md`（跟手動「去找新的」同一支
`knowledge_service.run_extend`，所以安全規則也是同一套：只給 WebSearch／WebFetch、cwd 是臨時空目錄、
回來的東西只當資料寫檔）。

兩道開關都**預設關**（同 `intel.enabled`／`social.enabled` 的規矩 —— 這一條會叫 claude 上網，
要 owner 自己開，不然額度會默默被吃掉）：
  1. 全域 `settings knowledge.watch.enabled`
  2. 每本書的 `meta.watch`（書頁自己開）

排程底座是 `core.scheduler._run_daily_master_task`：master gate、當日去重、時刻判斷都在那裡。
星期幾在這裡判；每本書另記 `meta.watch_last`（ISO 週）避免同一週重跑（排程重啟或跨日補跑時會用到）。
"""
from __future__ import annotations

import logging
from datetime import datetime

from services import knowledge_service as ks

logger = logging.getLogger(__name__)

#: 一次排程最多處理幾本（避免一次開一整排書時把整晚塞滿）
MAX_BOOKS_PER_RUN = 6
#: 這個功能在 `_daily_fired` 裡的名字
TASK_KEY = "knowledge_watch"


def settings_watch() -> dict:
    """`settings.knowledge.watch`（讀不到就用預設：關、週日、21 點）。"""
    try:
        from config import load_settings
        raw = ((load_settings().get("knowledge") or {}).get("watch") or {})
    except Exception:
        raw = {}
    return {
        "enabled": bool(raw.get("enabled")),
        "weekday": _int_in(raw.get("weekday"), 6, 0, 6),      # 0＝週一…6＝週日
        "hour": _int_in(raw.get("hour"), 21, 0, 23),
    }


def _int_in(v, default: int, lo: int, hi: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if lo <= n <= hi else default


def save_watch(*, enabled=None, weekday=None, hour=None) -> dict:
    """只寫 `knowledge.watch` 這一格（形狀在這裡夾好），回存完的值。

    🔴 不要改走 /api/settings/save：那條的合併只到第二層，而 /api/settings/load 會把
    `knowledge.root` 抹掉（api_system._SECRET_SUBKEYS）—— 前端拿不到 root 又送回去，
    書架的路徑就沒了。`config.save_settings` 對 `knowledge` 是逐鍵合併，只送 watch 是安全的。
    """
    cur = settings_watch()
    nxt = {
        "enabled": cur["enabled"] if enabled is None else bool(enabled),
        "weekday": cur["weekday"] if weekday is None else _int_in(weekday, cur["weekday"], 0, 6),
        "hour": cur["hour"] if hour is None else _int_in(hour, cur["hour"], 0, 23),
    }
    from config import save_settings
    save_settings({"knowledge": {"watch": nxt}})
    return nxt


def watch_hour(settings: dict) -> int:
    """給 `_run_daily_master_task` 的 `hour_getter` —— 知識庫的鐘點不在 `finance` 底下。"""
    return _int_in(((settings.get("knowledge") or {}).get("watch") or {}).get("hour"), 21, 0, 23)


def iso_week(now: datetime) -> str:
    """`2026-W38`：同一週只跑一次的比對用。"""
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def due_books(now: datetime) -> list:
    """這一輪該跑的書：開了 watch、有檔（待補的沒東西可讀）、這一週還沒跑過。"""
    week = iso_week(now)
    out = []
    for b in ks.list_books():
        if not b.get("watch") or b.get("status") == "pending":
            continue
        try:
            if ks.watch_last(b["id"]) == week:
                continue
        except ks.BookNotFound:
            continue
        out.append(b["id"])
        if len(out) >= MAX_BOOKS_PER_RUN:
            break
    return out


async def run_once(now: datetime) -> int:
    """跑這一輪：回實際處理了幾本。一本失敗記 log 換下一本（不要因為一本卡住整批）。"""
    week = iso_week(now)
    done = 0
    for book_id in due_books(now):
        try:
            ks.start_extend(book_id)          # 這本正在找（或正在被手動觸發）就跳過，下週再說
        except (ks.BookBusy, ks.BookNotFound, ValueError):
            continue
        try:
            await ks.run_extend(book_id)      # 走 _KNOWLEDGE_GATE，一次一本，不搶報價助理
        except Exception:
            logger.exception("研究助理：這本失敗了 %s", book_id)
        # 成功或失敗都記這一週 —— 失敗就下週再試，不要同一晚重打同一本
        try:
            ks.set_watch_last(book_id, week)
        except ks.BookNotFound:
            pass
        done += 1
    return done


async def weekly_check() -> None:
    """排程每分鐘叫一次；真正的 gate 在 `_run_daily_master_task` 與下面三個條件裡。"""
    conf = settings_watch()
    if not conf["enabled"]:
        return
    from core.scheduler import _run_daily_master_task

    async def _body(factory, now):                 # factory 用不到（這功能不碰 DB）
        if now.weekday() != conf["weekday"]:
            return
        from services.knowledge_claude import claude_available
        if not claude_available():
            logger.info("研究助理：這台沒有 claude CLI，這週跳過")
            return
        # 🔴 tick 只負責「該跑就丟背景任務」—— 六本書各跑七分鐘就是四十分鐘，
        # 在這裡 await 下去會把整個排程迴圈（貸款提醒、財務行事曆…）一起停掉。
        # 同 reference_archiver 的規矩。
        from core.bg_task import fire
        fire(run_once(now), label="knowledge watch")

    await _run_daily_master_task(TASK_KEY, "", _body, hour_getter=watch_hour)
