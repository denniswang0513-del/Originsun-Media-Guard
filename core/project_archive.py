"""core/project_archive.py — 結案歸檔清單 + 專案回顧（KPTA）的純邏輯（無 DB、無 IO）

對齊 owner 的 Notion 專案啟動面版兩塊：「歸檔資料確認事項」與「專案回顧」。

與 core/proposal_survey 同一套設計：**欄目在程式碼、值在 DB**。範本每次讀時
對齊 —— 之後想加一列「客戶簽收單」，改這裡一行，全部專案立刻長出來。

每個範本項目多帶一個 `folder`：一鍵建歸檔資料夾時的子夾名，也是「掃描自動勾」
的對照 —— 夾裡有東西就把那一列標成已收。自訂列沒有 folder（掃不到、手動勾）。
"""
from __future__ import annotations

from core.row_table import (MAX_LABEL, add_custom_row, is_extra,  # noqa: F401
                            remove_custom_row)

# (key, label, hint, folder) —— 順序就是清單順序。key 是存值的鍵，**不要改**。
TEMPLATE: tuple[tuple[str, str, str, str], ...] = (
    ("ppm", "PPM資料", "Final PPM／Final 腳本／通告單／場記表", "01_PPM資料"),
    ("final", "完成檔", "clean 檔／final 檔", "02_完成檔"),
    ("stills", "截圖", "3-5 張", "03_截圖"),
    ("license", "版權相關授權資料", "肖像權使用同意書／音樂授權書／童工證", "04_版權授權"),
    ("docs", "案件資料及官網上架欄位文件", "案件資料／官網上架文件", "05_案件資料"),
)

TEMPLATE_KEYS = tuple(k for k, _l, _h, _f in TEMPLATE)

# 歸檔子夾都建在專案資產夾的這個夾底下（與提案期的檔案分開）
ROOT_FOLDER = "歸檔"

TODO, DONE, NA = "待收", "已收", "不適用"
STATUSES = (TODO, DONE, NA)

FIELDS = ("status", "note")
MAX_TEXT = 2000

# KPTA（專案回顧）—— 固定四欄，沒有自訂
KPTA_FIELDS: tuple[tuple[str, str], ...] = (
    ("keep", "Keep：做得好的地方、應持續的地方"),
    ("problem", "Problem：問題、不順利的地方"),
    ("try", "Try：想做的嘗試、問題的解決方案"),
    ("action", "Action：具體落實 Try 的行動"),
)
KPTA_KEYS = tuple(k for k, _ in KPTA_FIELDS)
KPTA_MAX = 8000


def _norm_status(raw) -> str:
    return raw if raw in STATUSES else TODO


def rows(stored) -> list[dict]:
    """DB 值 → 完整清單（範本列在前、自訂列在後）。stored 可以是 None / 壞資料。"""
    by_key: dict[str, dict] = {}
    for r in (stored or []):
        if isinstance(r, dict) and isinstance(r.get("key"), str) and r["key"]:
            by_key.setdefault(r["key"], r)

    out: list[dict] = []
    for key, label, hint, folder in TEMPLATE:
        r = by_key.pop(key, None) or {}
        out.append({"key": key, "label": label, "hint": hint, "folder": folder,
                    "status": _norm_status(r.get("status")),
                    "note": str(r.get("note") or "")[:MAX_TEXT]})
    for key, r in by_key.items():
        if not is_extra(key):
            continue                      # 認不得的 key = 舊版/髒資料
        out.append({"key": key, "label": str(r.get("label") or key)[:MAX_LABEL],
                    "hint": str(r.get("hint") or "")[:MAX_TEXT], "folder": "",
                    "status": _norm_status(r.get("status")),
                    "note": str(r.get("note") or "")[:MAX_TEXT]})
    return out


def progress(stored) -> dict:
    """齊備度。**「不適用」算數** —— 沒有童工的案子不該永遠卡在 4/5。"""
    return progress_of(rows(stored))


def progress_of(cur: list) -> dict:
    """已經正規化過的清單直接算（呼叫端手上有 rows() 結果時用，免算第二次）。"""
    done = sum(1 for r in cur if r["status"] in (DONE, NA))
    return {"done": done, "total": len(cur), "ready": bool(cur) and done == len(cur)}


# 掃描時只認得的資料夾名（TEMPLATE 的 folder 欄）—— 掃描端據此跳過使用者
# 自己在歸檔夾裡開的其他夾，不必為了掃了也丟掉的東西付 SMB 往返。
KNOWN_FOLDERS = frozenset(f for _k, _l, _h, f in TEMPLATE if f)


def apply_patch(stored, key: str, field: str, value):
    """單格寫入 → (新的 stored, 錯誤訊息)。錯誤時 stored 原樣退回。"""
    if field not in FIELDS:
        return stored, f"field 只能是 {' / '.join(FIELDS)}"
    if not isinstance(key, str) or not (key in TEMPLATE_KEYS or is_extra(key)):
        return stored, "未知的項目"
    if field == "status":
        if value not in STATUSES:
            return stored, f"狀態只能是 {' / '.join(STATUSES)}"
        text = value
    else:
        text = "" if value is None else str(value)
        if len(text) > MAX_TEXT:
            return stored, f"備註超過 {MAX_TEXT} 字上限"

    cur = rows(stored)
    for r in cur:
        if r["key"] == key:
            r[field] = text
            return cur, ""
    return stored, "未知的項目"


def add_row(stored, label: str, *, key_seed: str):
    """新增一列自訂歸檔項目 → (新的 stored, 錯誤訊息)。"""
    new, err = add_custom_row(rows(stored), label, key_seed=key_seed,
                              blank={"hint": "", "folder": "", "status": TODO, "note": ""},
                              noun="項目")
    return (stored, err) if err else (new, "")


def remove_row(stored, key: str):
    """刪一列自訂歸檔項目 → (新的 stored, 錯誤訊息)。範本列不給刪（標不適用即可）。"""
    new, err = remove_custom_row(rows(stored), key, noun="項目")
    return (stored, err) if err else (new, "")


def apply_scan(stored, non_empty_folders):
    """掃描結果 → 自動把「有檔案的資料夾」對應的列標成已收。

    只往前推進，**不會**把人手動標的「已收 / 不適用」倒退回待收 —— 檔案放在
    別的地方（Drive、實體本）也是正當的歸檔方式，掃不到不代表沒有。
    回 (新的 stored, 這次新標了哪些 key)。
    """
    folders = {f for f in (non_empty_folders or []) if f}
    cur = rows(stored)
    marked = []
    for r in cur:
        if r["folder"] and r["folder"] in folders and r["status"] == TODO:
            r["status"] = DONE
            marked.append(r["key"])
    return cur, marked


def kpta(stored) -> dict:
    """DB 值 → 四欄齊全的 KPTA（缺的補空字串）。"""
    src = stored if isinstance(stored, dict) else {}
    return {k: str(src.get(k) or "")[:KPTA_MAX] for k in KPTA_KEYS}


def apply_kpta(stored, key: str, value):
    """KPTA 單欄寫入 → (新的 dict, 錯誤訊息)。"""
    if key not in KPTA_KEYS:
        return stored, "未知的回顧欄位"
    text = "" if value is None else str(value)
    if len(text) > KPTA_MAX:
        return stored, f"單欄超過 {KPTA_MAX} 字上限"
    cur = kpta(stored)
    cur[key] = text
    return cur, ""
