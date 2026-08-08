"""core/row_table.py — 「範本列 + 自訂列」表格的共用零件

`core/proposal_survey.py`（提案現況盤點）與 `core/project_archive.py`（結案歸檔
清單）是同一種東西：一張欄目在程式碼、值在 DB 的表，使用者可以在範本列之後
加自己的列。兩邊的「加一列 / 刪一列」除了錯誤訊息裡的名詞之外一模一樣，
自訂列的 key 格式更是**跨兩張表的資料契約**，所以收在這裡一份。

刻意**不**把 `rows()` / `apply_patch()` 也抽上來：那兩支的差異（公開過濾、
狀態列舉正規化、欄位形狀）要靠一包 config 才吃得下，抽象出來會比兩份具體
程式碼更難讀 —— 而那兩份具體程式碼的價值正是「一眼看得出這張表長什麼樣」。
"""
from __future__ import annotations

import re

# 自訂列的 key 格式（DB 值 + 前端 `startsWith('x-')` 判斷共用的契約）
EXTRA_KEY_PREFIX = "x-"
EXTRA_KEY_RE = re.compile(r"^x-[0-9a-f]{6}$")

MAX_EXTRA_ROWS = 20
MAX_LABEL = 40


def is_extra(key) -> bool:
    return bool(EXTRA_KEY_RE.match(str(key or "")))


def add_custom_row(cur: list, label, *, key_seed: str, blank: dict, noun: str):
    """在正規化過的 cur 後面加一列自訂欄目 → (新清單, 錯誤訊息)。

    blank = 那張表除了 key/label 之外的欄位預設值（盤點是 content/note，
    歸檔是 hint/folder/status/note）。noun 只進錯誤訊息。"""
    name = str(label or "").strip()[:MAX_LABEL]
    if not name:
        return None, f"{noun}名稱必填"
    if sum(1 for r in cur if is_extra(r["key"])) >= MAX_EXTRA_ROWS:
        return None, f"自訂{noun}最多 {MAX_EXTRA_ROWS} 列"
    key = f"{EXTRA_KEY_PREFIX}{str(key_seed)[:6].lower()}"
    if not is_extra(key) or any(r["key"] == key for r in cur):
        return None, f"{noun} key 產生失敗，請再試一次"
    return cur + [{"key": key, "label": name, **blank}], ""


def remove_custom_row(cur: list, key, *, noun: str):
    """刪一列自訂欄目 → (新清單, 錯誤訊息)。範本列不給刪（清空/標不適用即可）。"""
    if not is_extra(key):
        return None, f"只能刪自訂{noun}"
    left = [r for r in cur if r["key"] != key]
    if len(left) == len(cur):
        return None, f"找不到這個{noun}"
    return left, ""
