"""core/project_folders.py — 資產資料夾的命名與改名跟隨（單一正本）

三個子系統共用同一套規則（2026-08-06 owner 指定）：

| 子系統   | 根目錄設定              | 設定存哪／為什麼            | 每夾命名              | 改名觸發     |
|----------|------------------------|----------------------------|----------------------|-------------|
| 影像紀錄 | `media_log.root`       | **DB**（收檔端在 master 與 NAS 對外容器都有，各存各的會讓照片安靜散落兩處） | `{專案建立日}_{專案名}` | 專案改名     |
| 提案庫   | `proposals.root`       | **DB**（2026-08-07 起 —— 客戶的提案分享頁由 NAS 對外容器 serve，它要**讀**得到這個路徑；settings.json 留作 fallback） | `{專案建立日}_{專案名}` | 專案改名     |
| 結案歸檔 | 沿用 `proposals.root`  | 同上（同一棵樹，不另開根目錄）| `{資產夾}/歸檔/NN_*`  | 跟著資產夾   |
| 片庫     | `reference_archive.dir`| settings.json（封存 runner 是 master-only） | `{片名}_{品牌}`        | 片名/品牌改動 |

判準＝**幾台機器會碰這個資料夾（讀或寫都算）**：兩台以上 → DB（單一真相），
只有 master → settings.json。提案庫就是被「多一台機器要讀」推過門檻的例子 ——
新增讀取端（例如把某頁搬上對外容器）時要回頭看這一格還對不對。

⚠️ `proposals.root` **已經不只服務提案期**：2026-08-08 起結案歸檔
（`routers/crm/archive.py`，範本在 `core/project_archive.py`）也在同一個專案
資產夾底下開 `歸檔/` 子樹。設定 key 的名字是歷史遺留，語意其實是「專案資產
根目錄」。同一棵樹因此有兩條寫入路徑：後台資料夾瀏覽器的通用上傳/開夾/改名，
與歸檔那條的建夾/掃描 —— 兩邊都經過本檔的名稱規則，不要繞過。
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


def validate_folder_name(root: str, raw: str,
                         taken: Optional[set] = None) -> tuple:
    """使用者自己打的資料夾名 → `(名稱, 錯誤訊息)`；不合格時名稱為 ""。

    命名規則的正本住在這裡（「新增資料夾」與「改名」兩種入口、三個子系統
    共用）—— 散在各 router 的話，下次加一條規則（保留字、長度上限）就得去
    每個入口找一遍，錯誤訊息也會各自漂。

    含 `/` `\\` 直接擋，**不默默清成別的名字** —— 使用者會以為改成了他打的
    那個。其餘非法字元照清（那些字他本來也打不出有意義的東西）。
    """
    raw = str(raw or "")
    if "/" in raw or "\\" in raw:
        return "", "資料夾名稱不可含 / 或 \\"
    name = clean_name(raw)
    if not name or not subfolder_path(root, name):
        return "", "資料夾名稱無效"
    if taken and name in taken:
        return "", f"已經有一個叫「{name}」的資料夾了"
    return name, ""


def create_subfolder(parent_abs: str, raw_name, taken: Optional[set] = None) -> tuple:
    """在 parent_abs 底下開一個新子資料夾 → `(名稱, 錯誤訊息)`，失敗時名稱為 ""。

    「往共用磁碟開夾」的規則正本（影像紀錄的新增資料夾、提案資產夾的就地開夾
    都走這支）——理由跟 save_uploads 那組一樣：這是**寫進共用磁碟**這件事的
    性質，不是哪個子系統的性質。散在各 router 就會像先前那樣，同一個「已存在」
    長出兩種錯誤訊息。

    **同步函式，呼叫端請在 `to_thread` 內跑** —— 驗證本身就會 realpath，
    在 UNC 上是網路往返；留在 event loop 上會卡住整個行程的每一個請求。
    路徑用 `os.path.join` 直接組：`validate_folder_name` 已經擋掉分隔字元與
    逃逸，再過一次 `subfolder_path` 是白付兩趟 realpath。
    """
    name, err = validate_folder_name(parent_abs, raw_name, taken)
    if err:
        return "", err
    try:
        # os.mkdir 不是 makedirs —— 只開這一層。makedirs 會把不存在的上層一起
        # 造出來，root 打錯字時會靜靜長出一整條假路徑（人還以為存進去了）。
        # 不先 isdir 問「在不在」：FileExistsError 本身就是答案，少一趟 UNC。
        os.mkdir(os.path.join(parent_abs, name))
    except FileExistsError:
        return "", f"這一層已經有「{name}」了"
    except FileNotFoundError:
        return "", "上層資料夾不存在（路徑設定有誤或 NAS 搆不到）"
    except OSError as e:
        return "", f"建立資料夾失敗：{e}"
    return name, ""


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


def safe_rel_dir(folder_abs: str, rel: str) -> Optional[str]:
    """folder 內相對路徑 → 絕對**目錄**路徑（rel 空 = folder 本身）；逃出或非目錄 → None。
    `safe_rel_path` 的目錄版 —— 逐層瀏覽時每一層都要重驗，不能只驗最外層。"""
    r = str(rel or "").replace("\\", "/").strip().strip("/")
    p = os.path.normpath(os.path.join(folder_abs, r)) if r else folder_abs
    if not within_dir(folder_abs, p) or not os.path.isdir(p):
        return None
    return p


FOLDER_VIEW_CAP = 1000   # 單夾列檔上限（超過標 truncated，避免超大夾逐檔 stat 卡死）


def _visible_dir(name: str) -> bool:
    """列舉時要不要走進這個子資料夾（`.git` / `_tmp` 那類略過）。
    攤平版與逐層版共用同一條規則 —— 分兩份寫，總有一天只有一邊被改到。"""
    return name[:1] not in (".", "_")


def _file_record(rel: str, entry) -> Optional[dict]:
    """檔案的對外形狀（列檔端點共用一份；rel 一律轉成 `/` 給前端）。
    stat 失敗（權限/剛被刪）→ None，呼叫端跳過。"""
    try:
        st = entry.stat()          # 目錄列舉已帶回，不再打一次 SMB
    except OSError:
        return None
    return {"rel": rel.replace("\\", "/"), "filename": entry.name,
            "size_bytes": int(st.st_size), "mtime": st.st_mtime}


def iter_files_rel(folder: str, accept: Optional[Callable[[str], bool]] = None):
    """產出資料夾內檔案的相對路徑（相對 folder；走訪細節見 `_iter_entries`）。
    `accept(filename)` 給定時只產出它認可的（影像紀錄用它篩媒體副檔名）。"""
    for rel, _entry in _iter_entries(folder, accept):
        yield rel


def _iter_entries(folder: str, accept: Optional[Callable[[str], bool]] = None):
    """`(相對路徑, DirEntry)` —— DirEntry 的 stat 是目錄列舉時**順便帶回來的**，
    再自己 os.stat 一次等於每個檔案多一趟 SMB round trip（滿 cap 的資料夾就是
    多 1000 趟，而這是三個子系統共用的熱路徑）。

    真正的惰性產出（不先把整個目錄物化）—— 呼叫端到 cap 就 break 時，5 萬檔的
    資料夾只會列舉到第 1000 筆，cap 才擋得住它該擋的東西。相對路徑在堆疊上帶
    著走，省掉每檔一次 relpath。

    🔴 相對路徑用 **os.sep** 組，與 `os.path.relpath` 同形 —— 消費端會拿它去
    `os.path.join(folder, rel)` 再存進 DB（影像紀錄的 stored_path），分隔符
    混用會讓既有記錄比對不上、同一張照片被當新檔重複匯入。要給前端的 `/`
    形式由 `list_folder_files` 自己轉。
    """
    stack = [(folder, "")]
    while stack:
        cur, prefix = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir():
                            if _visible_dir(e.name):
                                stack.append((e.path, prefix + e.name + os.sep))
                            continue
                    except OSError:
                        continue
                    if accept is None or accept(e.name):
                        yield prefix + e.name, e
        except OSError:
            continue


def list_folder_files(folder_abs: str, *, cap: int = FOLDER_VIEW_CAP,
                      accept: Optional[Callable[[str], bool]] = None) -> tuple:
    """列出資料夾內（含子夾）的檔案 →
    `([{rel, filename, size_bytes, mtime}], truncated)`，依 mtime 新→舊。
    到 cap 就停（大夾防慢）。呼叫端在 to_thread 內跑。"""
    out: list = []
    truncated = False
    for rel, entry in _iter_entries(folder_abs, accept):
        if len(out) >= cap:
            truncated = True
            break
        rec = _file_record(rel, entry)
        if rec:
            out.append(rec)
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out, truncated


def list_folder_level(folder_abs: str, rel: str = "",
                      *, cap: int = FOLDER_VIEW_CAP) -> Optional[tuple]:
    """資料夾內**某一層**的內容 → `(dirs, files, truncated)`；
    rel 逃出資料夾 / 不是目錄 / 資料夾根本不存在 → **None**（呼叫端自己決定
    是 404 還是「還沒建，空的」）。子夾依名稱、檔案依 mtime 新→舊。

    給「可以走進子資料夾」的瀏覽 UI 用（`list_folder_files` 是整棵樹攤平，
    深的資料夾等於一次把整個 NAS 子樹掃完 —— 使用者只想看第一層時很不划算，
    而且攤平後看不出原本的目錄結構）。

    路徑防護**包在裡面**：`rel` 來自前端，忘了先過 `safe_rel_dir` 就是靜默的
    目錄遍歷（不會壞掉、只會多給），所以不留這個給呼叫端自己記得做。
    `rel` 為空時直接用 `folder_abs` —— 呼叫端給的資料夾本來就已經過防護，
    再驗一次是兩趟 realpath，在 UNC 上是實打實的往返。
    """
    r = str(rel or "").replace("\\", "/").strip("/")
    dir_abs = safe_rel_dir(folder_abs, r) if r else folder_abs
    if not dir_abs:
        return None
    prefix = r + "/" if r else ""
    dirs: list = []
    files: list = []
    truncated = False
    try:
        with os.scandir(dir_abs) as it:
            for e in it:
                if len(dirs) + len(files) >= cap:
                    truncated = True
                    break
                try:
                    if e.is_dir():
                        if _visible_dir(e.name):
                            dirs.append({"name": e.name, "rel": prefix + e.name})
                        continue
                except OSError:
                    continue
                rec = _file_record(prefix + e.name, e)
                if rec:
                    files.append(rec)
    except OSError:
        return None
    dirs.sort(key=lambda d: d["name"])
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return dirs, files, truncated


# ── 往共用資料夾寫檔：安全規則 + 落地（任何 any-file 上傳端點共用）──
# 這幾條的理由是「寫進**共用磁碟**」這件事的性質，不是哪個子系統的性質，
# 所以跟 clean_filename / dedupe 住在一起 —— 下一個 any-file 落地點不必
# 重新決定一次。

# ⚠️ 沒有預設上限 —— `save_uploads` 的 max_bytes 是必填。有預設值的話，下一個
# 接進來的子系統忘了覆寫就沉默繼承別人的業務判斷（memory 記過這族陷阱：
# 共用函式有預設值 + 呼叫端不送 = 該值被預設洗掉）。
UPLOAD_CHUNK = 1024 * 1024                # 分塊寫入，大檔不整包進記憶體
# 擋掉可執行檔（NAS 是共用磁碟，別讓它變成散播點）；其餘一律放行 ——
# 企劃檔格式太雜（.key/.indd/.aep/.srt…），白名單只會擋到自己人。
BLOCKED_UPLOAD_EXTS = {
    ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".vbs", ".js",
    ".jar", ".dll", ".lnk", ".reg", ".hta", ".cpl",
}


def _stream_to_disk(src, dest: str, max_bytes: int) -> int:
    """分塊寫入 → 位元組數；超過上限刪半成品回 -1（呼叫端在 to_thread 內跑）。
    整包 `await f.read()` 會讓一次多檔上傳把數百 MB 壓在行程記憶體裡，而且
    超限也是**讀完才發現**。"""
    total = 0
    with open(dest, "wb") as fp:
        while True:
            chunk = src.read(UPLOAD_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                fp.close()
                try:
                    os.remove(dest)
                except OSError:
                    pass
                return -1
            fp.write(chunk)
    return total


def safe_sub_dirs(rel_path: str) -> Optional[list]:
    """使用者拖進來的相對路徑 → 乾淨的**目錄段**清單（不含最後的檔名）。
    任何一段是 `.` / `..` / 清完為空 → None（整筆拒收，不「盡量修好」）。

    🔴 這條路徑來自瀏覽器的拖放事件，**是敵意輸入**。清洗必須逐段做：
    只檢查整串有沒有 `..` 擋不住 `a/..%2f..`、也擋不住 Windows 的 `C:` 這種
    磁碟機前綴。逐段 clean_name + 明確拒收特殊段，才是可以講清楚的規則。
    """
    parts = str(rel_path or "").replace("\\", "/").split("/")[:-1]   # 去掉檔名那段
    out = []
    for seg in parts:
        seg = seg.strip()
        if not seg:
            continue                       # 連續斜線 / 開頭斜線 → 忽略空段
        if seg in (".", ".."):
            return None
        cleaned = clean_name(seg)[:_MAX_SEGMENT_LEN].strip()
        if not cleaned or cleaned in (".", ".."):
            return None                    # 清完只剩非法字元 → 這筆不要
        out.append(cleaned)
    return out


async def save_uploads(folder_abs: str, files, *, max_bytes: int,
                       rel_paths=None) -> tuple:
    """把上傳檔案落地到 folder_abs → `(saved, skipped)`。

    檔名保留原樣（清洗非法字元）、撞名補 -2、可執行檔與超大檔進 skipped
    並附人看得懂的理由。

    `rel_paths[i]`（拖整個資料夾進來時前端會送）= 該檔在來源資料夾裡的相對
    路徑，含被拖進來的那層資料夾名 → 這裡照著重建目錄結構。每一段都過
    `safe_sub_dirs`，且最終路徑會再驗一次落在 folder_abs 內（雙保險：一層是
    規則、一層是實際 realpath 比對）。不給就全部平放，行為與從前相同。

    撞名是**逐目錄**判斷的 —— 全域一份 taken 會讓 `a/圖.jpg` 與 `b/圖.jpg`
    其中一個平白變成 `圖-2.jpg`。

    範圍：目前的消費端是提案資產夾的兩個上傳入口（專案的與任一資料夾的）。
    影像紀錄**刻意沒接** —— 它走的是媒體副檔名白名單 + 時間戳前綴去重，
    是另一套政策，硬合併只會讓兩邊都變難懂。
    """
    over = f"超過 {max_bytes // (1024 * 1024)}MB 上限"
    saved, skipped = [], []
    taken_by_dir: dict = {}                # 目錄 → 已佔用檔名（逐目錄去重）
    made_dirs = set()

    async def _dest_dir(rel_path: str):
        """→ (絕對目錄, 顯示用前綴)；不合法或建不出來 → (None, reason)。"""
        segs = safe_sub_dirs(rel_path) if rel_path else []
        if segs is None:
            return None, "路徑不合法"
        if not segs:
            return folder_abs, ""
        target = os.path.join(folder_abs, *segs)
        if not within_dir(folder_abs, target):     # 第二道：實際路徑比對
            return None, "路徑逃出資料夾"
        if target not in made_dirs:
            try:
                await asyncio.to_thread(os.makedirs, target, exist_ok=True)
            except OSError as e:
                return None, f"無法建立子資料夾（{type(e).__name__}）"
            made_dirs.add(target)
        return target, "/".join(segs) + "/"

    for i, f in enumerate(files):
        rel_path = (rel_paths[i] if rel_paths and i < len(rel_paths) else "") or ""
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext in BLOCKED_UPLOAD_EXTS:
            skipped.append({"filename": f.filename, "reason": f"不允許的檔案類型（{ext}）"})
            continue
        # 大小事前就知道（multipart 解析時已算好）→ 超限的一個位元組都別寫進
        # NAS，不然是先傳 max_bytes 上去再刪掉
        if (getattr(f, "size", None) or 0) > max_bytes:
            skipped.append({"filename": f.filename, "reason": over})
            continue
        dest, prefix = await _dest_dir(rel_path)
        if dest is None:
            skipped.append({"filename": f.filename, "reason": prefix})
            continue
        if dest not in taken_by_dir:
            try:
                taken_by_dir[dest] = set(await asyncio.to_thread(os.listdir, dest))
            except OSError:
                taken_by_dir[dest] = set()
        taken = taken_by_dir[dest]
        name = dedupe(clean_filename(f.filename or ""), taken, keep_ext=True)
        written = await asyncio.to_thread(
            _stream_to_disk, f.file, os.path.join(dest, name), max_bytes)
        if written < 0:                 # size 拿不到時的第二道防線
            skipped.append({"filename": f.filename, "reason": over})
            continue
        taken.add(name)
        saved.append(prefix + name)
    return saved, skipped


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
