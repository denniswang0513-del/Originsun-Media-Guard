"""core/proposal_survey.py — 提案「現況盤點」表的純邏輯（無 DB、無 IO）

owner 的 Notion 專案啟動面版有一張固定的「現況盤點」表（項目 / 內容 / 備註），
提案期間跟客戶一起把它填滿。這裡是那張表的正本：**欄目在程式碼、值在 DB**。

為什麼不把 label 也存進 DB：範本會長（owner 之後想加一列「競品」），存了
label 就等於每個提案各自凍結一份範本，新欄目永遠不會出現在舊提案上。
於是 `rows()` 每次讀都拿 TEMPLATE 重新對齊 —— 加一列 = 改這裡一行，
全部提案立刻長出來（值還在的照樣顯示）。

🔴 **加一列就是一次對外揭露**：提案公開頁會分享給客戶，所以 TEMPLATE 每一列
自帶 `public` 旗標，加新欄目時**必須當場決定**客戶看不看得到 —— 少填那一格
是語法錯誤，不是靜默把內部資訊送到客戶眼前（黑名單式的「除了預算都公開」
才會有那種事）。公開路徑一律走 `rows(..., public=True)` 與
`apply_patch(..., public=True)`，不是靠呼叫端記得過濾。
`tests/unit/test_proposal_survey.py` 有一條把公開欄目集合釘死的斷言。

自訂列（owner 臨時想加的）一律**不公開**，共用 core/row_table 的 `x-` key 契約。
"""
from __future__ import annotations

from core.row_table import (MAX_LABEL, add_custom_row, is_extra,  # noqa: F401
                            remove_custom_row)

# (key, label, hint, public) —— 順序就是表格順序。key 是 DB 存值的鍵，
# **不要改**（改了值會孤兒）。public=False = 連讀都不出公開端點。
#
# `hint` 是空格子裡的提示文字。這一欄不是裝飾：欄目名稱都很短（「品牌調性」、
# 「TA」、「播放媒介」），客戶打開連結看到一排空格會不知道要寫什麼、於是就不寫。
# 提示放在**這裡**而不是前端 —— 同一份範本餵登入後台與客戶公開頁兩個渲染端，
# 寫在其中一邊就會有一邊沒有。
TEMPLATE: tuple[tuple[str, str, str, bool], ...] = (
    ("client", "客戶", "公司/品牌全名，以及這次對接的窗口", True),
    ("brand_tone", "品牌調性", "品牌給人的感覺，例：專業穩重、年輕有活力", True),
    ("product", "產品", "這支片要講的產品或服務是什麼", True),
    ("product_tone", "產品調性", "產品本身想給的感覺，可能與品牌調性不同", True),
    ("ta", "TA", "想打中誰？年齡、身分、生活情境，例：25-40 歲雙薪家庭", True),
    ("goal", "期望目標", "影片上線後希望發生什麼事，例：提升詢問度、招募", True),
    ("style", "期望風格（1-3 個形容詞）", "例：溫暖、俐落、有故事感", True),
    ("special", "特殊需求", "一定要出現/絕對不能出現的東西，例：老闆入鏡、避免醫療宣稱", True),
    ("count_length", "支數及片長規劃", "例：主片 1 支 90 秒 + 社群短版 3 支各 15 秒", True),
    ("media", "播放媒介", "會放在哪，例：官網首頁、FB/IG、展場循環播放", True),
    # 金額 — 同 budget_range 的既有政策，客戶端連讀都不給
    ("budget", "預算", "含稅或未稅請一併註明", False),
    ("deliver_date", "預計交片時間", "有沒有非趕不可的日子，例：10/15 記者會前", True),
    ("client_refs", "客戶參考影片", "貼幾支你覺得對的片，並說說喜歡哪裡", True),
)

TEMPLATE_KEYS = tuple(k for k, _l, _h, _p in TEMPLATE)
PUBLIC_KEYS = frozenset(k for k, _l, _h, public in TEMPLATE if public)

FIELDS = ("content", "note")
MAX_TEXT = 4000          # 單格上限（現況盤點是摘要，不是腳本）


def _cell(raw) -> tuple[str, str]:
    if not isinstance(raw, dict):
        return "", ""
    return str(raw.get("content") or "")[:MAX_TEXT], str(raw.get("note") or "")[:MAX_TEXT]


def rows(stored, *, public: bool = False) -> list[dict]:
    """DB 值 → 完整表格（範本列在前、自訂列在後）。stored 可以是 None / 壞資料。

    public=True 只留 PUBLIC_KEYS —— 公開頁拿到的 payload 裡根本沒有那些列，
    不是前端隱藏。自訂列一律不公開（沒人替它決定過可見性）。
    """
    by_key: dict[str, dict] = {}
    for r in (stored or []):
        if isinstance(r, dict) and isinstance(r.get("key"), str) and r["key"]:
            by_key.setdefault(r["key"], r)

    out: list[dict] = []
    for key, label, hint, _public in TEMPLATE:
        if public and key not in PUBLIC_KEYS:
            continue
        content, note = _cell(by_key.pop(key, None))
        out.append({"key": key, "label": label, "hint": hint,
                    "content": content, "note": note})
    if public:
        return out
    # 自訂列：保留使用者給的 label 與 stored 的先後順序
    for key, r in by_key.items():
        if not is_extra(key):
            continue                      # 認不得的 key = 舊版/髒資料，不往外送
        content, note = _cell(r)
        out.append({"key": key, "label": str(r.get("label") or key)[:MAX_LABEL],
                    "hint": "", "content": content, "note": note})
    return out


def apply_patch(stored, key: str, field: str, value, *, public: bool = False):
    """單格寫入 → (新的 stored, 錯誤訊息)。錯誤時 stored 原樣退回。

    只認得出來的 key 才寫得進去 —— 不讓 PATCH 順手長出新欄目（新增列走 add_row，
    那條有上限）。"""
    if field not in FIELDS:
        return stored, f"field 只能是 {' / '.join(FIELDS)}"
    if not isinstance(key, str) or not (key in TEMPLATE_KEYS or is_extra(key)):
        return stored, "未知的欄目"
    if public and key not in PUBLIC_KEYS:
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
    new, err = add_custom_row(rows(stored), label, key_seed=key_seed,
                              blank={"content": "", "note": ""}, noun="欄目")
    return (stored, err) if err else (new, "")


def remove_row(stored, key: str):
    """刪一列自訂欄目 → (新的 stored, 錯誤訊息)。範本列不給刪（清空即可）。"""
    new, err = remove_custom_row(rows(stored), key, noun="欄目")
    return (stored, err) if err else (new, "")
