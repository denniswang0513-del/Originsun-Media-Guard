"""core/project_folders.py — 資產資料夾的命名與改名跟隨（單一正本）

三個子系統共用同一套規則（2026-08-06 owner 指定）：

| 子系統   | 根目錄設定              | 設定存哪／為什麼            | 每夾命名              | 改名觸發     |
|----------|------------------------|----------------------------|----------------------|-------------|
| 影像紀錄 | `media_log.root`       | **DB**（收檔端在 master 與 NAS 對外容器都有，各存各的會讓照片安靜散落兩處） | `{專案建立日}_{專案名}` | 專案改名     |
| 提案庫   | `proposals.root`       | settings.json（只有 master 會寫） | `{專案建立日}_{專案名}` | 專案改名     |
| 片庫     | `reference_archive.dir`| settings.json（封存 runner 是 master-only） | `{片名}_{品牌}`        | 片名/品牌改動 |

判準＝**幾台機器會寫這個資料夾**：兩台以上 → DB（單一真相），只有 master → settings.json。
日期一律取**專案建立日**（不是當天）—— 改名時資料夾不會跳到今天。

🔴 **改名的鐵則：實體 rename 與 DB 內絕對路徑的更新必須同進退。**
影像紀錄的 `ProjectMediaFile.stored_path`、片庫的 `PreprodReference.archive_path`
存的都是絕對路徑；只 rename 不更新 DB → 舊記錄指向不存在的路徑，而新路徑的
檔案會被資料夾↔DB 同步當成「新檔」再匯入一遍（同一張照片兩筆記錄）。
**呼叫端一律走 `rename_and_remap`**（順序、savepoint、補償都在裡面）——
不要自己組 `rename_dir` + `remap_prefix`，那樣會少掉補償這一步。

改名失敗（檔案被開著 → PermissionError）不可以讓來源改名整個失敗：
呼叫端拿到 err 就保留舊資料夾名 + 當 warning 回前端，**名稱本身照樣改成功**。
資料夾名與顯示名暫時不同步，下次改名會再試一次。

純函式（clean_name / make_* / remap_prefix / dedupe）不碰磁碟，單元測試對象見
tests/unit/test_project_folders.py。
"""
from __future__ import annotations

import asyncio
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


def clean_filename(name: str) -> str:
    """上傳原檔名 → 合法檔名：先去路徑成分；清完全空 → "upload"
    （副檔名保留 —— "." 不在非法清單）。"""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    return clean_name(base) or "upload"


def _segment(s: str, limit: int = _MAX_SEGMENT_LEN) -> str:
    """清洗 + 截斷單一名稱片段（片名/專案名可能很長，路徑有 260 上限）。"""
    return clean_name(s)[:limit].strip()


def dedupe(base: str, taken: Optional[set] = None, keep_ext: bool = False) -> str:
    """撞名補 -2、-3…（taken = 同一層已被佔用的名字集合）。

    `keep_ext=True` 給**檔名**用 —— 補號插在副檔名前面（`稿.pdf` → `稿-2.pdf`）。
    副檔名在函式內自己拆，呼叫端不必傳（傳進來就得跟 base 保持大小寫一致，
    `稿.PDF` 配 `.pdf` 會拆不出來、補成 `稿.PDF-2.pdf`）。
    """
    stem, ext = os.path.splitext(base) if keep_ext else (base, "")
    name, i = base, 2
    while name in (taken or set()):
        name = f"{stem}-{i}{ext}"
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
    return dedupe(f"{prefix}_{_segment(project_name) or 'project'}", taken)


def make_reference_folder_name(title: str, brand: str = "",
                               fallback: str = "",
                               taken: Optional[set] = None) -> str:
    """片庫：`{片名}_{品牌}`（owner 指定格式）。

    片名可能為空（貼網址時 title 還沒抓到）→ 退回 fallback（影片 ID），
    再空 → "reference"。品牌是多選欄，呼叫端挑第一個傳進來；空就只有片名。
    """
    head = _segment(title) or _segment(fallback) or "reference"
    tail = _segment(brand, 40)
    return dedupe(f"{head}_{tail}" if tail else head, taken)


async def taken_names(session, name_col, id_col, exclude_id: str = "") -> set:
    """同一個 root 底下已被佔用的資料夾名（撞名補 -2 用）。

    給**把資料夾名存成欄位**的子系統（影像紀錄、提案庫）；片庫的資料夾名是
    從 `archive_path` 的 dirname 反推、沒有欄位，自己有一份反推版。
    改名時傳 `exclude_id` 排除自己（否則會跟自己撞名、平白補號）。
    """
    from sqlalchemy import select
    q = select(name_col).where(name_col.isnot(None))
    if exclude_id:
        q = q.where(id_col != exclude_id)
    return {n for n in (await session.execute(q)).scalars() if n}


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
    # `no + "\\"` 的邊界檢查不可省 —— 否則 `..._案` 會吃到 `..._案外案`
    if np != no and not np.startswith(no + "\\"):
        return p
    return new_prefix.rstrip("\\") + p.rstrip("\\/")[len(old_prefix.rstrip("\\/")):]


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


# ── 資料夾內容瀏覽：路徑防護 + 列檔（三個子系統共用）────────────
# folder / rel 都來自前端 query → 一律經這裡的防護再開檔，擋 `..` 逃逸與
# 絕對路徑注入。正本原本在 routers/crm/media_log.py，提案資產夾是第三個
# 使用者時搬來這裡（規則只留一份，測試見 tests/unit/test_project_folders.py）。

def within_dir(base: str, target: str) -> bool:
    """target 是否落在 base 目錄內（或即為 base）。realpath 解 symlink/junction
    後 normcase+前綴比對，擋 .. 逃逸；Windows 大小寫/斜線不敏感。"""
    try:
        b = os.path.normcase(os.path.realpath(base))
        t = os.path.normcase(os.path.realpath(target))
    except OSError:
        return False
    return t == b or t.startswith(b + os.sep)


def subfolder_path(root: str, name: str) -> Optional[str]:
    """root 底下**單層**子資料夾的絕對路徑（純驗證、不檢查是否存在）；名稱含路徑
    分隔 / . / .. 或逃出 root → None。瀏覽（需已存在）與新增資料夾（需尚不存在）
    共用，各自再加存在性判斷。"""
    name = str(name or "").strip()
    if not root or not name or name in (".", "..") or "/" in name or "\\" in name:
        return None
    p = os.path.join(root, name)
    return p if within_dir(root, p) else None


def safe_subfolder(root: str, folder: str) -> Optional[str]:
    """root 底下**已存在**的直接子資料夾絕對路徑；不合法或非目錄 → None。"""
    p = subfolder_path(root, folder)
    return p if (p and os.path.isdir(p)) else None


def safe_rel_path(folder_abs: str, rel: str) -> Optional[str]:
    """folder 內相對路徑 → 絕對檔案路徑；逃出 folder（.. / 絕對路徑）或非檔案 → None。"""
    r = str(rel or "").replace("\\", "/").strip().lstrip("/")
    if not r:
        return None
    p = os.path.normpath(os.path.join(folder_abs, r))
    if not within_dir(folder_abs, p) or not os.path.isfile(p):
        return None
    return p


FOLDER_VIEW_CAP = 1000   # 單夾列檔上限（超過標 truncated，避免超大夾逐檔 stat 卡死）


def iter_files_rel(folder: str, accept: Optional[Callable[[str], bool]] = None):
    """os.walk 產出資料夾內檔案的相對路徑（相對 folder）。剪掉 ./_ 開頭的子夾。
    `accept(filename)` 給定時只產出它認可的（影像紀錄用它篩媒體副檔名）。"""
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d[:1] not in (".", "_")]
        for fn in filenames:
            if accept is None or accept(fn):
                yield os.path.relpath(os.path.join(dirpath, fn), folder)


def list_folder_files(folder_abs: str, *, cap: int = FOLDER_VIEW_CAP,
                      accept: Optional[Callable[[str], bool]] = None) -> tuple:
    """列出資料夾內（含子夾）的檔案 →
    `([{rel, filename, size_bytes, mtime}], truncated)`，依 mtime 新→舊。
    到 cap 就停（大夾防慢）。呼叫端在 to_thread 內跑。"""
    import stat as _stat
    out: list = []
    truncated = False
    for rel in iter_files_rel(folder_abs, accept):
        if len(out) >= cap:
            truncated = True
            break
        try:
            st = os.stat(os.path.join(folder_abs, rel))
        except OSError:
            continue
        if not _stat.S_ISREG(st.st_mode):
            continue
        out.append({
            "rel": rel.replace("\\", "/"),
            "filename": os.path.basename(rel),
            "size_bytes": int(st.st_size),
            "mtime": st.st_mtime,
        })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out, truncated


async def rename_and_remap(session, old_dir: str, new_dir: str, *, remap) -> tuple:
    """改名的**單一程序**（三個資產子系統共用）→ `(changed, err)`。

    `old_dir` / `new_dir` 用呼叫端的視角（canonical UNC 或本機皆可）—— 開檔前
    自己翻成本機視角。**`remap` 收到的是同一組原視角路徑**，要寫進 DB 前自己
    決定要不要 `to_canonical_path`（影像紀錄/提案庫傳本機視角所以要轉，片庫
    傳的本來就是 canonical 所以不轉）。

    🔴 不變式「磁碟與 DB 同進退」靠**順序 + savepoint + 補償**三層保證：
    1. rename 失敗 → 什麼都不動，回原因。
    2. remap 包在 `begin_nested()` savepoint 裡 —— DB 例外會只回滾這一段，
       呼叫端的 session 仍可用（沒有 savepoint 的話，一個 remap 例外會讓外層
       交易進入 failed 狀態，後面的子系統與最終 commit 全部連鎖失敗）。
    3. remap 失敗 → **把資料夾改回舊名**。補償**本身**也失敗才是真的不一致，
       那種情況回一個講實話、可辨識的訊息（呼叫端會當 warning 呈現）。
    """
    from core.drive_map import to_local_path
    ok, err = await asyncio.to_thread(
        rename_dir, to_local_path(old_dir), to_local_path(new_dir))
    if not ok:
        return False, f"{err}；資料夾維持舊名，檔案未受影響"
    try:
        async with session.begin_nested():
            await remap(old_dir, new_dir)
    except Exception as e:
        undone, undo_err = await asyncio.to_thread(
            rename_dir, to_local_path(new_dir), to_local_path(old_dir))
        if not undone:
            return False, (f"路徑同步失敗（{type(e).__name__}: {e}）"
                           f"且資料夾無法改回舊名（{undo_err}）—— "
                           f"資料夾現在叫「{os.path.basename(new_dir)}」但紀錄仍指舊路徑，需人工處理")
        return False, f"路徑同步失敗（{type(e).__name__}: {e}）；資料夾已改回舊名"
    return True, ""
