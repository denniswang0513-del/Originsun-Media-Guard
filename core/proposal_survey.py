"""core/proposal_survey.py — 提案「現況盤點」表的純邏輯（無 DB、無 IO）

owner 的 Notion 專案啟動面版有一張固定的「現況盤點」表（項目 / 內容 / 備註），
提案期間跟客戶一起把它填滿。這裡是那張表的正本：**欄目在程式碼、值在 DB**。

為什麼不把 label 也存進 DB：範本會長（owner 之後想加一列「競品」），存了
label 就等於每個提案各自凍結一份範本，新欄目永遠不會出現在舊提案上。
於是 `rows()` 每次讀都拿 TEMPLATE 重新對齊 —— 加一列 = 改這裡一行，
全部提案立刻長出來（值還在的照樣顯示）。

自訂列（owner 臨時想加的）用 `x-` 開頭的 key 存在同一份清單裡、排在範本列
之後，label 由使用者給、程式不動。

🔴 `PRIVATE_KEYS` 是「連讀都不給公開連結看」的欄目 —— 提案公開頁會分享給客戶，
預算不能出現在那裡（同 api_proposals 對 budget_range / outcome_reason 的既有政策）。
公開路徑一律走 `rows(..., public=True)` 與 `apply_patch(..., public=True)`，
不是靠呼叫端記得過濾。
"""
from __future__ import annotations

import re

# (key, label) —— 順序就是表格順序。key 是 DB 存值的鍵，**不要改**（改了值會孤兒）。
TEMPLATE: tuple[tuple[str, str], ...] = (
    ("client", "客戶"),
    ("brand_tone", "品牌調性"),
    ("product", "產品"),
    ("product_tone", "產品調性"),
    ("ta", "TA"),
    ("goal", "期望目標"),
    ("style", "期望風格（1-3 個形容詞）"),
    ("special", "特殊需求"),
    ("count_length", "支數及片長規劃"),
    ("media", "播放媒介"),
    ("budget", "預算"),
    ("deliver_date", "預計交片時間"),
    ("client_refs", "客戶參考影片"),
)

TEMPLATE_KEYS = tuple(k for k, _ in TEMPLATE)
_LABELS = dict(TEMPLATE)

# 公開連結看不到的欄目（金額）。organisational learning 那類欄位不在這張表上。
PRIVATE_KEYS = frozenset({"budget"})

FIELDS = ("content", "note")
MAX_TEXT = 4000          # 單格上限（現況盤點是摘要，不是腳本）
MAX_EXTRA_ROWS = 20      # 自訂列上限
MAX_LABEL = 40
_EXTRA_KEY_RE = re.compile(r"^x-[0-9a-f]{6}$")


def _cell(raw) -> tuple[str, str]:
    if not isinstance(raw, dict):
        return "", ""
    return str(raw.get("content") or "")[:MAX_TEXT], str(raw.get("note") or "")[:MAX_TEXT]


def rows(stored, *, public: bool = False) -> list[dict]:
    """DB 值 → 完整表格（範本列在前、自訂列在後）。stored 可以是 None / 壞資料。

    public=True 時抽掉 PRIVATE_KEYS —— 公開頁拿到的 payload 裡根本沒有那一列，
    不是前端隱藏。
    """
    by_key: dict[str, dict] = {}
    for r in (stored or []):
        if isinstance(r, dict) and isinstance(r.get("key"), str) and r["key"]:
            by_key.setdefault(r["key"], r)

    out: list[dict] = []
    for key, label in TEMPLATE:
        if public and key in PRIVATE_KEYS:
            continue
        content, note = _cell(by_key.pop(key, None))
        out.append({"key": key, "label": label, "content": content, "note": note})
    # 自訂列：保留使用者給的 label 與 stored 的先後順序
    for key, r in by_key.items():
        if not _EXTRA_KEY_RE.match(key):
            continue                      # 認不得的 key = 舊版/髒資料，不往外送
        content, note = _cell(r)
        out.append({"key": key, "label": str(r.get("label") or key)[:MAX_LABEL],
                    "content": content, "note": note})
    return out


def apply_patch(stored, key: str, field: str, value, *, public: bool = False):
    """單格寫入 → (新的 stored, 錯誤訊息)。錯誤時 stored 原樣退回。

    只認得出來的 key 才寫得進去 —— 不讓 PATCH 順手長出新欄目（新增列走 add_row，
    那條有上限）。"""
    if field not in FIELDS:
        return stored, f"field 只能是 {' / '.join(FIELDS)}"
    if not isinstance(key, str) or not key:
        return stored, "key 必填"
    known = key in _LABELS or _EXTRA_KEY_RE.match(key)
    if not known:
        return stored, "未知的欄目"
    if public and key in PRIVATE_KEYS:
        return stored, "這個欄目不開放公開連結編輯"
    text = "" if value is None else str(value)
    if len(text) > MAX_TEXT:
        return stored, f"單格內容超過 {MAX_TEXT} 字上限"

    cur = rows(stored)                                   # 正規化後再改：髒資料一併洗掉
    for r in cur:
        if r["key"] == key:
            r[field] = text
            return cur, ""
    # 範本列在 rows() 裡一定有；走到這裡代表是還沒建立的自訂列
    return stored, "未知的欄目"


def add_row(stored, label: str, *, key_seed: str):
    """新增一列自訂欄目 → (新的 stored, 錯誤訊息)。key_seed 由呼叫端給
    （uuid hex），讓這個函式維持純函式、可測。"""
    name = str(label or "").strip()[:MAX_LABEL]
    if not name:
        return stored, "欄目名稱必填"
    cur = rows(stored)
    extras = [r for r in cur if _EXTRA_KEY_RE.match(r["key"])]
    if len(extras) >= MAX_EXTRA_ROWS:
        return stored, f"自訂欄目最多 {MAX_EXTRA_ROWS} 列"
    key = f"x-{str(key_seed)[:6].lower()}"
    if not _EXTRA_KEY_RE.match(key) or any(r["key"] == key for r in cur):
        return stored, "欄目 key 產生失敗，請再試一次"
    cur.append({"key": key, "label": name, "content": "", "note": ""})
    return cur, ""


def remove_row(stored, key: str):
    """刪一列自訂欄目 → (新的 stored, 錯誤訊息)。範本列不給刪（清空即可）。"""
    if not _EXTRA_KEY_RE.match(str(key or "")):
        return stored, "只能刪自訂欄目"
    cur = rows(stored)
    left = [r for r in cur if r["key"] != key]
    if len(left) == len(cur):
        return stored, "找不到這個欄目"
    return left, ""


def filled_count(stored) -> int:
    """填了幾格（只看內容欄）—— 提案列表/卡片顯示完成度用。"""
    return sum(1 for r in rows(stored) if r["content"].strip())
