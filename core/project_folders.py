"""core/project_folders.py — 資產資料夾的命名與改名跟隨（單一正本）

三個子系統共用同一套規則（2026-08-06 owner 指定）：

| 子系統   | 根目錄                  | 每夾命名              | 改名觸發     |
|----------|------------------------|----------------------|-------------|
| 影像紀錄 | `media_log.root`       | `{專案建立日}_{專案名}` | 專案改名     |
| 提案庫   | `proposals.root`       | `{專案建立日}_{專案名}` | 專案改名     |
| 片庫     | `reference_archive.dir`| `{片名}_{品牌}`        | 片名/品牌改動 |

日期一律取**專案建立日**（不是當天）—— 改名時資料夾不會跳到今天。

🔴 **改名的鐵則：實體 rename 與 DB 內絕對路徑的更新必須同進退。**
影像紀錄的 `ProjectMediaFile.stored_path`、片庫的 `PreprodReference.archive_path`
存的都是絕對路徑；只 rename 不更新 DB → 舊記錄指向不存在的路徑，而新路徑的
檔案會被資料夾↔DB 同步當成「新檔」再匯入一遍（同一張照片兩筆記錄）。
所以呼叫端一律：rename_dir 成功後、同一個 session 內做 remap_prefix 批次更新。

改名失敗（檔案被開著 → PermissionError）不可以讓來源改名整個失敗：
呼叫端拿到 (False, err) 就保留舊資料夾名 + 把 err 當 warning 回前端，
**名稱本身照樣改成功**。資料夾名與顯示名暫時不同步，下次改名會再試一次。

純函式（clean_name / make_* / remap_prefix）不碰磁碟，單元測試對象見
tests/unit/test_project_folders.py。
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Callable, Optional

# Windows 檔名/資料夾名非法字元（與 routers/crm/media_log.py 的檔名清洗同源）
_ILLEGAL_NAME_CHARS = '<>:"/\\|?*'
_MAX_SEGMENT_LEN = 80          # 單一名稱片段上限 —— 路徑總長 260 的保險


def clean_name(s: str) -> str:
    """移除 Windows 非法字元與控制字元 + strip。"""
    return "".join(
        ch for ch in str(s or "")
        if ch not in _ILLEGAL_NAME_CHARS and ord(ch) >= 32
    ).strip()


def _segment(s: str, limit: int = _MAX_SEGMENT_LEN) -> str:
    """清洗 + 截斷單一名稱片段（片名/專案名可能很長，路徑有 260 上限）。"""
    return clean_name(s)[:limit].strip()


def _dedupe(base: str, taken: Optional[set]) -> str:
    """撞名補 -2、-3…（taken = 同 root 底下已被佔用的名字集合）。"""
    name, i = base, 2
    while name in (taken or set()):
        name = f"{base}-{i}"
        i += 1
    return name


_DATE_PREFIX_RE = re.compile(r"^(\d{8})_")


def make_dated_folder_name(project_name: str, created: datetime,
                           taken: Optional[set] = None,
                           existing: str = "") -> str:
    """影像紀錄 / 提案庫：`{建立日 YYYYMMDD}_{專案名}`（owner 指定格式）。

    `existing`（改名時傳舊資料夾名）帶得動日期前綴就**沿用它** —— 改個名字
    不該讓資料夾的日期跳掉，那會讓人以為是新的一批。舊名沒有日期前綴
    （手動建的夾）或新建時 → 用 created（專案建立日，不是今天）。
    """
    m = _DATE_PREFIX_RE.match(str(existing or ""))
    prefix = m.group(1) if m else created.strftime("%Y%m%d")
    return _dedupe(f"{prefix}_{_segment(project_name) or 'project'}", taken)


def make_reference_folder_name(title: str, brand: str = "",
                               fallback: str = "",
                               taken: Optional[set] = None) -> str:
    """片庫：`{片名}_{品牌}`（owner 指定格式）。

    片名可能為空（貼網址時 title 還沒抓到）→ 退回 fallback（影片 ID），
    再空 → "reference"。品牌是多選欄，呼叫端挑第一個傳進來；空就只有片名。
    """
    head = _segment(title) or _segment(fallback) or "reference"
    tail = _segment(brand, 40)
    return _dedupe(f"{head}_{tail}" if tail else head, taken)


def _norm(p: str) -> str:
    """比對用正規化：斜線統一 + 去尾斜線 + Windows 大小寫折疊。長度不變
    （normcase 只做 lower 與 / → \\），故切片位移在原字串上仍成立。"""
    return os.path.normcase(str(p or "").replace("/", "\\")).rstrip("\\")


def remap_prefix(path: str, old_prefix: str, new_prefix: str) -> str:
    """path 落在 old_prefix 底下 → 換成 new_prefix；否則原樣回。

    資料夾改名後批次更新 DB 內絕對路徑用。Windows 大小寫/斜線不敏感比對，
    只換前綴、保留原本的尾段（子資料夾結構不動）。
    """
    p = str(path or "")
    if not p or not old_prefix or not new_prefix:
        return p
    np, no = _norm(p), _norm(old_prefix)
    if np == no:
        return new_prefix.rstrip("\\")
    if not np.startswith(no + "\\"):
        return p
    return new_prefix.rstrip("\\") + p[len(str(old_prefix).rstrip("\\/")):]


def rename_dir(old_abs: str, new_abs: str, *,
               exists: Callable[[str], bool] = os.path.exists,
               rename: Callable[[str, str], None] = os.rename) -> tuple:
    """實體改名 → (ok, err)。存在性/改名以 callable 注入，純邏輯可測。

    - 舊夾不存在（還沒建過）→ (True, "")，什麼都不做；呼叫端照樣更新 DB 名稱，
      下次建夾就會用新名。
    - 新舊同路徑 → (True, "") no-op。
    - 新夾已存在 → (False, ...)：呼叫端該先用 taken 集合避開撞名，走到這裡
      表示磁碟上有非本系統建立的同名夾，不覆蓋。
    - PermissionError（檔案被開著）等 OSError → (False, 原因)，**不拋**。
    """
    if not old_abs or not new_abs:
        return False, "路徑為空"
    if _norm(old_abs) == _norm(new_abs):
        return True, ""
    if not exists(old_abs):
        return True, ""
    if exists(new_abs):
        return False, f"目標資料夾已存在：{new_abs}"
    try:
        rename(old_abs, new_abs)
    except OSError as e:
        return False, f"{type(e).__name__}: {e}"
    return True, ""
