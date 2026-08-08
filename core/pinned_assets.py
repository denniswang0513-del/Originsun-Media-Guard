"""core/pinned_assets.py — 提案的「重點提案」勾選（純邏輯，不碰磁碟/DB）。

提案資產夾裡混著報價、成本、內部版腳本、歷版簡報。同事在上面勾幾個項目，
那些就是**此刻的重點**——不一定是最終版，但是現在要人看的那一版。勾選是
**策展**：不搬檔案、不複製、隨時可改。

⚠️ 這取代了舊的「對外分享」子夾（2026-08-07~08 存在了兩天）。那個做法要求檔案
   **實際位在**某個特定名字的資料夾裡，於是只能二選一：把簡報從 PPM 結構裡搬
   出來（破壞原本的整理），或複製一份（改了 v3 客戶還在看 v2）。勾選兩個問題
   都沒有。舊夾若已有檔案，由 `adopt_legacy_share_dir` 自動轉成一個勾選項。

🔴 **`allows()` 是這個模組的安全核心**：客戶那條路能不能拿到某個檔，只由它決定。
   公開端點一律問它，不做「開放瀏覽再過濾」——那種寫法漏一個分支就是整個資產夾
   外洩（裡面有報價與成本）。

路徑一律用 `/` 分隔的相對路徑（前端 `list_folder_files` 就是這個形式），
比對**不分大小寫** —— 目的地是 Windows/SMB，`Deck.pdf` 與 `deck.pdf` 在那裡
是同一個檔，判定跟著檔案系統的真實行為走才不會出現「擋得住比對、擋不住開檔」。
"""
from __future__ import annotations

from datetime import datetime, timezone

# 一個提案能勾幾項。不是技術限制，是「重點」的定義 —— 勾 50 個就沒有重點了。
MAX_PINS = 30

# 勾選項的欄位（thumb_url 由伺服器事後補算，可能一直是空的）
FIELDS = ("rel", "is_dir", "thumb_url", "pinned_at", "pinned_by")


def _clean_rel(rel) -> str:
    """正規化成 `/` 分隔、去頭尾斜線的相對路徑；不合法 → ""。

    `..` 一律拒收 —— 這個值會被拿去組真實路徑。呼叫端另有 `safe_rel_path` 做
    第二層（規則 + realpath 各一層），但這裡不能因此就放行。
    """
    s = str(rel or "").replace("\\", "/").strip("/")
    if not s or any(seg in ("", ".", "..") for seg in s.split("/")):
        return ""
    return s


def _key(rel: str) -> str:
    return _clean_rel(rel).casefold()


def rows(stored) -> list:
    """DB 值 → 乾淨的勾選清單（保序、去重、丟掉壞資料）。

    讀的時候修，不是寫的時候信 —— 這份 JSONB 可能被舊版寫過或被人手改過。
    """
    out, seen = [], set()
    for item in (stored or []):
        if not isinstance(item, dict):
            continue
        rel = _clean_rel(item.get("rel"))
        if not rel or _key(rel) in seen:
            continue
        seen.add(_key(rel))
        out.append({
            "rel": rel,
            "is_dir": bool(item.get("is_dir")),
            "thumb_url": str(item.get("thumb_url") or ""),
            "pinned_at": str(item.get("pinned_at") or ""),
            "pinned_by": str(item.get("pinned_by") or ""),
        })
    return out


def allows(stored, rel: str) -> bool:
    """客戶能不能拿到 `rel`？

    放行條件只有兩個：它**就是**某個勾選項，或它**位在某個勾選的資料夾底下**。
    前綴比對必須帶分隔符邊界 —— 只比 startswith 的話，勾了 `提案` 會連
    `提案外流備份/` 一起放出去（同 core.project_folders.remap_prefix 的那個坑）。
    """
    want = _key(rel)
    if not want:
        return False
    for r in rows(stored):
        pin = _key(r["rel"])
        if want == pin:
            return True
        if r["is_dir"] and want.startswith(pin + "/"):
            return True
    return False


def pin(stored, rel: str, *, is_dir: bool, by: str = "",
        now: datetime | None = None) -> tuple:
    """勾一項 → `(新清單, 錯誤訊息)`。已勾過 → 原樣回（冪等，重複點不報錯）。"""
    cleaned = _clean_rel(rel)
    if not cleaned:
        return rows(stored), "路徑不合法"
    cur = rows(stored)
    if any(_key(r["rel"]) == _key(cleaned) for r in cur):
        return cur, ""
    if len(cur) >= MAX_PINS:
        return cur, f"最多只能勾 {MAX_PINS} 項"
    cur.append({
        "rel": cleaned,
        "is_dir": bool(is_dir),
        "thumb_url": "",
        "pinned_at": (now or datetime.now(timezone.utc)).isoformat(),
        "pinned_by": str(by or "")[:64],
    })
    return cur, ""


def unpin(stored, rel: str) -> list:
    """取消勾選。客戶那條路**立刻**失效（allows 下一次就回 False）。"""
    want = _key(rel)
    return [r for r in rows(stored) if _key(r["rel"]) != want]


def reorder(stored, order) -> list:
    """依 `order`（rel 清單）重排；沒出現在 order 裡的保持原相對順序排在後面。"""
    cur = rows(stored)
    rank = {_key(r): i for i, r in enumerate(order or []) if _clean_rel(r)}
    return sorted(cur, key=lambda r: (rank.get(_key(r["rel"]), len(rank)),))


def set_thumb(stored, rel: str, thumb_url: str) -> list:
    """補上事後算好的縮圖網址（算縮圖是背景工作，不擋勾選）。"""
    want = _key(rel)
    cur = rows(stored)
    for r in cur:
        if _key(r["rel"]) == want:
            r["thumb_url"] = str(thumb_url or "")
    return cur


def needs_thumb(stored) -> list:
    """還沒有縮圖的**檔案**項（資料夾不算）→ rel 清單。"""
    return [r["rel"] for r in rows(stored) if not r["is_dir"] and not r["thumb_url"]]


LEGACY_SHARE_DIR = "對外分享"


def adopt_legacy_share_dir(stored, *, exists: bool, now: datetime | None = None) -> list:
    """舊的「對外分享」夾若還在且有東西 → 自動轉成一個勾選項（一次性、冪等）。

    客戶手上可能已經有指向那個夾的連結；直接砍掉舊機制會讓他們看到的東西
    憑空消失。轉成勾選項之後行為完全一樣（照樣看得到整夾），而程式裡不再有
    「某個魔法資料夾名」這個概念。
    """
    if not exists:
        return rows(stored)
    cur, _err = pin(stored, LEGACY_SHARE_DIR, is_dir=True, by="（沿用舊分享夾）",
                    now=now)
    return cur
