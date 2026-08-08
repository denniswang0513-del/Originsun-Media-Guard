"""core/chunked_upload.py — 大檔分塊上傳的落地層（只管拼檔，不管授權/DB/縮圖）。

**為什麼需要**：對外流量走 Cloudflare，單一 HTTP 請求的 body 有 100MB 硬上限
（方案層級的限制，設定改不掉）。超過的檔會在抵達我們的伺服器**之前**就被擋下
—— 後端看不到任何請求、前端只拿到一個沒有內容的失敗。唯一的繞法是前端把檔案
切塊、每塊各自一個請求，後端再拼回來。公司內網直連不經 CF，所以同一個檔在辦
公室傳得上去、在外面傳不上去，這就是這個模組存在的全部理由。

**採 append 模型（不是 parts 模型）**：一路 append 到同一個 `.part`，而不是每塊
存一個檔、最後再合併。理由三個：

  - 合併等於把整份再讀寫一次（500MB 的檔多一趟完整 I/O，而目的地在 NAS）
  - 不需要兩倍磁碟空間
  - 「已經收到幾個 byte」**就是** `.part` 的檔案大小 —— 續傳不必另外記帳，
    也就不存在「記帳與真實檔案不一致」這種故障

代價是同一個檔不能平行傳多塊。上傳的瓶頸在使用者的上行頻寬，平行切塊本來也
不會更快，所以這個代價實際上不存在。

**upload_id 是推導出來的，不是隨機發的**：`upload_id(scope, 瀏覽器鍵, 檔名,
大小, mtime)`。同一個人、同一個檔重新開始上傳 → 落到同一個 `.part` → 續傳是
天然的，連關掉分頁重開都還接得回去。也因為這樣，不需要一個「傳到哪了」的查詢
端點；`begin` 回報 `received` 就夠了。

**staging 放哪**：由呼叫端指定，慣例是收檔根目錄底下的 `.chunk-uploads`。
必須與目的地**同一個 volume**，收尾才能用 `os.replace` 原子改名（跨 volume 的
`os.replace` 是直接 OSError/EXDEV，不會默默退化成複製 —— 別「順手」改成
`shutil.move` 把它變成一趟 500MB 的複製）。目錄名以 `.` 開頭 → 過得了
`core.project_folders.is_hidden_name`，所以資料夾總覽與 reconcile 都掃不到半
成品，不會有人在 NAS 上看到殘檔、也不會被誤匯進資料庫。**那條規則是這個模組
的正確性依賴，不是慣例**：`tests/unit/test_chunked_upload.py` 有一條測試把
`STAGING_DIRNAME` 釘在它上面。
"""
from __future__ import annotations

import hashlib
import os
import re
import time

# 收檔根目錄底下的暫存夾名。`.` 開頭是規格的一部分（見模組 docstring）。
STAGING_DIRNAME = ".chunk-uploads"

# 前端每塊的大小。CF 上限是 100MB，取 8MB 是為了「行動網路上斷一次只需重傳
# 8MB」——不是為了貼近上限。塊小一點，弱網下的實際完成率比較高。
CHUNK_BYTES = 8 * 1024 * 1024

# Cloudflare 對單一請求 body 的硬上限（方案層級，設定改不掉）。**這是整個功能最
# 吃重的一個數字**，所以給它一個名字放在這裡；伺服器用 cf-connecting-ip 判斷這條
# 連線有沒有經過 CF，把它下發給前端（見 media_log._request_body_limit）。
# 「多大才需要分塊」由那個值決定，不是後端訂一個全域門檻 —— 區網直連根本不必分塊。
CF_BODY_LIMIT = 100 * 1024 * 1024

#
# 沒動靜超過這麼久的半成品視為棄置，GC 掉。設一天是因為現場拍攝常見的情境是
# 「收工前傳一半、隔天早上回到訊號好的地方接著傳」。
STALE_SEC = 24 * 3600

_ID_RE = re.compile(r"^[0-9a-f]{40}$")
_PART_EXT = ".part"


class ChunkError(Exception):
    """分塊上傳的預期內錯誤 —— `kind` 由呼叫端對應到 HTTP 狀態碼。

    kind:
      - "offset"    客戶端的位移與伺服器實際收到的不符（`received` 帶回真實值，
                    客戶端據此重新對齊；這是續傳與重試的正常路徑，不是意外）
      - "too-large" 累計超過上限
      - "missing"   `.part` 不存在（過期被 GC / 沒先 begin）
      - "bad-id"    upload_id 格式不合法（路徑參數，見 part_path）

    一個模組只出一種例外 —— 呼叫端就只有一個 except 要寫，也不會有第二套
    對應表要維護（HTTP 狀態碼的對應在 routers/crm/media_log._chunk_http）。
    """

    def __init__(self, kind: str, detail: str, received: int = 0):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.received = received


def upload_id(*parts) -> str:
    """把「同一個人的同一個檔」推導成穩定的 id（見模組 docstring）。

    用 `\\x00` 接是為了不讓欄位邊界可以被內容偽造（檔名裡放 `|` 就能撞到別人
    的 session）。回 40 字元 hex，與 `_ID_RE` 對應。
    """
    raw = "\x00".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()


def staging_dir(root: str) -> str:
    """半成品的暫存夾。

    ⚠ 前提：`root` 是一棵由 `core.project_folders` 的規則列舉的樹 —— 半成品的
      隱形靠的是 `is_hidden_name`（見模組 docstring）。之後若有別的上傳面要用
      這支，而它用自己的方式列舉目錄，那個保證就不成立了。
    """
    return os.path.join(root, STAGING_DIRNAME)


def part_path(root: str, uid: str) -> str:
    """`.part` 的絕對路徑。

    🔴 id 會被拿來組路徑，而它來自 HTTP 路徑參數 —— 白名單比對格式（不是清洗
    非法字元）才擋得住目錄遍歷。
    """
    if not _ID_RE.match(str(uid or "")):
        raise ChunkError("bad-id", "upload_id 格式不合法")
    return os.path.join(staging_dir(root), uid + _PART_EXT)


def received_bytes(root: str, uid: str) -> int:
    """已收到的位元組數；還沒開始 → 0。"""
    try:
        return os.path.getsize(part_path(root, uid))
    except OSError:
        return 0


def begin(root: str, uid: str) -> int:
    """建 staging 夾、回報目前進度（冪等 —— 重開分頁重按上傳走的就是這裡）。

    staging 夾**只在這裡建**。`append_bytes` 不重複建：在 UNC 上
    `makedirs(exist_ok=True)` 不是一個 syscall 而是 exists→mkdir→isdir 三趟，
    每塊都做等於一個 300MB 的檔白花 114 趟往返。id 是推導的 → begin 必然先於
    chunk，真的不見了（被手動刪掉）由 `append_bytes` 補建一次。
    """
    os.makedirs(staging_dir(root), exist_ok=True)
    return received_bytes(root, uid)


_WRITE_BUF = 1 << 20      # 1MB —— ASGI 交過來的片段是 64KB，預設 8KB 緩衝會讓
                          # writelines 對 SMB 發出上百次小寫入


def append_bytes(root: str, uid: str, parts, total: int, *, offset: int,
                 max_bytes: int) -> int:
    """把一塊接到 `.part` 尾端 → 回接完後的總長度。

    `parts` 是**位元組片段的序列**（ASGI 直接交過來的那些），`total` 是它們的
    長度和（呼叫端收的時候本來就要累計，不必再算一次）。刻意不收單一 bytes ——
    `b"".join` 出來的那份 8MB 複本純粹是為了丟給 `write`，而 `writelines` 不需要
    它：省掉每塊約 2ms 的複製，而那 2ms 是花在**事件迴圈上**的（其餘工作都在
    執行緒裡），也讓每個進行中的上傳少佔 8MB。

    `offset` 必須等於伺服器目前實際持有的長度。不相等就整塊丟掉並把真實值回
    給客戶端（`ChunkError("offset")`）—— 這正是「重試時第一次其實已經寫進去了」
    的處理方式：客戶端拿到真實進度後跳過已收的部分，不會寫進重複的資料。

    超過 `max_bytes` → 直接把半成品刪掉（這個檔不可能被接受，留著只是佔空間）。

    長度是**在開起來的檔案 handle 上**問的（`seek` 到尾端），不是先 getsize 再
    open —— 後者是同一個檔在 SMB 上開兩次，每塊白花一趟往返。
    """
    path = part_path(root, uid)
    try:
        fp = open(path, "a+b", buffering=_WRITE_BUF)
    except FileNotFoundError:          # staging 夾被清掉了 → 補建一次再試
        os.makedirs(staging_dir(root), exist_ok=True)
        fp = open(path, "a+b", buffering=_WRITE_BUF)
    try:
        with fp:                       # 先關檔，too-large 那條才刪得掉（Windows）
            fp.seek(0, os.SEEK_END)
            have = fp.tell()
            if offset != have:
                raise ChunkError("offset", f"位移不符（伺服器已收到 {have}）",
                                 received=have)
            if have + total > max_bytes:
                raise ChunkError("too-large",
                                 f"檔案超過 {max_bytes // (1024 * 1024)}MB 上限")
            fp.writelines(parts)
            return have + total
    except ChunkError as e:
        if e.kind == "too-large":      # 這個檔不可能被接受 → 別留著佔空間
            discard(root, uid)
        raise


def finish(root: str, uid: str, dest_path: str, *, expect_bytes: int) -> int:
    """`.part` → 目的地（同 volume 的原子改名）→ 回最終大小。

    先驗長度 —— 前端說 300MB、實際只收到 280MB 卻照樣改名，使用者會拿到一個
    看起來成功的壞檔。寧可回 409 讓它接著傳完。負數 = 不驗（測試用）。
    """
    path = part_path(root, uid)
    size = received_bytes(root, uid)
    if not size and not os.path.isfile(path):
        raise ChunkError("missing", "上傳工作階段不存在或已過期")
    if expect_bytes >= 0 and size != expect_bytes:
        raise ChunkError("offset", f"檔案不完整（已收到 {size} / {expect_bytes}）",
                         received=size)
    os.replace(path, dest_path)
    return size


def discard(root: str, uid: str) -> None:
    """丟掉半成品（放棄上傳 / 超限）。不存在也算成功。"""
    try:
        os.remove(part_path(root, uid))
    except (OSError, ChunkError):
        pass


def gc(root: str, *, older_than: int = STALE_SEC) -> int:
    """清掉沒動靜的半成品 → 清掉幾個。

    使用者關掉分頁就不會有人來收尾，所以一定要有這個；沒有的話 NAS 上會慢慢
    積滿沒人認領的 500MB 檔案。呼叫端節流（見 media_log 的 `_gc_throttled`），
    這裡只管做事。
    """
    cutoff = time.time() - older_than
    removed = 0
    try:
        with os.scandir(staging_dir(root)) as it:
            entries = [e for e in it if e.name.endswith(_PART_EXT)]
    except OSError:
        return 0
    for e in entries:
        try:
            if e.stat().st_mtime < cutoff:
                os.remove(e.path)
                removed += 1
        except OSError:
            continue
    return removed
